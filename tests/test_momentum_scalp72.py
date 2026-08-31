from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.risk.eval_sizing72 import apply_eval_sizing72
from src.risk.momentum_scalp72 import evaluate_scalp_operating_mode72
from src.strategies.models import Candle, Displacement
from src.strategies.momentum_scalp72 import MomentumScalpEngine72, PendingMomentumScalp


UTC = timezone.utc


def candle(symbol, timeframe, minute, o, h, l, c):
    start = datetime(2026, 8, 19, 0, 0, tzinfo=UTC) + timedelta(minutes=minute)
    seconds = 300 if timeframe == "5m" else 60
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=start,
        close_time=start + timedelta(seconds=seconds),
        open=o,
        high=h,
        low=l,
        close=c,
        ticks=20,
    )


class MomentumScalpEngineTests(unittest.TestCase):
    def test_strong_impulse_can_build_shallow_first_pullback(self):
        engine = MomentumScalpEngine72()
        displacement = Displacement(
            symbol="GC",
            timeframe="1m",
            direction="bearish",
            candle_time=datetime(2026, 8, 19, 0, 30, tzinfo=UTC),
            low=100.0,
            high=110.0,
            body_ratio=2.10,
            range_ratio=1.60,
        )
        context = PendingMomentumScalp(
            direction="bearish",
            displacement=displacement,
            break_level=103.0,
            started_bar_count=30,
            five_minute_regime={"direction": "bearish", "regime": "TRENDING_DOWN"},
        )
        latest = candle("GC", "1m", 31, 105.2, 106.2, 104.8, 105.55)
        setup, reason = engine._build_setup("GC", "1m", [latest], context)
        self.assertIsNotNone(setup, reason)
        self.assertEqual(setup.metadata["strategy"], "MOMENTUM_SCALP")
        self.assertLessEqual(setup.metadata["signal_target_progress"], 0.35)
        self.assertGreaterEqual(setup.risk_reward, 1.25)
        self.assertEqual(setup.metadata["a_plus_context"]["context_timeframe"], "5m")

    def test_opposing_five_minute_context_blocks_arming(self):
        engine = MomentumScalpEngine72()
        five = []
        price = 100.0
        for idx in range(12):
            five.append(candle("GC", "5m", idx * 5, price, price + 2.0, price - 0.2, price + 1.5))
            price += 1.5
        allowed, regime, _ = engine._five_minute_context(
            {("GC", "5m"): five}, "GC", five[-1].close_time, "bearish"
        )
        self.assertFalse(allowed)
        self.assertEqual(regime["direction"], "bullish")


class MomentumScalpRiskTests(unittest.TestCase):
    def test_eval_sizing_hard_caps_scalp_risk(self):
        setup = SimpleNamespace(
            risk_reward=1.5,
            metadata={
                "strategy": "MOMENTUM_SCALP",
                "operating_mode": {"mode": "EVAL", "quality_grade": "A"},
            },
        )
        decision = SimpleNamespace(risk_dollars=500.0)
        risk, multiplier = apply_eval_sizing72(decision, setup, 500.0, 1.0)
        self.assertEqual(risk, 125.0)
        self.assertEqual(multiplier, 0.25)

    def test_scalps_use_separate_quota_from_primary_trade_rows(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE engine_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE paper_trades (
                setup_id TEXT PRIMARY KEY,
                symbol TEXT,
                status TEXT,
                result TEXT,
                opened_at TEXT,
                closed_at TEXT,
                result_dollars REAL
            );
            CREATE TABLE strategy_setups (
                setup_id TEXT PRIMARY KEY,
                payload_json TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?)",
            ("primary", "GC", "CLOSED", "WIN", "2026-08-19T00:10:00+00:00", "2026-08-19T00:20:00+00:00", 1000.0),
        )
        connection.execute(
            "INSERT INTO strategy_setups VALUES (?,?)",
            ("primary", '{"metadata":{"strategy":"ICT_CONFLUENCE"}}'),
        )
        connection.commit()

        setup = SimpleNamespace(
            symbol="GC",
            created_at=datetime(2026, 8, 19, 0, 30, tzinfo=UTC),
            metadata={},
        )
        allowed, reason, details = evaluate_scalp_operating_mode72(connection, setup)
        connection.close()
        self.assertTrue(allowed, reason)
        self.assertEqual(details["scalp_trades_today"], 0)
        self.assertEqual(details["scalp_trades_session"], 0)
        self.assertFalse(details["primary_eval_slots_consumed"])


if __name__ == "__main__":
    unittest.main()
