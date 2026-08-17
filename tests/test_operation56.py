import json
import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from src.main_multi import _b_plus_execution_gate


NOW = datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc)


def database():
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE strategy_setups (
            setup_id TEXT PRIMARY KEY, created_at TEXT, payload_json TEXT
        );
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY, result TEXT, closed_at TEXT
        );
        """
    )
    return con


def add_b_plus(con, setup_id, *, result=None, closed_at=None):
    payload = {"metadata": {"a_plus_context": {"quality_grade": "B+"}}}
    con.execute(
        "INSERT INTO strategy_setups VALUES (?, ?, ?)",
        (setup_id, NOW.isoformat(), json.dumps(payload)),
    )
    con.execute(
        "INSERT INTO paper_trades VALUES (?, ?, ?)",
        (setup_id, result, closed_at),
    )
    con.commit()


class Operation56BPlusTierTests(unittest.TestCase):
    def test_first_two_b_plus_slots_are_available(self):
        con = database()
        setup = SimpleNamespace(created_at=NOW)
        allowed, reason = _b_plus_execution_gate(con, setup)
        self.assertTrue(allowed)
        self.assertIn("0/2", reason)
        add_b_plus(con, "one")
        allowed, reason = _b_plus_execution_gate(con, setup)
        self.assertTrue(allowed)
        self.assertIn("1/2", reason)

    def test_third_b_plus_trade_is_blocked(self):
        con = database()
        add_b_plus(con, "one")
        add_b_plus(con, "two")
        allowed, reason = _b_plus_execution_gate(con, SimpleNamespace(created_at=NOW))
        self.assertFalse(allowed)
        self.assertIn("2/2", reason)

    def test_any_realized_daily_loss_disables_b_plus(self):
        con = database()
        add_b_plus(con, "loss", result="LOSS", closed_at=NOW.isoformat())
        allowed, reason = _b_plus_execution_gate(con, SimpleNamespace(created_at=NOW))
        self.assertFalse(allowed)
        self.assertIn("disabled after", reason)


if __name__ == "__main__":
    unittest.main()
