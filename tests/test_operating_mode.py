from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import sqlite3
import unittest

from src.risk.operating_mode import OperatingModeConfig, evaluate_operating_mode


REFERENCE = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)


def setup(grade: str = "A+"):
    return SimpleNamespace(
        created_at=REFERENCE,
        metadata={
            "risk_multiplier": 1.0,
            "a_plus_context": {"quality_grade": grade},
        },
    )


class OperatingModeTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """
            CREATE TABLE paper_trades (
                status TEXT,
                opened_at TEXT,
                closed_at TEXT,
                result_dollars REAL
            )
            """
        )

    def tearDown(self):
        self.connection.close()

    def test_eval_allows_first_trade_and_blocks_second_in_same_session(self):
        config = OperatingModeConfig(mode="EVAL")
        candidate = setup()
        allowed, _, details = evaluate_operating_mode(self.connection, candidate, config)
        self.assertTrue(allowed)
        self.assertEqual(details["trades_today"], 0)
        self.assertEqual(candidate.metadata["profit_objective_dollars"], 1500.0)

        self.connection.execute(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?)",
            ("CLOSED", "2026-08-27T13:15:00+00:00", "2026-08-27T13:45:00+00:00", 800.0),
        )
        blocked, reason, details = evaluate_operating_mode(self.connection, setup(), config)
        self.assertFalse(blocked)
        self.assertIn("NEW_YORK", reason)
        self.assertEqual(details["session_trades"], 1)

    def test_funded_mode_protects_profit_zone(self):
        self.connection.execute(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?)",
            ("CLOSED", "2026-08-27T13:00:00+00:00", "2026-08-27T13:30:00+00:00", 400.0),
        )
        config = OperatingModeConfig(mode="FUNDED")

        strong = setup("A+")
        allowed, _, details = evaluate_operating_mode(self.connection, strong, config)
        self.assertTrue(allowed)
        self.assertEqual(strong.metadata["execution_tier"], "FUNDED_PROTECT_A_PLUS")
        self.assertLessEqual(strong.metadata["risk_multiplier"], 0.35)
        self.assertEqual(details["realized_today"], 400.0)

        ordinary = setup("A")
        blocked, reason, _ = evaluate_operating_mode(self.connection, ordinary, config)
        self.assertFalse(blocked)
        self.assertIn("only A+", reason)

    def test_funded_mode_locks_at_daily_ceiling(self):
        self.connection.execute(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?)",
            ("CLOSED", "2026-08-27T13:00:00+00:00", "2026-08-27T13:30:00+00:00", 525.0),
        )
        allowed, reason, _ = evaluate_operating_mode(
            self.connection,
            setup("A+"),
            OperatingModeConfig(mode="FUNDED"),
        )
        self.assertFalse(allowed)
        self.assertIn("ceiling", reason.lower())


if __name__ == "__main__":
    unittest.main()
