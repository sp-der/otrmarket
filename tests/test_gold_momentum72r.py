from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.strategies.gold_momentum72r import GoldMomentumPullbackEngine72R, PendingGoldMomentum
from src.strategies.models import Candle, FairValueGap


START = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def candles(timeframe="5m", count=22, *, latest_low=100.0, latest_high=101.0, latest_close=100.5):
    minutes = 5 if timeframe == "5m" else 15
    output = []
    for idx in range(count):
        close_time = START + timedelta(minutes=minutes * (idx + 1))
        low = latest_low if idx == count - 1 else 99.0 + idx * 0.05
        high = latest_high if idx == count - 1 else low + 1.0
        close = latest_close if idx == count - 1 else low + 0.6
        output.append(Candle(
            symbol="GC", timeframe=timeframe,
            open_time=close_time - timedelta(minutes=minutes), close_time=close_time,
            open=low + 0.2, high=high, low=low, close=close, ticks=50,
        ))
    return output


class GoldMomentum72RTests(unittest.TestCase):
    def test_lane_is_verify_gc_5m_15m_only(self):
        engine = GoldMomentumPullbackEngine72R()
        histories = {("GC", "5m"): candles("5m")}
        self.assertIsNone(engine.on_candle("GC", "5m", histories, "EVAL"))
        self.assertIsNone(engine.on_candle("GC", "1m", {("GC", "1m"): candles("5m")}, "VERIFY"))
        self.assertIsNone(engine.on_candle("NQ", "5m", {("NQ", "5m"): candles("5m")}, "VERIFY"))

    def test_strong_aligned_sweep_arms_without_chasing(self):
        engine = GoldMomentumPullbackEngine72R()
        bars = candles("5m", latest_low=109.0, latest_high=112.0, latest_close=111.5)
        displacement = SimpleNamespace(
            direction="bullish", body_ratio=2.10, range_ratio=1.60,
            low=109.0, high=112.0, candle_time=bars[-1].close_time,
        )
        sweep = SimpleNamespace(direction="bullish", swept_level=108.5, swing_time=bars[-3].close_time)
        histories = {("GC", "5m"): bars}
        with patch("src.strategies.gold_momentum72r.detect_displacement", return_value=displacement), patch.object(
            engine, "_primary_bias", return_value=("15m", "bullish", {"source": "test"})
        ), patch.object(engine, "_recent_sweep", return_value=sweep), patch.object(
            engine, "_structure_break", return_value=(110.0, 108.0)
        ):
            setup = engine.on_candle("GC", "5m", histories, "VERIFY")
        self.assertIsNone(setup)
        self.assertIn(("GC", "5m"), engine.contexts)
        self.assertEqual(engine.diagnostic("GC", "5m")["stage"], "WAIT_ENTRY_FVG")

    def test_first_fvg_pullback_emits_candidate_for_normal_quality_gate(self):
        engine = GoldMomentumPullbackEngine72R(min_rr=1.5)
        bars = candles("5m", latest_low=105.0, latest_high=108.0, latest_close=107.0)
        displacement = SimpleNamespace(
            direction="bullish", body_ratio=2.30, range_ratio=1.70,
            low=100.0, high=110.0, candle_time=bars[-3].close_time,
        )
        engine.contexts[("GC", "5m")] = PendingGoldMomentum(
            direction="bullish", displacement=displacement,
            trigger_type="liquidity_sweep", trigger_details={"swept_level": 99.5},
            stop_anchor=100.0, started_bar_count=len(bars) - 2,
            started_at=bars[-3].close_time, context_timeframe="15m", context_bias="bullish",
        )
        fvg = FairValueGap(
            symbol="GC", timeframe="5m", direction="bullish", lower=105.0, upper=107.0,
            formed_at=bars[-2].close_time, candle1_time=bars[-4].close_time,
            candle3_time=bars[-2].close_time,
        )
        geometry = SimpleNamespace(valid=True, risk_reward=2.25)
        with patch.object(engine, "_primary_bias", return_value=("15m", "bullish", {})), patch.object(
            engine, "_fresh_fvg", return_value=fvg
        ), patch("src.strategies.gold_momentum72r.nearest_target_swing", return_value=SimpleNamespace(price=120.0)), patch(
            "src.strategies.gold_momentum72r.normalize_trade_prices", return_value=(106.0, 99.9, 120.0)
        ), patch("src.strategies.gold_momentum72r.validate_trade_geometry", return_value=geometry):
            setup = engine.on_candle("GC", "5m", {("GC", "5m"): bars}, "VERIFY")
        self.assertIsNotNone(setup)
        self.assertEqual(setup.metadata["strategy"], "GOLD_MOMENTUM_PULLBACK_72R")
        self.assertTrue(setup.metadata["no_chase"])
        self.assertGreaterEqual(setup.risk_reward, 1.5)
        self.assertNotIn(("GC", "5m"), engine.contexts)

    def test_context_dies_if_primary_htf_flips(self):
        engine = GoldMomentumPullbackEngine72R()
        bars = candles("15m", latest_low=95.0, latest_high=98.0, latest_close=96.0)
        displacement = SimpleNamespace(
            direction="bearish", body_ratio=2.4, range_ratio=1.8,
            low=95.0, high=105.0, candle_time=bars[-2].close_time,
        )
        engine.contexts[("GC", "15m")] = PendingGoldMomentum(
            direction="bearish", displacement=displacement,
            trigger_type="market_structure_shift", trigger_details={"break_level": 99.0},
            stop_anchor=105.0, started_bar_count=len(bars) - 1,
            started_at=bars[-2].close_time, context_timeframe="1h", context_bias="bearish",
        )
        with patch.object(engine, "_primary_bias", return_value=("1h", "bullish", {})):
            setup = engine.on_candle("GC", "15m", {("GC", "15m"): bars}, "VERIFY")
        self.assertIsNone(setup)
        self.assertNotIn(("GC", "15m"), engine.contexts)
        self.assertEqual(engine.diagnostic("GC", "15m")["stage"], "EXPIRED")

    def test_mss_only_requires_extra_impulse_strength(self):
        engine = GoldMomentumPullbackEngine72R()
        bars = candles("5m", latest_low=90.0, latest_high=95.0, latest_close=91.0)
        displacement = SimpleNamespace(
            direction="bearish", body_ratio=2.05, range_ratio=1.55,
            low=90.0, high=95.0, candle_time=bars[-1].close_time,
        )
        with patch("src.strategies.gold_momentum72r.detect_displacement", return_value=displacement), patch.object(
            engine, "_primary_bias", return_value=("15m", "bearish", {})
        ), patch.object(engine, "_recent_sweep", return_value=None), patch.object(
            engine, "_structure_break", return_value=(92.0, 96.0)
        ):
            setup = engine.on_candle("GC", "5m", {("GC", "5m"): bars}, "VERIFY")
        self.assertIsNone(setup)
        self.assertNotIn(("GC", "5m"), engine.contexts)
        self.assertIn("MSS-only", engine.diagnostic("GC", "5m")["note"])


if __name__ == "__main__":
    unittest.main()
