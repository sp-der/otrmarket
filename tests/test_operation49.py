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
        setup_id="candidate",
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


class Operation49QualityGateCompatibilityTests(unittest.TestCase):
    def setUp(self):
        from src import main_multi

        main_multi.runtime.paper.positions.clear()

    def test_one_minute_nq_requires_smt(self):
        con = db()
        allowed, reason = _a_plus_quality_gate(
            con,
            setup(trigger_type="liquidity_sweep", risk_reward=3.0),
        )
        self.assertFalse(allowed)
        self.assertIn("requires SMT", reason)

    def test_rr_no_longer_decides_a_plus_quality_above_one_r(self):
        con = db()
        allowed, reason = _a_plus_quality_gate(con, setup(risk_reward=1.25))
        self.assertTrue(allowed, reason)

    def test_sub_one_r_is_still_rejected(self):
        con = db()
        allowed, reason = _a_plus_quality_gate(con, setup(risk_reward=0.9))
        self.assertFalse(allowed)
        self.assertIn("1.00R", reason)

    def test_loss_creates_sixty_minute_same_market_reset(self):
        con = db()
        closed = datetime(2026, 8, 16, 13, 0, tzinfo=timezone.utc)
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?)",
            ("loss", "NQ", "CLOSED", closed.isoformat(), "LOSS"),
        )
        con.commit()
        allowed, reason = _a_plus_quality_gate(
            con,
            setup(created_at=closed + timedelta(minutes=45), risk_reward=3.0),
        )
        self.assertFalse(allowed)
        self.assertIn("Same-market reset", reason)

    def test_cross_market_loss_creates_thirty_minute_global_reset(self):
        con = db()
        closed = datetime(2026, 8, 16, 13, 45, tzinfo=timezone.utc)
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?)",
            ("loss", "GC", "CLOSED", closed.isoformat(), "LOSS"),
        )
        con.commit()
        allowed, reason = _a_plus_quality_gate(
            con,
            setup(symbol="NQ", created_at=closed + timedelta(minutes=20)),
        )
        self.assertFalse(allowed)
        self.assertIn("Global post-loss", reason)

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
