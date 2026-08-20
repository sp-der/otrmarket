import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src import main_70 as op70


def _setup(symbol="NQ", grade="A+", minutes=0):
    created_at = datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc) + timedelta(minutes=minutes)
    return SimpleNamespace(
        symbol=symbol,
        timeframe="1m",
        created_at=created_at,
        metadata={"a_plus_context": {"quality_grade": grade}, "risk_multiplier": 1.0},
    )


def _connection():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT,
            status TEXT,
            result TEXT,
            opened_at TEXT,
            closed_at TEXT
        );
        CREATE TABLE strategy_setups (
            setup_id TEXT PRIMARY KEY,
            created_at TEXT,
            payload_json TEXT
        );
        """
    )
    return connection


def _closed_trade(connection, setup_id, symbol, result, closed_at, grade=None):
    connection.execute(
        "INSERT INTO paper_trades(setup_id, symbol, status, result, closed_at) VALUES (?, ?, 'CLOSED', ?, ?)",
        (setup_id, symbol, result, closed_at.isoformat()),
    )
    payload = {"metadata": {}}
    if grade:
        payload["metadata"]["a_plus_context"] = {"quality_grade": grade}
    connection.execute(
        "INSERT INTO strategy_setups(setup_id, created_at, payload_json) VALUES (?, ?, ?)",
        (setup_id, closed_at.isoformat(), json.dumps(payload)),
    )
    connection.commit()


class Operation70Tests(unittest.TestCase):
    def test_cross_market_loss_cooldown_removed(self):
        allowed, reason = op70._no_cross_market_loss_cooldown(None, _setup("ES"))
        self.assertTrue(allowed)
        self.assertIn("unrelated", reason.lower())

    def test_same_symbol_loss_uses_30_minute_cooldown(self):
        connection = _connection()
        loss_time = datetime(2026, 8, 19, 14, 45, tzinfo=timezone.utc)
        _closed_trade(connection, "loss1", "NQ", "LOSS", loss_time)

        blocked, reason = op70._same_symbol_cooldown_70(connection, _setup("NQ", minutes=0))
        allowed, _ = op70._same_symbol_cooldown_70(connection, _setup("NQ", minutes=20))

        self.assertFalse(blocked)
        self.assertIn("15", reason)
        self.assertTrue(allowed)

    def test_nq_loss_does_not_disable_es_b_plus(self):
        connection = _connection()
        loss_time = datetime(2026, 8, 19, 14, 30, tzinfo=timezone.utc)
        _closed_trade(connection, "nq_loss", "NQ", "LOSS", loss_time)

        allowed, reason = op70._b_plus_execution_gate_70(connection, _setup("ES", "B+"))

        self.assertTrue(allowed)
        self.assertIn("unrelated-market", reason)

    def test_same_symbol_loss_disables_b_plus_only_on_that_symbol(self):
        connection = _connection()
        loss_time = datetime(2026, 8, 19, 14, 30, tzinfo=timezone.utc)
        _closed_trade(connection, "gc_loss", "GC", "LOSS", loss_time)

        allowed, reason = op70._b_plus_execution_gate_70(connection, _setup("GC", "B+"))

        self.assertFalse(allowed)
        self.assertIn("GC", reason)

    def test_single_symbol_loss_allows_a_plus_at_60_percent(self):
        connection = _connection()
        loss_time = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)
        _closed_trade(connection, "nq_loss", "NQ", "LOSS", loss_time)
        setup = _setup("NQ", "A+")

        allowed, reason = op70._post_loss_risk_70(connection, setup)

        self.assertTrue(allowed)
        self.assertEqual(setup.metadata["risk_multiplier"], 0.60)
        self.assertEqual(setup.metadata["execution_tier"], "SYMBOL_RECOVERY_70")
        self.assertIn("60%", reason)

    def test_other_market_loss_does_not_reduce_clean_symbol(self):
        connection = _connection()
        loss_time = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)
        _closed_trade(connection, "nq_loss", "NQ", "LOSS", loss_time)
        setup = _setup("ES", "A+")

        allowed, _ = op70._post_loss_risk_70(connection, setup)

        self.assertTrue(allowed)
        self.assertEqual(setup.metadata["risk_multiplier"], 1.0)
        self.assertEqual(setup.metadata["recovery_control_70"]["mode"], "NORMAL")

    def test_two_consecutive_losses_trigger_account_recovery(self):
        connection = _connection()
        first = datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)
        second = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)
        _closed_trade(connection, "nq_loss", "NQ", "LOSS", first)
        _closed_trade(connection, "es_loss", "ES", "LOSS", second)
        setup = _setup("GC", "A+")

        allowed, reason = op70._post_loss_risk_70(connection, setup)

        self.assertTrue(allowed)
        self.assertEqual(setup.metadata["risk_multiplier"], 0.35)
        self.assertEqual(setup.metadata["execution_tier"], "ACCOUNT_RECOVERY_70")
        self.assertIn("consecutive", reason)

    def test_two_consecutive_losses_block_b_plus(self):
        connection = _connection()
        first = datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc)
        second = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)
        _closed_trade(connection, "nq_loss", "NQ", "LOSS", first)
        _closed_trade(connection, "es_loss", "ES", "LOSS", second)
        setup = _setup("GC", "B+")

        allowed, reason = op70._post_loss_risk_70(connection, setup)

        self.assertFalse(allowed)
        self.assertIn("B+", reason)

    def test_operation69_precision_gate_remains_installed(self):
        self.assertIs(op70.op59.op58._adaptive_quality_gate, op70.op69._adaptive_quality_gate_69)
        self.assertEqual(op70.op69.op65.INTRABAR_TIMEFRAMES, {"5m"})


if __name__ == "__main__":
    unittest.main()
