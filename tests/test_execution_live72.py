from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3
import unittest

from src.execution.live.config import ExecutionConfig
from src.execution.live.models import ExecutionMode
from src.execution.live.safety import bridge_dispatch_ready, evaluate_intent
from src.execution.live.sizing import build_execution_intent
from src.execution.live.store import enqueue_intent, ensure_schema, poll_commands, record_bridge_snapshot, record_event, set_state


UTC = timezone.utc
NOW = datetime(2026, 8, 28, 20, 0, tzinfo=UTC)


def setup(symbol="NQ", ident="s1"):
    return SimpleNamespace(
        setup_id=ident,
        symbol=symbol,
        timeframe="5m",
        direction="bullish",
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        risk_reward=2.0,
        metadata={"strategy": "ICT_CONFLUENCE", "risk_multiplier": 1.0, "a_plus_context": {"quality_grade": "A+", "quality_score": 92}},
    )


def sim_config(**overrides):
    values = dict(
        mode=ExecutionMode.SIM_BRIDGE,
        armed=True,
        live_allowed=False,
        certified=False,
        account="Sim101",
        max_micros=40,
        max_risk_dollars=350.0,
        command_ttl_seconds=45,
        heartbeat_ttl_seconds=15,
        reconciliation_ttl_seconds=15,
        claimed_redelivery_seconds=5,
    )
    values.update(overrides)
    return ExecutionConfig(**values)


class ExecutionSizingTests(unittest.TestCase):
    def test_micro_sizing_reuses_canonical_specs(self):
        expected = {"NQ": (25, 10.0), "ES": (10, 25.0), "GC": (5, 50.0)}
        for symbol, (quantity, per_contract) in expected.items():
            intent = build_execution_intent(
                setup(symbol, symbol),
                risk_dollars=250,
                config=sim_config(),
                signal_contract=f"{symbol} SEP26" if symbol != "GC" else "GC DEC26",
                now=NOW,
            )
            self.assertEqual(intent.quantity, quantity)
            self.assertEqual(intent.per_contract_risk, per_contract)
            self.assertEqual(intent.risk_dollars, 250)
            self.assertTrue(intent.execution_contract.startswith({"NQ": "MNQ", "ES": "MES", "GC": "MGC"}[symbol]))

    def test_size_cap_is_hard(self):
        intent = build_execution_intent(setup(), risk_dollars=250, config=sim_config(max_micros=1), signal_contract="NQ SEP26", now=NOW)
        self.assertEqual(intent.quantity, 1)
        self.assertEqual(intent.risk_dollars, 10)


class ExecutionSafetyAndStoreTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        ensure_schema(self.connection)

    def tearDown(self):
        self.connection.close()

    def reconciled(self, account="Sim101", positions=None, orders=None, now=NOW):
        return record_bridge_snapshot(
            self.connection,
            {"bridge_id": "test-bridge", "timestamp": now.isoformat(), "account": account, "positions": positions or [], "orders": orders or []},
            configured_account="Sim101",
            now=now,
        )

    def intent(self, config=None, ident="s1"):
        return build_execution_intent(setup(ident=ident), risk_dollars=250, config=config or sim_config(), signal_contract="NQ SEP26", now=NOW)

    def test_paper_and_unarmed_modes_fail_closed(self):
        paper = ExecutionConfig(mode=ExecutionMode.PAPER)
        intent = build_execution_intent(setup(), risk_dollars=250, config=paper, signal_contract="NQ SEP26", now=NOW)
        decision = evaluate_intent(self.connection, intent, paper, now=NOW)
        self.assertEqual(decision.code, "PAPER_ONLY")

        self.reconciled()
        unarmed = sim_config(armed=False)
        intent = self.intent(unarmed)
        decision = evaluate_intent(self.connection, intent, unarmed, now=NOW)
        self.assertEqual(decision.code, "NOT_ARMED")

    def test_clean_sim_reconciliation_allows_intent(self):
        verdict = self.reconciled()
        self.assertTrue(verdict["ok"])
        config = sim_config()
        decision = evaluate_intent(self.connection, self.intent(config), config, now=NOW)
        self.assertTrue(decision.allowed)

    def test_non_sim_account_is_refused_in_sim_bridge(self):
        config = sim_config(account="Funded123")
        decision = evaluate_intent(self.connection, self.intent(config), config, now=NOW)
        self.assertEqual(decision.code, "SIM_ACCOUNT_REQUIRED")

    def test_live_requires_two_independent_interlocks(self):
        config = sim_config(mode=ExecutionMode.LIVE, account="LiveAccount", live_allowed=True, certified=False)
        decision = evaluate_intent(self.connection, self.intent(config), config, now=NOW)
        self.assertEqual(decision.code, "LIVE_INTERLOCK")

    def test_duplicate_setup_cannot_create_duplicate_command(self):
        self.reconciled()
        intent = self.intent()
        enqueue_intent(self.connection, intent)
        enqueue_intent(self.connection, intent)
        count = self.connection.execute("SELECT COUNT(*) FROM execution_commands").fetchone()[0]
        self.assertEqual(count, 1)

    def test_poll_claims_then_ack_stops_redelivery(self):
        self.reconciled()
        intent = self.intent()
        enqueue_intent(self.connection, intent)
        first = poll_commands(self.connection, account="Sim101", mode="SIM_BRIDGE", now=NOW, redelivery_seconds=5)
        self.assertEqual(len(first), 1)
        too_soon = poll_commands(self.connection, account="Sim101", mode="SIM_BRIDGE", now=NOW + timedelta(seconds=2), redelivery_seconds=5)
        self.assertEqual(too_soon, [])
        redelivery = poll_commands(self.connection, account="Sim101", mode="SIM_BRIDGE", now=NOW + timedelta(seconds=6), redelivery_seconds=5)
        self.assertEqual(len(redelivery), 1)
        record_event(self.connection, {"event_id": "evt-ack", "command_id": intent.command_id, "event_type": "ACKNOWLEDGED", "occurred_at": (NOW + timedelta(seconds=7)).isoformat()}, now=NOW + timedelta(seconds=7))
        after_ack = poll_commands(self.connection, account="Sim101", mode="SIM_BRIDGE", now=NOW + timedelta(seconds=20), redelivery_seconds=5)
        self.assertEqual(after_ack, [])

    def test_events_are_idempotent(self):
        self.reconciled()
        intent = self.intent()
        enqueue_intent(self.connection, intent)
        event = {"event_id": "evt-one", "command_id": intent.command_id, "event_type": "ACKNOWLEDGED", "occurred_at": NOW.isoformat()}
        record_event(self.connection, event, now=NOW)
        record_event(self.connection, event, now=NOW)
        count = self.connection.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0]
        self.assertEqual(count, 1)

    def test_unexpected_broker_position_breaks_reconciliation_and_dispatch(self):
        verdict = self.reconciled(positions=[{"instrument": "MNQ SEP26", "quantity": 1, "average_price": 100}])
        self.assertFalse(verdict["ok"])
        ready = bridge_dispatch_ready(self.connection, sim_config(), now=NOW)
        self.assertFalse(ready.allowed)
        self.assertEqual(ready.code, "RECONCILIATION_REQUIRED")

    def test_kill_switch_blocks_dispatch(self):
        self.reconciled()
        set_state(self.connection, "kill_switch", True, NOW)
        ready = bridge_dispatch_ready(self.connection, sim_config(), now=NOW)
        self.assertEqual(ready.code, "KILL_SWITCH")

    def test_filled_position_reconciles_to_expected_quantity(self):
        self.reconciled()
        intent = self.intent()
        enqueue_intent(self.connection, intent)
        record_event(self.connection, {"event_id": "evt-fill", "command_id": intent.command_id, "event_type": "FILLED", "filled_quantity": intent.quantity, "price": intent.entry_price, "occurred_at": NOW.isoformat()}, now=NOW)
        verdict = self.reconciled(
            positions=[{"instrument": intent.execution_contract, "quantity": intent.quantity}],
            orders=[{
                "command_id": intent.command_id,
                "broker_order_id": "protective-stop",
                "name": f"OTR72|{intent.command_id}|S",
                "instrument": intent.execution_contract,
                "state": "Working",
                "order_type": "StopMarket",
                "quantity": intent.quantity,
                "filled_quantity": 0,
            }],
        )
        self.assertTrue(verdict["ok"], verdict["reason"])


class PromotionTests(unittest.TestCase):
    def test_operation72_promotes_every_older_runtime(self):
        from src.dashboard.server_72 import promoted_engine_module

        for value in ("", "src.main_66", "src.main_70", "src.main_71"):
            self.assertEqual(promoted_engine_module(value), "src.main_72")
        self.assertEqual(promoted_engine_module("src.main_72"), "src.main_72")


if __name__ == "__main__":
    unittest.main()
