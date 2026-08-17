import sqlite3
import unittest
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from src.main_58 import evaluate_session_consistency_58
from src.risk.session_consistency import SessionConsistencyConfig


NY = ZoneInfo("America/New_York")


@dataclass
class DummySetup:
    symbol: str
    timeframe: str
    created_at: datetime
    metadata: dict = field(default_factory=dict)
    risk_reward: float = 2.0
    displacement: object | None = None


class Operation58AllSessionTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.config = SessionConsistencyConfig(
            timezone_name="America/New_York",
            execution_timeframe="ALL",
            session_start="09:30",
            session_end="13:00",
            max_trades_per_day=1,
            base_win_lock_dollars=250.0,
        )

    def tearDown(self):
        self.connection.close()

    def decision(self, when, symbol="NQ", timeframe="15m"):
        setup = DummySetup(symbol=symbol, timeframe=timeframe, created_at=when)
        return setup, evaluate_session_consistency_58(self.connection, setup, self.config)

    def test_monday_evening_asia_session_can_trade(self):
        setup, decision = self.decision(datetime(2026, 8, 17, 19, 0, tzinfo=NY))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.details["session_tier"], "ASIA")
        self.assertAlmostEqual(setup.metadata["risk_multiplier"], 0.50)

    def test_london_session_can_trade(self):
        setup, decision = self.decision(datetime(2026, 8, 18, 3, 0, tzinfo=NY))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.details["session_tier"], "LONDON")
        self.assertAlmostEqual(setup.metadata["risk_multiplier"], 0.65)

    def test_ny_core_keeps_full_session_risk(self):
        setup, decision = self.decision(datetime(2026, 8, 19, 10, 0, tzinfo=NY))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.details["session_tier"], "NY_CORE")
        self.assertAlmostEqual(setup.metadata["risk_multiplier"], 1.00)

    def test_daily_maintenance_is_hard_blocked(self):
        _, decision = self.decision(datetime(2026, 8, 17, 17, 30, tzinfo=NY))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.details["market_session_mode"], "DAILY_MAINTENANCE")

    def test_friday_after_close_is_hard_blocked(self):
        _, decision = self.decision(datetime(2026, 8, 21, 18, 30, tzinfo=NY))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.details["market_session_mode"], "WEEKLY_CLOSED")

    def test_saturday_is_hard_blocked(self):
        _, decision = self.decision(datetime(2026, 8, 22, 10, 0, tzinfo=NY))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.details["market_session_mode"], "WEEKLY_CLOSED")

    def test_sunday_reopen_can_trade(self):
        setup, decision = self.decision(datetime(2026, 8, 23, 18, 30, tzinfo=NY))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.details["session_tier"], "SUNDAY_GLOBEX")
        self.assertAlmostEqual(setup.metadata["risk_multiplier"], 0.50)

    def test_old_calibration_trade_cap_and_small_win_do_not_stop_all_session_engine(self):
        self.connection.execute(
            """
            CREATE TABLE paper_trades (
                status TEXT,
                result TEXT,
                opened_at TEXT,
                closed_at TEXT,
                result_dollars REAL
            )
            """
        )
        self.connection.execute(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?)",
            (
                "CLOSED",
                "WIN",
                "2026-08-19T13:30:00+00:00",
                "2026-08-19T14:00:00+00:00",
                500.0,
            ),
        )
        self.connection.commit()

        _, decision = self.decision(datetime(2026, 8, 19, 14, 0, tzinfo=NY))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.details["day_stats"]["trades"], 1)
        self.assertGreaterEqual(decision.details["day_stats"]["realized_pnl"], 500.0)
        self.assertFalse(decision.details["calibration_trade_cap_is_hard_block"])
        self.assertFalse(decision.details["base_win_lock_is_hard_block"])


if __name__ == "__main__":
    unittest.main()
