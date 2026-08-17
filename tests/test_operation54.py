import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from src.risk.session_consistency import (
    SessionConsistencyConfig,
    evaluate_session_consistency,
)


def db():
    con = sqlite3.connect(":memory:")
    con.execute(
        """
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY,
            status TEXT,
            result TEXT,
            opened_at TEXT,
            closed_at TEXT,
            result_dollars REAL
        )
        """
    )
    return con


def setup(**overrides):
    values = dict(
        symbol="NQ",
        timeframe="5m",
        created_at=datetime(2026, 8, 16, 22, 30, tzinfo=timezone.utc),
        risk_reward=2.25,
        displacement=SimpleNamespace(body_ratio=2.0, range_ratio=1.6),
        metadata={"strategy": "ICT_CONFLUENCE"},
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def config(**overrides):
    values = dict(
        timezone_name="America/New_York",
        execution_timeframe="5m",
        session_start="09:30",
        session_end="13:00",
        max_trades_per_day=2,
        base_win_lock_dollars=250.0,
        second_chance_min_rr=2.0,
        second_chance_body_ratio=1.90,
        second_chance_range_ratio=1.50,
    )
    values.update(overrides)
    return SessionConsistencyConfig(**values)


class Operation54MarketHoursTests(unittest.TestCase):
    def test_sunday_futures_after_globex_open_can_reach_quality_gate(self):
        con = db()
        # 22:30 UTC is 18:30 ET on Aug 16, 2026.
        decision = evaluate_session_consistency(con, setup(symbol="NQ"), config())
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.details["market_session_mode"], "SUNDAY_GLOBEX")

    def test_sunday_futures_before_globex_open_are_still_blocked(self):
        con = db()
        # 21:30 UTC is 17:30 ET on Aug 16, 2026.
        candidate = setup(
            symbol="ES",
            created_at=datetime(2026, 8, 16, 21, 30, tzinfo=timezone.utc),
        )
        decision = evaluate_session_consistency(con, candidate, config())
        self.assertFalse(decision.allowed)
        self.assertIn("18:00", decision.reason)

    def test_bitcoin_can_be_evaluated_24_7(self):
        con = db()
        candidate = setup(
            symbol="BTC",
            created_at=datetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc),
        )
        decision = evaluate_session_consistency(con, candidate, config())
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.details["market_session_mode"], "24_7")

    def test_weekday_futures_still_respect_calibration_window(self):
        con = db()
        candidate = setup(
            symbol="GC",
            created_at=datetime(2026, 8, 17, 13, 0, tzinfo=timezone.utc),
        )
        decision = evaluate_session_consistency(con, candidate, config())
        self.assertFalse(decision.allowed)
        self.assertIn("Outside selected", decision.reason)


if __name__ == "__main__":
    unittest.main()
