import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.execution.paper import PaperExecutor
from src.risk.evaluation import EvaluationConfig, EvaluationRiskGuard
from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.strategies.confluence import ConfluenceEngine, PendingContext
from src.strategies.models import Candle, Displacement, FairValueGap, StrategySetup, SwingPoint


def db():
    con = sqlite3.connect(":memory:")
    con.execute(
        """
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT,
            timeframe TEXT,
            direction TEXT,
            status TEXT,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            opened_at TEXT,
            closed_at TEXT,
            exit_price REAL,
            result TEXT,
            result_r REAL,
            risk_dollars REAL,
            result_dollars REAL,
            guard_reason TEXT,
            updated_at TEXT
        )
        """
    )
    return con


def config(**overrides):
    values = dict(
        enabled=True,
        profile="LUCID_PRO_50K",
        phase="EVALUATION",
        starting_balance=50_000,
        profit_target=3_000,
        max_loss_limit=2_000,
        firm_daily_loss_limit=1_200,
        initial_trail_balance=52_100,
        locked_mll_balance=50_100,
        max_micros=40,
        risk_per_trade=100,
        min_risk_per_trade=25,
        internal_daily_stop=400,
        mll_safety_buffer=400,
        max_trades_per_day=4,
        max_consecutive_losses=3,
        max_concurrent_positions=1,
        no_new_trades_after_et="16:30",
        resume_trading_et="18:00",
    )
    values.update(overrides)
    return EvaluationConfig(**values)


def insert_trade(con, setup_id, status, *, day, result=None, risk=100, pnl=None, opened=True):
    opened_at = (day + timedelta(minutes=1)).isoformat() if opened else None
    closed_at = (day + timedelta(minutes=2)).isoformat() if status == "CLOSED" else None
    con.execute(
        """
        INSERT INTO paper_trades (
            setup_id,symbol,timeframe,direction,status,entry_price,stop_price,target_price,
            opened_at,closed_at,exit_price,result,result_r,risk_dollars,result_dollars,guard_reason,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            setup_id,"NQ","1m","bullish",status,100,99,102,
            opened_at,closed_at,100,result,None,risk,pnl,"test",(day + timedelta(minutes=2)).isoformat(),
        ),
    )
    con.commit()


class Operation45GeometryTests(unittest.TestCase):
    def test_inverted_short_is_rejected(self):
        entry, stop, target = normalize_trade_prices("NQ", "bearish", 29877.88, 29875.99, 29869.75)
        result = validate_trade_geometry("NQ", "bearish", entry, stop, target)
        self.assertFalse(result.valid)
        self.assertIn("target < entry < stop", result.reason)

    def test_prices_are_rounded_to_futures_ticks(self):
        entry, stop, target = normalize_trade_prices("NQ", "bullish", 29877.88, 29875.99, 29881.12)
        self.assertAlmostEqual(entry % 0.25, 0.0, places=7)
        self.assertAlmostEqual(stop % 0.25, 0.0, places=7)
        self.assertAlmostEqual(target % 0.25, 0.0, places=7)

    def test_bad_bearish_swept_level_falls_back_to_swing_high(self):
        t = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
        candles = [
            Candle("NQ", "1m", t + timedelta(minutes=i), t + timedelta(minutes=i + 1), 100, 101, 99, 100.5, 10)
            for i in range(10)
        ]
        entry_fvg = FairValueGap("NQ", "1m", "bearish", 100.0, 101.0, t, t, t)
        ctx = PendingContext(
            symbol="NQ",
            timeframe="1m",
            direction="bearish",
            pd_array=entry_fvg,
            stage="WAIT_VALID_RR",
            started_bar_count=1,
            stage_bar_count=6,
            trigger_type="smt",
            swept_level=99.0,  # invalid for a short stop; below entry
            displacement=Displacement("NQ", "1m", "bearish", t, 98, 104, 2, 2),
        )
        swing_high = SwingPoint("NQ", "1m", "high", 102.0, t, 2)
        swing_low = SwingPoint("NQ", "1m", "low", 95.0, t, 3)
        engine = ConfluenceEngine(min_rr=1.25)
        with patch("src.strategies.confluence.detect_swings", return_value=[swing_high, swing_low]), patch(
            "src.strategies.confluence.nearest_target_swing", return_value=swing_low
        ):
            setup = engine._build_setup(candles, ctx, entry_fvg, t)
        self.assertIsNotNone(setup)
        self.assertGreater(setup.stop_price, setup.entry_price)
        self.assertLess(setup.target_price, setup.entry_price)
        self.assertEqual(setup.entry_price % 0.25, 0)

    def test_paper_executor_refuses_invalid_geometry(self):
        t = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
        fvg = FairValueGap("NQ", "1m", "bearish", 100, 101, t, t, t)
        setup = StrategySetup(
            "bad", "NQ", "1m", "bearish", t, fvg, "smt", {},
            Displacement("NQ", "1m", "bearish", t, 99, 102, 2, 2),
            fvg, 100.5, 99.0, 95.0, 2.0,
        )
        with self.assertRaises(ValueError):
            PaperExecutor().register_setup(setup, risk_dollars=100)


class Operation45EvaluationGuardTests(unittest.TestCase):
    def test_empty_50k_eval_starts_with_2000_cushion(self):
        con = db()
        ref = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
        snap = EvaluationRiskGuard(config()).snapshot(con, ref)
        self.assertEqual(snap["status"], "ACTIVE")
        self.assertEqual(snap["mll_floor"], 48000)
        self.assertEqual(snap["mll_cushion"], 2000)
        self.assertEqual(snap["available_risk"], 100)

    def test_internal_daily_stop_locks_new_trades(self):
        con = db()
        day = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
        insert_trade(con, "loss1", "CLOSED", day=day, result="LOSS", risk=400, pnl=-400)
        snap = EvaluationRiskGuard(config()).snapshot(con, day + timedelta(hours=1))
        self.assertEqual(snap["status"], "DAILY_LOCK")
        self.assertEqual(snap["available_risk"], 0)

    def test_profit_target_marks_eval_passed(self):
        con = db()
        day = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
        insert_trade(con, "win", "CLOSED", day=day, result="WIN", risk=100, pnl=3000)
        snap = EvaluationRiskGuard(config()).snapshot(con, day + timedelta(hours=1))
        self.assertEqual(snap["status"], "PASSED")
        self.assertEqual(snap["balance"], 53000)

    def test_eod_drawdown_trails_prior_closing_balance(self):
        con = db()
        day1 = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
        insert_trade(con, "win", "CLOSED", day=day1, result="WIN", risk=100, pnl=1000)
        ref = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
        snap = EvaluationRiskGuard(config()).snapshot(con, ref)
        self.assertEqual(snap["peak_eod_balance"], 51000)
        self.assertEqual(snap["mll_floor"], 49000)

    def test_eod_drawdown_locks_at_50100_after_trail_threshold(self):
        con = db()
        day1 = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
        insert_trade(con, "win", "CLOSED", day=day1, result="WIN", risk=100, pnl=2200)
        ref = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
        snap = EvaluationRiskGuard(config(profit_target=9999)).snapshot(con, ref)
        self.assertEqual(snap["peak_eod_balance"], 52200)
        self.assertEqual(snap["mll_floor"], 50100)

    def test_active_position_blocks_second_trade(self):
        con = db()
        day = datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc)
        insert_trade(con, "pending", "PENDING", day=day, risk=100, pnl=None, opened=False)
        decision = EvaluationRiskGuard(config()).decide(con, day)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.status, "POSITION_LOCK")

class Operation45DashboardPnlTests(unittest.TestCase):
    def _repo_db(self):
        import tempfile
        from pathlib import Path
        from src.dashboard.queries import DashboardRepository

        handle = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        handle.close()
        path = Path(handle.name)
        con = sqlite3.connect(path)
        con.execute(
            """
            CREATE TABLE paper_trades (
                setup_id TEXT PRIMARY KEY,
                symbol TEXT,
                timeframe TEXT,
                direction TEXT,
                status TEXT,
                entry_price REAL,
                stop_price REAL,
                target_price REAL,
                opened_at TEXT,
                closed_at TEXT,
                exit_price REAL,
                result TEXT,
                result_r REAL,
                risk_dollars REAL,
                result_dollars REAL,
                guard_reason TEXT,
                updated_at TEXT
            )
            """
        )
        con.commit()
        return DashboardRepository(path), con, path

    def test_legacy_r_rows_get_normalized_display_dollars_without_entering_eval_ledger(self):
        import os
        from unittest.mock import patch as mock_patch

        repo, con, path = self._repo_db()
        try:
            closed = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc)
            con.execute(
                """
                INSERT INTO paper_trades (
                    setup_id,symbol,timeframe,direction,status,entry_price,stop_price,target_price,
                    opened_at,closed_at,exit_price,result,result_r,risk_dollars,result_dollars,guard_reason,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    'legacy','NQ','1m','bullish','CLOSED',100,99,103,
                    closed.isoformat(),closed.isoformat(),103,'WIN',1.39,None,None,None,closed.isoformat(),
                ),
            )
            con.commit()
            con.row_factory = sqlite3.Row
            with mock_patch.dict(os.environ, {'EVAL_RISK_PER_TRADE': '100'}):
                trades = repo.recent_trades(con)
                stats = repo.trade_stats(con, closed)
            self.assertAlmostEqual(trades[0]['display_result_dollars'], 139.0)
            self.assertAlmostEqual(stats['total_dollars'], 139.0)
            self.assertAlmostEqual(stats['today_dollars'], 139.0)
            # Persisted risk remains null, so the Evaluation Guard will not count legacy history.
            self.assertIsNone(con.execute("SELECT risk_dollars FROM paper_trades WHERE setup_id='legacy'").fetchone()[0])
        finally:
            con.close()
            path.unlink(missing_ok=True)

    def test_exact_modeled_result_dollars_override_legacy_fallback(self):
        repo, con, path = self._repo_db()
        try:
            closed = datetime(2026, 8, 13, 15, 0, tzinfo=timezone.utc)
            con.execute(
                """
                INSERT INTO paper_trades (
                    setup_id,symbol,timeframe,direction,status,entry_price,stop_price,target_price,
                    opened_at,closed_at,exit_price,result,result_r,risk_dollars,result_dollars,guard_reason,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    'new','ES','1m','bullish','CLOSED',100,99,102,
                    closed.isoformat(),closed.isoformat(),102,'WIN',2.0,75.0,150.0,'approved',closed.isoformat(),
                ),
            )
            con.commit()
            con.row_factory = sqlite3.Row
            trades = repo.recent_trades(con)
            stats = repo.trade_stats(con, closed)
            self.assertAlmostEqual(trades[0]['display_result_dollars'], 150.0)
            self.assertAlmostEqual(stats['total_dollars'], 150.0)
        finally:
            con.close()
            path.unlink(missing_ok=True)

    def test_daily_pnl_uses_replay_trading_day_not_wall_clock(self):
        import os
        from unittest.mock import patch as mock_patch

        repo, con, path = self._repo_db()
        try:
            # 03:30 UTC Aug 14 is still Aug 13 in New York.
            close_a = datetime(2026, 8, 14, 3, 30, tzinfo=timezone.utc)
            # 14:00 UTC Aug 14 is Aug 14 in New York and should not count for an Aug 13 replay reference.
            close_b = datetime(2026, 8, 14, 14, 0, tzinfo=timezone.utc)
            for setup_id, closed, r in [('a', close_a, 1.0), ('b', close_b, 2.0)]:
                con.execute(
                    """
                    INSERT INTO paper_trades (
                        setup_id,symbol,timeframe,direction,status,entry_price,stop_price,target_price,
                        opened_at,closed_at,exit_price,result,result_r,risk_dollars,result_dollars,guard_reason,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        setup_id,'NQ','1m','bullish','CLOSED',100,99,102,
                        closed.isoformat(),closed.isoformat(),102,'WIN',r,None,None,None,closed.isoformat(),
                    ),
                )
            con.commit()
            con.row_factory = sqlite3.Row
            replay_reference = datetime(2026, 8, 14, 3, 45, tzinfo=timezone.utc)
            with mock_patch.dict(os.environ, {'EVAL_RISK_PER_TRADE': '100'}):
                stats = repo.trade_stats(con, replay_reference)
            self.assertAlmostEqual(stats['today_r'], 1.0)
            self.assertAlmostEqual(stats['today_dollars'], 100.0)
            self.assertAlmostEqual(stats['total_dollars'], 300.0)
        finally:
            con.close()
            path.unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
