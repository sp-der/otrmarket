"""Pre-live audit regression tests for the broker execution gateway.

Every test here drives the server-side command queue with simulated broker
state. Nothing in this file can reach NinjaTrader or a brokerage.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.execution.live import api as execution_api
from src.execution.live.config import ExecutionConfig
from src.execution.live.gateway import ExecutionGateway
from src.execution.live.models import ExecutionMode
from src.execution.live.safety import bridge_dispatch_ready, evaluate_intent
from src.execution.live.sizing import build_execution_intent
from src.execution.live.store import (
    cancel_directives,
    cancel_pending_commands,
    daily_execution_ledger,
    enqueue_intent,
    ensure_schema,
    get_command,
    get_state,
    poll_commands,
    record_bridge_snapshot,
    record_event,
    set_state,
    trading_day_start,
)


UTC = timezone.utc
# 14:00 UTC = 10:00 New York (EDT): inside one CME trading day.
NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
CONTRACT = "GC 12-26"


def gold_setup(ident="g1", *, direction="bullish", entry=4300.0, stop=4295.0, target=4315.0, timeframe="1m"):
    return SimpleNamespace(
        setup_id=ident,
        symbol="GC",
        timeframe=timeframe,
        direction=direction,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        risk_reward=abs(target - entry) / abs(entry - stop),
        metadata={"strategy": "ICT_CONFLUENCE", "a_plus_context": {"quality_grade": "A+", "quality_score": 92}},
    )


def sim_config(**overrides):
    values = dict(
        mode=ExecutionMode.SIM_BRIDGE,
        armed=True,
        account="Sim101",
        max_micros=40,
        max_risk_dollars=250.0,
        max_daily_loss_dollars=750.0,
        max_trades_per_day=0,  # production default: unlimited
        max_active_commands=1,
        max_data_age_seconds=10,
    )
    values.update(overrides)
    return ExecutionConfig(**values)


class _GatewayHarness(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        ensure_schema(self.connection)
        self.connection.execute(
            "CREATE TABLE market_quotes (id INTEGER PRIMARY KEY, symbol TEXT, source TEXT, received_at TEXT, ingested_at TEXT)"
        )
        self.fresh_quote(NOW)

    def tearDown(self):
        self.connection.close()

    def fresh_quote(self, at, symbol="GC"):
        self.connection.execute(
            "INSERT INTO market_quotes(symbol, source, received_at, ingested_at) VALUES (?, ?, ?, ?)",
            (symbol, f"ninjatrader:{CONTRACT}", at.isoformat(), at.isoformat()),
        )

    def reconcile(self, positions=None, orders=None, now=NOW):
        return record_bridge_snapshot(
            self.connection,
            {"bridge_id": "test", "timestamp": now.isoformat(), "account": "Sim101", "positions": positions or [], "orders": orders or []},
            configured_account="Sim101",
            now=now,
        )

    def intent(self, ident="g1", config=None, now=NOW, **kwargs):
        return build_execution_intent(
            gold_setup(ident, **kwargs),
            risk_dollars=250.0,
            config=config or sim_config(),
            signal_contract=CONTRACT,
            now=now,
            entry_lifetime_seconds=12 * 60,
        )

    def event(self, command_id, event_type, ident, now=NOW, **fields):
        payload = {"event_id": ident, "command_id": command_id, "event_type": event_type, "occurred_at": now.isoformat()}
        payload.update(fields)
        return record_event(self.connection, payload, now=now)

    def close_trade(self, intent, *, entry, exit_price, now=NOW, prefix=""):
        enqueue_intent(self.connection, intent)
        self.event(intent.command_id, "FILLED", f"{prefix}fill-{intent.command_id}", now, filled_quantity=intent.quantity, price=entry)
        self.event(intent.command_id, "CLOSED", f"{prefix}close-{intent.command_id}", now, filled_quantity=intent.quantity, price=exit_price)


class ValidSetupAndSizingTests(_GatewayHarness):
    def test_valid_gold_setup_produces_correct_order(self):
        self.reconcile()
        config = sim_config()
        intent = self.intent(config=config)
        # MGC = $10/point. 5-point stop = $50/contract -> floor(250/50) = 5.
        self.assertEqual(intent.execution_contract, "MGC 12-26")
        self.assertEqual(intent.quantity, 5)
        self.assertEqual(intent.per_contract_risk, 50.0)
        self.assertEqual(intent.risk_dollars, 250.0)
        self.assertEqual(intent.side, "BUY")
        self.assertIsNotNone(intent.metadata["entry_expires_at"])
        decision = evaluate_intent(self.connection, intent, config, now=NOW)
        self.assertTrue(decision.allowed, decision.reason)

    def test_position_size_is_floored_never_rounded_up(self):
        # 4.3-point stop = $43/contract; 250/43 = 5.81 -> 5 contracts ($215).
        intent = self.intent(stop=4295.7)
        self.assertEqual(intent.quantity, 5)
        self.assertLessEqual(intent.risk_dollars, 250.0)

    def test_risk_that_cannot_fund_one_contract_is_rejected(self):
        with self.assertRaises(ValueError):
            build_execution_intent(gold_setup(stop=4270.0), risk_dollars=250.0, config=sim_config(), signal_contract=CONTRACT, now=NOW)

    def test_risk_above_cap_is_blocked(self):
        self.reconcile()
        config = sim_config()
        oversized = replace(self.intent(config=config), risk_dollars=300.0, quantity=6)
        self.assertEqual(evaluate_intent(self.connection, oversized, config, now=NOW).code, "RISK_LIMIT")

    def test_invalid_stop_is_blocked(self):
        self.reconcile()
        config = sim_config()
        inverted = replace(self.intent(config=config), stop_price=4305.0)
        self.assertEqual(evaluate_intent(self.connection, inverted, config, now=NOW).code, "INVALID_GEOMETRY")

    def test_incorrect_contract_multiplier_is_blocked(self):
        self.reconcile()
        config = sim_config()
        # Full-size GC multiplier ($100/pt) leaking into a micro order.
        wrong = replace(self.intent(config=config), per_contract_risk=500.0, quantity=1, risk_dollars=50.0)
        self.assertEqual(evaluate_intent(self.connection, wrong, config, now=NOW).code, "CONTRACT_MULTIPLIER_MISMATCH")

    def test_unresolved_contract_expiry_is_blocked(self):
        self.reconcile()
        config = sim_config()
        intent = build_execution_intent(gold_setup(), risk_dollars=250.0, config=config, signal_contract="", now=NOW)
        self.assertEqual(intent.execution_contract, "MGC")
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=NOW).code, "CONTRACT_UNRESOLVED")

    def test_non_gold_symbol_cannot_be_transmitted(self):
        self.reconcile()
        config = sim_config()
        nq = SimpleNamespace(**{**vars(gold_setup("nq1")), "symbol": "NQ", "entry_price": 20000.0, "stop_price": 19990.0, "target_price": 20030.0})
        intent = build_execution_intent(nq, risk_dollars=250.0, config=config, signal_contract="NQ 12-26", now=NOW)
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=NOW).code, "SYMBOL_NOT_ALLOWED")

    def test_empty_allowlist_env_blocks_everything(self):
        with mock.patch.dict(os.environ, {"OTR_EXECUTION_ALLOWED_SYMBOLS": " "}):
            self.assertEqual(ExecutionConfig.from_env().allowed_symbols, ())


class ModeAndKillSwitchTests(_GatewayHarness):
    def test_execution_defaults_are_paper_and_disarmed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            config = ExecutionConfig.from_env()
        self.assertEqual(config.mode, ExecutionMode.PAPER)
        self.assertFalse(config.armed)
        self.assertFalse(config.live_allowed)
        self.assertFalse(config.certified)

    def test_simulation_paper_mode_never_queues(self):
        config = ExecutionConfig()  # PAPER default
        gateway = ExecutionGateway(config)
        result = gateway.handle_approved_setup(gold_setup(), risk_dollars=250.0, connection=self.connection)
        self.assertEqual(result["code"], "PAPER_ONLY")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM execution_commands").fetchone()[0], 0)
        self.assertFalse(bridge_dispatch_ready(self.connection, config, now=NOW).allowed)

    def test_kill_switch_blocks_entries_cancels_undelivered_and_working_entries(self):
        self.reconcile()
        pending = self.intent("pending")
        working = self.intent("working")
        filled = self.intent("filled")
        for item in (pending, working, filled):
            enqueue_intent(self.connection, item)
        self.event(working.command_id, "WORKING", "w1")
        self.event(filled.command_id, "FILLED", "f1", filled_quantity=filled.quantity, price=4300.0)

        set_state(self.connection, "kill_switch", True, NOW)
        self.assertEqual(cancel_pending_commands(self.connection, "kill switch", NOW), 1)
        self.assertEqual(get_command(self.connection, pending.command_id)["status"], "CANCELLED")

        directives = cancel_directives(self.connection, account="Sim101", mode="SIM_BRIDGE", kill_switch=True, now=NOW)
        self.assertEqual([d["command_id"] for d in directives], [working.command_id])
        # Filled exposure keeps its protective bracket: never in cancel list.
        self.assertNotIn(filled.command_id, [d["command_id"] for d in directives])

        config = sim_config(max_active_commands=10, max_trades_per_day=10, max_daily_loss_dollars=10_000)
        self.assertEqual(evaluate_intent(self.connection, self.intent("new"), config, now=NOW).code, "KILL_SWITCH")

    def test_kill_switch_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "otr.db"
            first = sqlite3.connect(path)
            set_state(first, "kill_switch", True, NOW)
            first.close()
            second = sqlite3.connect(path)
            try:
                self.assertTrue(get_state(second, "kill_switch", False)[0])
                self.assertEqual(bridge_dispatch_ready(second, sim_config(), now=NOW).code, "KILL_SWITCH")
            finally:
                second.close()


class VerifyModeInterlockTests(_GatewayHarness):
    def test_verify_mode_can_never_transmit(self):
        self.reconcile()
        with mock.patch.dict(os.environ, {"OTR_TRADING_MODE": "VERIFY"}):
            self.assertEqual(evaluate_intent(self.connection, self.intent(), sim_config(), now=NOW).code, "VERIFY_MODE_FORBIDDEN")
            self.assertEqual(bridge_dispatch_ready(self.connection, sim_config(), now=NOW).code, "VERIFY_MODE_FORBIDDEN")


class EvaluationTradingDayTests(unittest.TestCase):
    def test_paper_daily_lock_follows_the_1800_et_cme_day(self):
        from src.risk.evaluation import EvaluationRiskGuard
        from tests.test_operation45 import config as eval_config, db, insert_trade

        con = db()
        # Loss closes 19:32 ET Oct 13 -> CME trading day of Oct 14.
        loss_day = datetime(2026, 10, 13, 23, 30, tzinfo=UTC)
        insert_trade(con, "evening-loss", "CLOSED", day=loss_day, result="LOSS", risk=400, pnl=-400)
        guard = EvaluationRiskGuard(eval_config())
        after_midnight = datetime(2026, 10, 14, 4, 30, tzinfo=UTC)  # 00:30 ET, same CME day
        self.assertEqual(guard.snapshot(con, after_midnight)["status"], "DAILY_LOCK")
        next_day = datetime(2026, 10, 14, 22, 30, tzinfo=UTC)  # 18:30 ET, new CME day
        self.assertEqual(guard.snapshot(con, next_day)["status"], "ACTIVE")


class DataFreshnessTests(_GatewayHarness):
    def test_stale_market_data_blocks_new_entries(self):
        self.reconcile(now=NOW + timedelta(seconds=30))
        config = sim_config()
        later = NOW + timedelta(seconds=30)
        intent = self.intent(now=later)
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=later).code, "MARKET_DATA_STALE")

    def test_backlog_replay_with_old_exchange_time_blocks_entries(self):
        self.reconcile()
        # Freshly ingested (server clock) but carrying 20-minute-old exchange
        # timestamps: an engine restart replaying its backlog in LIVE.
        self.connection.execute("DELETE FROM market_quotes")
        for offset in (21, 20):
            self.connection.execute(
                "INSERT INTO market_quotes(symbol, source, received_at, ingested_at) VALUES ('GC', 'ninjatrader:GC 12-26', ?, ?)",
                ((NOW - timedelta(minutes=offset)).isoformat(), NOW.isoformat()),
            )
        self.assertEqual(evaluate_intent(self.connection, self.intent(), sim_config(), now=NOW).code, "MARKET_DATA_NOT_CURRENT")

    def test_feed_running_backwards_blocks_entries(self):
        self.reconcile()
        self.connection.execute(
            "INSERT INTO market_quotes(symbol, source, received_at, ingested_at) VALUES ('GC', 'ninjatrader:GC 12-26', ?, ?)",
            ((NOW - timedelta(minutes=20)).isoformat(), NOW.isoformat()),
        )
        self.assertEqual(evaluate_intent(self.connection, self.intent(), sim_config(), now=NOW).code, "MARKET_DATA_REORDERED")

    def test_ninjatrader_seven_digit_timestamp_is_accepted(self):
        self.reconcile()
        self.connection.execute(
            "INSERT INTO market_quotes(symbol, source, received_at, ingested_at) VALUES ('GC', 'ninjatrader:GC 12-26', ?, ?)",
            ("2026-10-13T13:59:59.1234567Z", NOW.isoformat()),
        )
        self.assertTrue(evaluate_intent(self.connection, self.intent(), sim_config(), now=NOW).allowed)

    def test_missing_market_data_blocks_new_entries(self):
        self.connection.execute("DELETE FROM market_quotes")
        self.reconcile()
        self.assertEqual(evaluate_intent(self.connection, self.intent(), sim_config(), now=NOW).code, "MARKET_DATA_STALE")


class ExposureAndDailyLimitTests(_GatewayHarness):
    def test_duplicate_signal_creates_one_command_and_second_signal_is_blocked(self):
        self.reconcile()
        config = sim_config()
        first = self.intent("dup")
        enqueue_intent(self.connection, first)
        enqueue_intent(self.connection, first)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM execution_commands").fetchone()[0], 1)
        decision = evaluate_intent(self.connection, self.intent("other"), config, now=NOW)
        self.assertEqual(decision.code, "EXPOSURE_LIMIT")

    def test_two_simultaneous_signals_cannot_both_queue(self):
        # The gateway evaluates against the real wall clock.
        wall = datetime.now(UTC)
        self.connection.execute("DELETE FROM market_quotes")
        self.fresh_quote(wall)
        self.reconcile(now=wall)
        gateway = ExecutionGateway(sim_config())
        a = gateway.handle_approved_setup(gold_setup("sa"), risk_dollars=250.0, connection=self.connection)
        b = gateway.handle_approved_setup(gold_setup("sb", direction="bearish", entry=4300.0, stop=4305.0, target=4285.0), risk_dollars=250.0, connection=self.connection)
        self.assertEqual(a["code"], "QUEUED", a)
        self.assertEqual(b["code"], "EXPOSURE_LIMIT", b)

    def test_daily_loss_limit_locks_trading(self):
        self.reconcile()
        config = sim_config(max_trades_per_day=10)
        # Two full stop-outs: 5 micros x 5pt x $10 = $250 each -> $500 used.
        self.close_trade(self.intent("l1"), entry=4300.0, exit_price=4295.0)
        self.close_trade(self.intent("l2"), entry=4300.0, exit_price=4295.0)
        ledger = daily_execution_ledger(self.connection, account="Sim101", now=NOW)
        self.assertEqual(ledger["realized_pnl"], -500.0)
        # $500 used + $250 new risk = $750: allowed (not above the limit).
        self.assertTrue(evaluate_intent(self.connection, self.intent("l3"), config, now=NOW).allowed)
        self.close_trade(self.intent("l3"), entry=4300.0, exit_price=4295.0)
        decision = evaluate_intent(self.connection, self.intent("l4"), config, now=NOW)
        self.assertEqual(decision.code, "DAILY_LOSS_LIMIT")

    def test_next_stop_out_may_not_breach_daily_limit(self):
        self.reconcile()
        config = sim_config(max_trades_per_day=10, max_daily_loss_dollars=500.0)
        self.close_trade(self.intent("p1"), entry=4300.0, exit_price=4294.0)  # -$300; +$250 next risk > $500
        decision = evaluate_intent(self.connection, self.intent("p2"), config, now=NOW)
        self.assertEqual(decision.code, "DAILY_LOSS_LIMIT")

    def test_profits_do_not_enlarge_the_daily_loss_budget(self):
        self.reconcile()
        config = sim_config(max_trades_per_day=10, max_daily_loss_dollars=250.0)
        self.close_trade(self.intent("w1"), entry=4300.0, exit_price=4315.0)  # +$750
        ledger = daily_execution_ledger(self.connection, account="Sim101", now=NOW)
        self.assertEqual(ledger["loss_used"], 0.0)
        self.assertTrue(evaluate_intent(self.connection, self.intent("w2"), config, now=NOW).allowed)

    def test_default_config_never_trade_count_blocks(self):
        self.reconcile()
        config = sim_config(max_daily_loss_dollars=10_000)
        for ident in ("t1", "t2", "t3", "t4"):
            self.close_trade(self.intent(ident), entry=4300.0, exit_price=4315.0)
        self.assertTrue(evaluate_intent(self.connection, self.intent("t5"), config, now=NOW).allowed)

    def test_lockout_is_reconstructed_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "otr.db"
            first = sqlite3.connect(path)
            ensure_schema(first)
            for ident in ("r1", "r2", "r3"):
                item = self.intent(ident)
                enqueue_intent(first, item)
                record_event(first, {"event_id": f"f-{ident}", "command_id": item.command_id, "event_type": "FILLED", "filled_quantity": item.quantity, "price": 4300.0, "occurred_at": NOW.isoformat()}, now=NOW)
                record_event(first, {"event_id": f"c-{ident}", "command_id": item.command_id, "event_type": "CLOSED", "price": 4295.0, "occurred_at": NOW.isoformat()}, now=NOW)
            first.close()

            second = sqlite3.connect(path)
            try:
                ledger = daily_execution_ledger(second, account="Sim101", now=NOW + timedelta(minutes=5))
                self.assertEqual(ledger["loss_used"], 750.0)
                self.assertEqual(ledger["trades"], 3)
            finally:
                second.close()

    def test_trading_day_rolls_at_1800_new_york(self):
        before_roll = datetime(2026, 10, 13, 21, 59, tzinfo=UTC)  # 17:59 EDT
        after_roll = datetime(2026, 10, 13, 22, 1, tzinfo=UTC)  # 18:01 EDT
        self.assertEqual(trading_day_start(before_roll), datetime(2026, 10, 12, 22, 0, tzinfo=UTC))
        self.assertEqual(trading_day_start(after_roll), datetime(2026, 10, 13, 22, 0, tzinfo=UTC))
        self.close_trade(self.intent("d1", now=before_roll), entry=4300.0, exit_price=4295.0, now=before_roll)
        self.assertEqual(daily_execution_ledger(self.connection, account="Sim101", now=before_roll)["loss_used"], 250.0)
        self.assertEqual(daily_execution_ledger(self.connection, account="Sim101", now=after_roll)["loss_used"], 0.0)


class BrokerFailureTests(_GatewayHarness):
    def test_broker_rejects_entry_state_reconciles_and_frees_capacity(self):
        self.reconcile()
        rejected = self.intent("rej")
        enqueue_intent(self.connection, rejected)
        self.event(rejected.command_id, "REJECTED", "rej-1", message="Entry order rejected by broker.")
        self.assertEqual(get_command(self.connection, rejected.command_id)["status"], "REJECTED")
        self.assertTrue(self.reconcile()["ok"])
        ledger = daily_execution_ledger(self.connection, account="Sim101", now=NOW)
        self.assertEqual((ledger["active_commands"], ledger["trades"], ledger["loss_used"]), (0, 0, 0.0))
        self.assertTrue(evaluate_intent(self.connection, self.intent("after"), sim_config(), now=NOW).allowed)

    def test_protective_stop_failure_blocks_and_charges_full_risk(self):
        self.reconcile()
        item = self.intent("nostop")
        enqueue_intent(self.connection, item)
        self.event(item.command_id, "FILLED", "f", filled_quantity=item.quantity, price=4300.0)
        self.event(item.command_id, "REJECTED", "stop-rej", message="Protective bracket order rejected.")
        verdict = self.reconcile(positions=[{"instrument": item.execution_contract, "quantity": item.quantity}])
        self.assertFalse(verdict["ok"])
        self.assertEqual(bridge_dispatch_ready(self.connection, sim_config(), now=NOW).code, "RECONCILIATION_REQUIRED")
        ledger = daily_execution_ledger(self.connection, account="Sim101", now=NOW)
        self.assertEqual(ledger["unresolved_loss"], item.risk_dollars)

    def test_filled_without_working_stop_fails_reconciliation(self):
        self.reconcile()
        item = self.intent("unprotected")
        enqueue_intent(self.connection, item)
        self.event(item.command_id, "FILLED", "f", filled_quantity=item.quantity, price=4300.0)
        verdict = self.reconcile(positions=[{"instrument": item.execution_contract, "quantity": item.quantity}])
        self.assertFalse(verdict["ok"])
        self.assertIn("no working protective stop", verdict["reason"])

    def test_partial_fill_reconciles_to_filled_quantity_only(self):
        self.reconcile()
        item = self.intent("partial")
        enqueue_intent(self.connection, item)
        self.event(item.command_id, "PARTIAL", "p1", filled_quantity=2, price=4300.0)
        stop = {"command_id": item.command_id, "name": f"OTR72|{item.command_id}|S", "state": "Working", "order_type": "StopMarket", "quantity": 2}
        self.assertTrue(self.reconcile(positions=[{"instrument": item.execution_contract, "quantity": 2}], orders=[stop])["ok"])
        self.assertFalse(self.reconcile(positions=[{"instrument": item.execution_contract, "quantity": item.quantity}], orders=[stop])["ok"])
        # A partially filled entry must not be cancelled by expiry directives.
        later = NOW + timedelta(hours=1)
        self.assertEqual(cancel_directives(self.connection, account="Sim101", mode="SIM_BRIDGE", kill_switch=True, now=later), [])

    def test_manual_position_is_detected(self):
        verdict = self.reconcile(positions=[{"instrument": "MGC 12-26", "quantity": -3}])
        self.assertFalse(verdict["ok"])
        self.assertFalse(evaluate_intent(self.connection, self.intent(), sim_config(), now=NOW).allowed)

    def test_disconnect_stops_dispatch_without_retry_storm(self):
        self.reconcile()
        enqueue_intent(self.connection, self.intent("dc"))
        later = NOW + timedelta(seconds=60)
        # No snapshot for 60s (bridge offline) -> dispatch blocked.
        self.assertEqual(bridge_dispatch_ready(self.connection, sim_config(), now=later).code, "RECONCILIATION_STALE")
        # The unacknowledged command expires by TTL rather than being retried forever.
        self.assertEqual(poll_commands(self.connection, account="Sim101", mode="SIM_BRIDGE", now=later), [])
        self.assertEqual(get_command(self.connection, self.intent("dc").command_id)["status"], "EXPIRED")

    def test_stale_working_entry_gets_cancel_directive(self):
        self.reconcile()
        item = self.intent("old")
        enqueue_intent(self.connection, item)
        self.event(item.command_id, "WORKING", "w")
        self.assertEqual(cancel_directives(self.connection, account="Sim101", mode="SIM_BRIDGE", kill_switch=False, now=NOW + timedelta(minutes=11)), [])
        directives = cancel_directives(self.connection, account="Sim101", mode="SIM_BRIDGE", kill_switch=False, now=NOW + timedelta(minutes=13))
        self.assertEqual(directives, [{"command_id": item.command_id, "reason": "ENTRY_EXPIRED"}])

    def test_concurrent_pollers_cannot_both_claim_a_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "otr.db"
            a = sqlite3.connect(path)
            b = sqlite3.connect(path)
            try:
                ensure_schema(a)
                enqueue_intent(a, self.intent("race"))
                first = poll_commands(a, account="Sim101", mode="SIM_BRIDGE", now=NOW)
                second = poll_commands(b, account="Sim101", mode="SIM_BRIDGE", now=NOW)
                self.assertEqual(len(first) + len(second), 1)
            finally:
                a.close()
                b.close()


class TickAndAuthHardeningTests(unittest.TestCase):
    def test_bridge_drops_non_finite_and_non_positive_ticks(self):
        from src.dashboard import app as dashboard

        saved = []
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(dashboard, "BRIDGE_KEY", "k"), \
             mock.patch.object(dashboard, "get_connection", lambda: sqlite3.connect(Path(tmp) / "q.db")), \
             mock.patch.object(dashboard, "save_quotes_batch", lambda connection, rows: saved.extend(rows) or len(rows)):
            client = TestClient(dashboard.app)
            ticks = [
                {"symbol": "GC", "contract": "GC 12-26", "timestamp": NOW.isoformat(), "last": price}
                for price in (4300.1, 0.0, -5.0, 4300.2)
            ]
            body = client.post("/market/api/bridge/ticks", json={"ticks": ticks}, headers={"X-OTR-Bridge-Key": "k"}).json()
        self.assertEqual(body["inserted"], 2)
        self.assertEqual(body["rejected"], {"GC": 2})
        self.assertEqual([row[4] for row in saved], [4300.1, 4300.2])

    def test_empty_dashboard_password_fails_closed(self):
        from src.dashboard import app as dashboard

        with mock.patch.object(dashboard, "DASHBOARD_PASSWORD", ""), \
             mock.patch.object(dashboard, "SESSION_SECRET", ""), \
             mock.patch.object(dashboard, "ALLOW_UNAUTHENTICATED_DASHBOARD", False):
            client = TestClient(dashboard.app)
            self.assertTrue(dashboard.auth_required())
            self.assertFalse(dashboard.valid_cookie(None))
            self.assertEqual(client.post("/market/api/login", json={"password": ""}).status_code, 503)
            self.assertEqual(client.get("/market/api/snapshot").status_code, 401)


class UnlimitedQualifiedTradesTests(_GatewayHarness):
    """Owner policy: unlimited good trades, limited bad risk."""

    def test_default_has_no_daily_trade_cap(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ExecutionConfig.from_env().max_trades_per_day, 0)
        self.assertEqual(ExecutionConfig().max_trades_per_day, 0)

    def test_third_fourth_and_fifth_qualified_trades_are_allowed(self):
        self.reconcile()
        config = sim_config(max_trades_per_day=0, max_daily_loss_dollars=1_000.0)
        # Four completed trades (two wins, two losses) -> net +$1,000.
        self.close_trade(self.intent("u1"), entry=4300.0, exit_price=4295.0)  # -250
        self.close_trade(self.intent("u2"), entry=4300.0, exit_price=4315.0)  # +750
        for ident in ("u3", "u4", "u5"):
            decision = evaluate_intent(self.connection, self.intent(ident), config, now=NOW)
            self.assertTrue(decision.allowed, f"{ident}: {decision.code} {decision.reason}")
            self.close_trade(self.intent(ident), entry=4300.0, exit_price=4315.0 if ident != "u4" else 4295.0)
        self.assertEqual(daily_execution_ledger(self.connection, account="Sim101", now=NOW)["trades"], 5)
        self.assertTrue(evaluate_intent(self.connection, self.intent("u6"), config, now=NOW).allowed)

    def test_no_cooldown_after_a_win(self):
        self.reconcile()
        self.close_trade(self.intent("win"), entry=4300.0, exit_price=4315.0)
        self.assertTrue(evaluate_intent(self.connection, self.intent("next"), sim_config(), now=NOW).allowed)

    def test_loss_capacity_still_blocks_with_unlimited_trades(self):
        self.reconcile()
        config = sim_config(max_trades_per_day=0, max_daily_loss_dollars=750.0)
        self.close_trade(self.intent("x1"), entry=4300.0, exit_price=4295.0)
        self.close_trade(self.intent("x2"), entry=4300.0, exit_price=4295.0)
        self.close_trade(self.intent("x3"), entry=4300.0, exit_price=4295.0)
        self.assertEqual(evaluate_intent(self.connection, self.intent("x4"), config, now=NOW).code, "DAILY_LOSS_LIMIT")

    def test_one_active_gold_idea_still_enforced_with_unlimited_trades(self):
        self.reconcile()
        enqueue_intent(self.connection, self.intent("active"))
        self.assertEqual(evaluate_intent(self.connection, self.intent("second"), sim_config(), now=NOW).code, "EXPOSURE_LIMIT")

    def test_optional_trade_cap_still_works_when_explicitly_configured(self):
        self.reconcile()
        config = sim_config(max_trades_per_day=1, max_daily_loss_dollars=10_000)
        self.close_trade(self.intent("c1"), entry=4300.0, exit_price=4315.0)
        self.assertEqual(evaluate_intent(self.connection, self.intent("c2"), config, now=NOW).code, "DAILY_TRADE_LIMIT")


class Operation81RiskPreservationTests(_GatewayHarness):
    def test_daily_loss_default_follows_paper_guard_internal_stop(self):
        with mock.patch.dict(os.environ, {"EVAL_INTERNAL_DAILY_STOP": "1000"}, clear=True):
            self.assertEqual(ExecutionConfig.from_env().max_daily_loss_dollars, 1000.0)
        with mock.patch.dict(os.environ, {"EVAL_INTERNAL_DAILY_STOP": "1000", "OTR_EXECUTION_MAX_DAILY_LOSS": "900"}, clear=True):
            self.assertEqual(ExecutionConfig.from_env().max_daily_loss_dollars, 900.0)

    def test_a_plus_750_dollar_plan_is_transmittable_with_matching_caps(self):
        self.reconcile()
        config = sim_config(max_risk_dollars=750.0, max_daily_loss_dollars=1000.0)
        intent = build_execution_intent(gold_setup("aplus"), risk_dollars=750.0, config=config, signal_contract=CONTRACT, now=NOW)
        # 5pt stop on MGC = $50/contract -> 15 micros = exactly $750.
        self.assertEqual((intent.quantity, intent.risk_dollars), (15, 750.0))
        self.assertTrue(evaluate_intent(self.connection, intent, config, now=NOW).allowed)

    def test_a_500_dollar_plan_is_floored_to_whole_mgc(self):
        intent = build_execution_intent(gold_setup("a", stop=4296.3), risk_dollars=500.0, config=sim_config(max_risk_dollars=750.0), signal_contract=CONTRACT, now=NOW)
        # 3.7pt stop = $37/contract -> floor(500/37) = 13 micros = $481.
        self.assertEqual(intent.quantity, 13)
        self.assertLessEqual(intent.risk_dollars, 500.0)

    def test_high_reward_targets_are_never_reduced(self):
        self.reconcile()
        config = sim_config(max_risk_dollars=750.0, max_daily_loss_dollars=1000.0)
        # ~6.88R plan: 5pt risk, 34.4pt reward.
        intent = build_execution_intent(gold_setup("big", target=4334.4), risk_dollars=750.0, config=config, signal_contract=CONTRACT, now=NOW)
        self.assertEqual(intent.target_price, 4334.4)
        self.assertTrue(evaluate_intent(self.connection, intent, config, now=NOW).allowed)

    def test_dynamic_low_rr_plan_is_not_blocked_by_broker_layer(self):
        self.reconcile()
        intent = self.intent("lowrr", target=4306.0)  # 1.2R, permitted upstream by 8.1 for A+
        self.assertTrue(evaluate_intent(self.connection, intent, sim_config(), now=NOW).allowed)


def replay_setup(ident, registered_at, **kwargs):
    setup = gold_setup(ident, **kwargs)
    setup.metadata["pending_registered_at_81"] = registered_at.isoformat()
    return setup


class DataClockPolicyTests(_GatewayHarness):
    # A September replay session viewed from an October wall clock.
    REPLAY_HEAD = datetime(2026, 9, 15, 14, 30, tzinfo=UTC)

    def replay_stream(self, head=None, count=3):
        head = head or self.REPLAY_HEAD
        self.connection.execute("DELETE FROM market_quotes")
        for offset in range(count - 1, -1, -1):
            self.connection.execute(
                "INSERT INTO market_quotes(symbol, source, received_at, ingested_at) VALUES ('GC', ?, ?, ?)",
                (f"ninjatrader:{CONTRACT}", (head - timedelta(seconds=offset)).isoformat(), NOW.isoformat()),
            )

    def replay_intent(self, ident, registered_at, config):
        return build_execution_intent(
            replay_setup(ident, registered_at), risk_dollars=250.0, config=config,
            signal_contract=CONTRACT, now=NOW, entry_lifetime_seconds=720,
        )

    def test_clock_policy_is_explicit_and_defaults_to_wall(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ExecutionConfig.from_env().data_clock, "WALL")
        for raw in ("", "auto", "replay-ish", "LIVE"):
            with mock.patch.dict(os.environ, {"OTR_EXECUTION_DATA_CLOCK": raw}, clear=True):
                self.assertEqual(ExecutionConfig.from_env().data_clock, "WALL", raw)
        with mock.patch.dict(os.environ, {"OTR_EXECUTION_DATA_CLOCK": "replay"}, clear=True):
            self.assertEqual(ExecutionConfig.from_env().data_clock, "REPLAY")

    def test_fresh_live_data_passes(self):
        self.reconcile()
        intent = self.replay_intent("live-ok", NOW - timedelta(seconds=5), sim_config())
        self.assertTrue(evaluate_intent(self.connection, intent, sim_config(), now=NOW).allowed)

    def test_stale_live_data_blocks(self):
        self.reconcile(now=NOW + timedelta(seconds=30))
        later = NOW + timedelta(seconds=30)
        self.assertEqual(evaluate_intent(self.connection, self.intent(now=later), sim_config(), now=later).code, "MARKET_DATA_STALE")

    def test_live_restart_backlog_signal_blocks(self):
        self.reconcile()
        # Feed is current, but the setup was registered from backlog ticks
        # processed ten minutes behind real time.
        intent = self.replay_intent("backlog", NOW - timedelta(minutes=10), sim_config())
        self.assertEqual(evaluate_intent(self.connection, intent, sim_config(), now=NOW).code, "SIGNAL_BACKLOG")

    def test_historical_replay_blocks_without_explicit_replay_clock(self):
        self.replay_stream()
        self.reconcile()
        config = sim_config()  # WALL clock (default)
        intent = self.replay_intent("hist", self.REPLAY_HEAD - timedelta(seconds=2), config)
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=NOW).code, "MARKET_DATA_NOT_CURRENT")

    def test_historical_replay_passes_with_explicit_replay_clock(self):
        self.replay_stream()
        self.reconcile()
        config = sim_config(data_clock="REPLAY")
        intent = self.replay_intent("hist", self.REPLAY_HEAD - timedelta(seconds=2), config)
        decision = evaluate_intent(self.connection, intent, config, now=NOW)
        self.assertTrue(decision.allowed, decision.reason)

    def test_replay_backlog_relative_to_stream_still_blocks(self):
        self.replay_stream()
        self.reconcile()
        config = sim_config(data_clock="REPLAY")
        intent = self.replay_intent("old", self.REPLAY_HEAD - timedelta(minutes=10), config)
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=NOW).code, "REPLAY_SIGNAL_BACKLOG")

    def test_replay_stalled_stream_blocks(self):
        self.replay_stream()
        self.reconcile(now=NOW + timedelta(seconds=30))
        later = NOW + timedelta(seconds=30)
        config = sim_config(data_clock="REPLAY")
        intent = self.replay_intent("stall", self.REPLAY_HEAD, config)
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=later).code, "MARKET_DATA_STALE")

    def test_replay_rewind_blocks(self):
        self.replay_stream()
        self.connection.execute(
            "INSERT INTO market_quotes(symbol, source, received_at, ingested_at) VALUES ('GC', ?, ?, ?)",
            (f"ninjatrader:{CONTRACT}", (self.REPLAY_HEAD - timedelta(hours=1)).isoformat(), NOW.isoformat()),
        )
        self.reconcile()
        config = sim_config(data_clock="REPLAY")
        intent = self.replay_intent("rewind", self.REPLAY_HEAD - timedelta(hours=1), config)
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=NOW).code, "MARKET_DATA_REORDERED")

    def test_accelerated_replay_chart_interleave_is_tolerated(self):
        # Two charts feeding GC under fast replay: newest row is 60 event-s
        # behind the other chart's last tick. Not a rewind.
        self.replay_stream()
        self.connection.execute(
            "INSERT INTO market_quotes(symbol, source, received_at, ingested_at) VALUES ('GC', ?, ?, ?)",
            (f"ninjatrader:{CONTRACT}", (self.REPLAY_HEAD - timedelta(seconds=60)).isoformat(), NOW.isoformat()),
        )
        self.reconcile()
        config = sim_config(data_clock="REPLAY")
        intent = self.replay_intent("interleave", self.REPLAY_HEAD - timedelta(seconds=60), config)
        decision = evaluate_intent(self.connection, intent, config, now=NOW)
        self.assertTrue(decision.allowed, decision.reason)

    def test_replay_requires_registration_event_time(self):
        self.replay_stream()
        self.reconcile()
        config = sim_config(data_clock="REPLAY")
        intent = build_execution_intent(gold_setup("nostamp"), risk_dollars=250.0, config=config, signal_contract=CONTRACT, now=NOW)
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=NOW).code, "REPLAY_SIGNAL_TIME_REQUIRED")

    def test_replay_clock_is_forbidden_in_live_mode(self):
        config = sim_config(mode=ExecutionMode.LIVE, account="Live1", live_allowed=True, certified=True, data_clock="REPLAY")
        self.assertEqual(bridge_dispatch_ready(self.connection, config, now=NOW).code, "REPLAY_CLOCK_FORBIDDEN")
        intent = self.replay_intent("live-replay", NOW, config)
        self.assertEqual(evaluate_intent(self.connection, intent, config, now=NOW).code, "REPLAY_CLOCK_FORBIDDEN")

    def test_replay_entry_expiry_uses_replay_event_time(self):
        self.replay_stream()
        self.reconcile()
        config = sim_config(data_clock="REPLAY")
        item = self.replay_intent("exp", self.REPLAY_HEAD, config)
        enqueue_intent(self.connection, item)
        self.event(item.command_id, "WORKING", "w-exp")
        # 13 replay minutes later (but still the same wall clock): expired.
        self.replay_stream(head=self.REPLAY_HEAD + timedelta(minutes=13))
        directives = cancel_directives(self.connection, account="Sim101", mode="SIM_BRIDGE", kill_switch=False, now=NOW, replay_clock=True)
        self.assertEqual(directives, [{"command_id": item.command_id, "reason": "ENTRY_EXPIRED"}])
        # The wall-clock deadline has not passed, so WALL policy would not cancel.
        self.assertEqual(cancel_directives(self.connection, account="Sim101", mode="SIM_BRIDGE", kill_switch=False, now=NOW), [])

    def test_replay_daily_ledger_follows_replay_trading_days(self):
        config = sim_config(data_clock="REPLAY")
        monday = datetime(2026, 9, 14, 14, 0, tzinfo=UTC)
        tuesday = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
        for ident, day in (("mon", monday), ("tue", tuesday)):
            item = self.replay_intent(ident, day, config)
            self.close_trade(item, entry=4300.0, exit_price=4295.0, prefix=ident)
        ledger = daily_execution_ledger(self.connection, account="Sim101", now=tuesday, replay_clock=True)
        self.assertEqual((ledger["trades"], ledger["loss_used"]), (1, 250.0))
        # The WALL clock would lump both replay days into today's wall day.
        wall = daily_execution_ledger(self.connection, account="Sim101", now=NOW)
        self.assertEqual(wall["trades"], 2)


class ReplayProvenanceTests(unittest.TestCase):
    def test_worker_declares_its_true_engine(self):
        from src.research.replay import worker

        provenance = worker.engine_provenance()
        self.assertEqual(provenance["engine_module"], "src.main_70")
        self.assertEqual(provenance["engine_operation"], "Operation 7.0")
        self.assertFalse(provenance["production_parity"])

    def test_runner_refuses_manifest_claiming_a_different_engine(self):
        from src.research.replay.runner import ReplayRunner
        from src.research.replay.runs import ReplayRunStore, RunManifest

        with tempfile.TemporaryDirectory() as tmp:
            store = ReplayRunStore(Path(tmp) / "runs.db", Path(tmp) / "runs")
            store.initialize()
            manifest = RunManifest(
                run_id="mislabelled", git_commit="UNKNOWN", capture_id="c", markets=("GC",),
                contracts=("GC 12-26",), start_time="2026-09-01T00:00:00+00:00", end_time="2026-09-02T00:00:00+00:00",
                enabled_timeframes=("1m",), configuration={}, account_profile={}, engine_version="Operation 8.1",
            )
            with self.assertRaisesRegex(ValueError, "Operation 7.0"):
                ReplayRunner(store, Path.cwd()).run(manifest, Path(tmp) / "missing.db")
            # Refused before any run directory or ledger was created.
            self.assertFalse((Path(tmp) / "runs" / "mislabelled").exists())

    def test_git_commit_is_detected_not_hard_coded(self):
        from src.research.replay.runner import current_git_commit

        commit = current_git_commit(Path(__file__).resolve().parents[1])
        self.assertTrue(commit == "UNKNOWN" or len(commit) == 40)
        self.assertNotEqual(commit, "62ee79bf441e2da26787052d00a92544c5605b17")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(current_git_commit(tmp), "UNKNOWN")


class BridgeApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "otr.db"
        app = FastAPI()
        app.include_router(execution_api.build_router(require_http_auth=lambda request: None, require_bridge_key=lambda request: None))
        self.client = TestClient(app)
        self.patch = mock.patch.object(execution_api, "get_connection", lambda: sqlite3.connect(self.path))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_commands_endpoint_delivers_cancels_even_when_dispatch_blocked(self):
        connection = sqlite3.connect(self.path)
        ensure_schema(connection)
        intent = build_execution_intent(gold_setup("api"), risk_dollars=250.0, config=sim_config(), signal_contract=CONTRACT, now=NOW, entry_lifetime_seconds=720)
        enqueue_intent(connection, intent)
        record_event(connection, {"event_id": "w", "command_id": intent.command_id, "event_type": "WORKING", "occurred_at": NOW.isoformat()}, now=NOW)
        connection.close()

        env = {"OTR_EXECUTION_MODE": "SIM_BRIDGE", "OTR_EXECUTION_ARMED": "1", "OTR_EXECUTION_ACCOUNT": "Sim101"}
        with mock.patch.dict(os.environ, env):
            response = self.client.post("/market/api/execution/kill-switch", json={"enabled": True, "reason": "test"})
            self.assertEqual(response.status_code, 200)
            body = self.client.get("/market/api/bridge/execution/commands").json()
        self.assertFalse(body["dispatch_ready"])
        self.assertEqual(body["code"], "KILL_SWITCH")
        self.assertEqual(body["commands"], [])
        self.assertEqual(body["cancels"], [{"command_id": intent.command_id, "reason": "KILL_SWITCH"}])

    def test_status_reports_daily_broker_ledger(self):
        body = self.client.get("/market/api/execution/status").json()
        self.assertIn("daily_ledger", body)
        self.assertEqual(body["config"]["allowed_symbols"], ["GC"])
        self.assertFalse(body["broker_transmission_possible"])


class RuntimeMirrorTests(unittest.TestCase):
    def test_refused_paper_registration_is_never_mirrored_to_broker(self):
        script = r'''
import json
from types import SimpleNamespace
from src import main_72
calls = []
main_72._original_paper_register_72 = lambda setup, **kw: SimpleNamespace(status="INVALIDATED")
main_72.execution_gateway.handle_approved_setup = lambda *a, **kw: calls.append(1) or {"code": "QUEUED"}
setup = SimpleNamespace(setup_id="x", symbol="GC", timeframe="1m", direction="bullish")
main_72._register_setup_72(setup, risk_dollars=250.0)
main_72._original_paper_register_72 = lambda setup, **kw: SimpleNamespace(status="PENDING")
main_72._register_setup_72(setup, risk_dollars=250.0)
print(json.dumps({"calls": len(calls)}))
'''
        result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True, timeout=60)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["calls"], 1)


if __name__ == "__main__":
    unittest.main()
