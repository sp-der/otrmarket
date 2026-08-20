import sqlite3
import unittest
import importlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.main_multi import _a_plus_quality_gate


def operation70_without_global_patch_leak():
    """Load current helpers while restoring the inherited legacy test surface."""
    from src import main_59 as op59
    from src import main_61 as op61
    from src.execution import paper

    base = op59.op58.base
    original = (
        base._global_loss_cooldown,
        base._same_symbol_cooldown,
        base._b_plus_execution_gate,
        op61._post_loss_risk,
        dict(paper._PENDING_BARS),
    )
    op70 = importlib.import_module("src.main_70")
    (
        base._global_loss_cooldown,
        base._same_symbol_cooldown,
        base._b_plus_execution_gate,
        op61._post_loss_risk,
        pending_bars,
    ) = original
    paper._PENDING_BARS.clear()
    paper._PENDING_BARS.update(pending_bars)
    return op70


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

    def test_operation70_loss_creates_thirty_minute_same_market_reset(self):
        op70 = operation70_without_global_patch_leak()
        con = db()
        closed = datetime(2026, 8, 16, 13, 0, tzinfo=timezone.utc)
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?)",
            ("loss", "NQ", "CLOSED", closed.isoformat(), "LOSS"),
        )
        con.commit()
        blocked, reason = op70._same_symbol_cooldown_70(
            con, setup(created_at=closed + timedelta(minutes=20), risk_reward=3.0)
        )
        allowed, _ = op70._same_symbol_cooldown_70(
            con, setup(created_at=closed + timedelta(minutes=30), risk_reward=3.0)
        )
        self.assertFalse(blocked)
        self.assertIn("10 market minutes", reason)
        self.assertTrue(allowed)

    def test_operation70_cross_market_loss_does_not_freeze_unrelated_market(self):
        op70 = operation70_without_global_patch_leak()
        con = db()
        closed = datetime(2026, 8, 16, 13, 45, tzinfo=timezone.utc)
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?)",
            ("loss", "GC", "CLOSED", closed.isoformat(), "LOSS"),
        )
        con.commit()
        allowed, reason = op70._no_cross_market_loss_cooldown(
            con, setup(symbol="NQ", created_at=closed + timedelta(minutes=20))
        )
        self.assertTrue(allowed)
        self.assertIn("unrelated", reason.lower())

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
