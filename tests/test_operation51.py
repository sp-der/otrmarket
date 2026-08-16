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
        timeframe="5m",
        created_at=datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc),
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


class Operation51SessionConsistencyTests(unittest.TestCase):
    def test_selected_timeframe_only(self):
        con = db()
        decision = evaluate_session_consistency(con, setup(timeframe="1m"), config())
        self.assertFalse(decision.allowed)
        self.assertIn("executing 5m", decision.reason)

    def test_all_mode_allows_supported_timeframes_to_reach_quality_gate(self):
        for timeframe in ("1m", "5m", "15m", "1h"):
            with self.subTest(timeframe=timeframe):
                con = db()
                decision = evaluate_session_consistency(
                    con,
                    setup(timeframe=timeframe),
                    config(execution_timeframe="ALL"),
                )
                self.assertTrue(decision.allowed, decision.reason)
                self.assertTrue(decision.details["multi_timeframe_execution"])
                self.assertEqual(decision.details["candidate_timeframe"], timeframe)

    def test_all_mode_still_rejects_unknown_timeframe(self):
        con = db()
        decision = evaluate_session_consistency(
            con,
            setup(timeframe="30s"),
            config(execution_timeframe="ALL"),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("not a supported", decision.reason)

    def test_selected_session_window_only(self):
        con = db()
        # 13:00 UTC is 09:00 ET during August DST.
        candidate = setup(created_at=datetime(2026, 8, 17, 13, 0, tzinfo=timezone.utc))
        decision = evaluate_session_consistency(con, candidate, config())
        self.assertFalse(decision.allowed)
        self.assertIn("Outside selected", decision.reason)

    def test_clean_first_trade_is_allowed(self):
        con = db()
        decision = evaluate_session_consistency(con, setup(), config())
        self.assertTrue(decision.allowed, decision.reason)
        self.assertEqual(decision.details["local_day"], "Monday")
        self.assertEqual(decision.details["local_time"], "10:00")

    def test_base_winning_day_locks_new_risk(self):
        con = db()
        opened = datetime(2026, 8, 17, 13, 40, tzinfo=timezone.utc).isoformat()
        closed = datetime(2026, 8, 17, 13, 50, tzinfo=timezone.utc).isoformat()
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?)",
            ("win", "CLOSED", "WIN", opened, closed, 300.0),
        )
        con.commit()
        decision = evaluate_session_consistency(con, setup(), config())
        self.assertFalse(decision.allowed)
        self.assertIn("Base winning day secured", decision.reason)

    def test_second_trade_after_loss_must_be_exceptional(self):
        con = db()
        opened = datetime(2026, 8, 17, 13, 35, tzinfo=timezone.utc).isoformat()
        closed = datetime(2026, 8, 17, 13, 45, tzinfo=timezone.utc).isoformat()
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?)",
            ("loss", "CLOSED", "LOSS", opened, closed, -200.0),
        )
        con.commit()

        weak = setup(
            risk_reward=1.5,
            displacement=SimpleNamespace(body_ratio=1.7, range_ratio=1.4),
        )
        decision = evaluate_session_consistency(con, weak, config())
        self.assertFalse(decision.allowed)
        self.assertIn("must be exceptional", decision.reason)

        strong = setup(
            risk_reward=2.5,
            displacement=SimpleNamespace(body_ratio=2.1, range_ratio=1.7),
        )
        decision = evaluate_session_consistency(con, strong, config())
        self.assertTrue(decision.allowed, decision.reason)


if __name__ == "__main__":
    unittest.main()
