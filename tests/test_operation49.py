import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.main_multi import _a_plus_quality_gate


def db():
    con = sqlite3.connect(":memory:")
    con.execute(
        """
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT,
            status TEXT,
            closed_at TEXT,
            result TEXT
        )
        """
    )
    return con


def setup(**overrides):
    values = dict(
        symbol="NQ",
        timeframe="1m",
        trigger_type="smt",
        risk_reward=2.0,
        created_at=datetime(2026, 8, 16, 14, 0, tzinfo=timezone.utc),
        metadata={
            "strategy": "ICT_CONFLUENCE",
            "entry_type": "FVG_MIDPOINT",
        },
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class Operation49QualityGateTests(unittest.TestCase):
    def test_one_minute_nq_requires_smt(self):
        con = db()
        allowed, reason = _a_plus_quality_gate(
            con,
            setup(trigger_type="liquidity_sweep", risk_reward=3.0),
        )
        self.assertFalse(allowed)
        self.assertIn("requires SMT", reason)

    def test_one_minute_nq_smt_needs_at_least_two_r(self):
        con = db()
        allowed, reason = _a_plus_quality_gate(con, setup(risk_reward=1.75))
        self.assertFalse(allowed)
        self.assertIn("2.00R", reason)

    def test_five_minute_ict_accepts_one_point_five_r(self):
        con = db()
        allowed, reason = _a_plus_quality_gate(
            con,
            setup(timeframe="5m", trigger_type="liquidity_sweep", risk_reward=1.5),
        )
        self.assertTrue(allowed, reason)

    def test_order_block_fallback_has_higher_threshold(self):
        con = db()
        allowed, reason = _a_plus_quality_gate(
            con,
            setup(
                timeframe="5m",
                risk_reward=1.75,
                metadata={"strategy": "ICT_CONFLUENCE", "entry_type": "ORDER_BLOCK"},
            ),
        )
        self.assertFalse(allowed)
        self.assertIn("2.00R", reason)

    def test_loss_creates_sixty_minute_same_market_reset(self):
        con = db()
        closed = datetime(2026, 8, 16, 13, 30, tzinfo=timezone.utc)
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?)",
            ("loss", "NQ", "CLOSED", closed.isoformat(), "LOSS"),
        )
        con.commit()
        allowed, reason = _a_plus_quality_gate(
            con,
            setup(created_at=closed + timedelta(minutes=30), risk_reward=3.0),
        )
        self.assertFalse(allowed)
        self.assertIn("reset window", reason)

    def test_rejection_block_requires_full_ten_of_ten_and_three_r(self):
        con = db()
        rb = setup(
            timeframe="5m",
            risk_reward=3.0,
            metadata={
                "strategy": "REJECTION_BLOCK_10_10",
                "checklist_score": 10,
                "checklist_total": 10,
            },
        )
        allowed, reason = _a_plus_quality_gate(con, rb)
        self.assertTrue(allowed, reason)

        rb.metadata["checklist_score"] = 9
        allowed, reason = _a_plus_quality_gate(con, rb)
        self.assertFalse(allowed)
        self.assertIn("9/10", reason)


if __name__ == "__main__":
    unittest.main()
