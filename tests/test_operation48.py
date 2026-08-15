import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.main_multi import _setup_risk
from src.strategies.confluence import PendingContext
from src.strategies.flexible_confluence import FlexibleConfluenceEngine
from src.strategies.models import Candle, Displacement, FairValueGap, SwingPoint


BASE = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)


def candle(i, o=100.0, h=101.0, l=99.0, close=100.5):
    return Candle(
        symbol="NQ",
        timeframe="1m",
        open_time=BASE + timedelta(minutes=i),
        close_time=BASE + timedelta(minutes=i + 1),
        open=o,
        high=h,
        low=l,
        close=close,
        ticks=10,
    )


def entry_fvg():
    t = BASE + timedelta(minutes=10)
    return FairValueGap(
        symbol="NQ",
        timeframe="1m",
        direction="bullish",
        lower=100.0,
        upper=101.0,
        formed_at=t,
        candle1_time=t - timedelta(minutes=2),
        candle3_time=t,
    )


def context(displacement_time=None):
    displacement_time = displacement_time or BASE + timedelta(minutes=6)
    return PendingContext(
        symbol="NQ",
        timeframe="1m",
        direction="bullish",
        pd_array=entry_fvg(),
        stage="WAIT_VALID_RR",
        started_bar_count=1,
        stage_bar_count=6,
        trigger_type="smt",
        trigger_details={"description": "test"},
        displacement=Displacement(
            symbol="NQ",
            timeframe="1m",
            direction="bullish",
            candle_time=displacement_time,
            low=95.0,
            high=105.0,
            body_ratio=2.0,
            range_ratio=1.8,
        ),
        entry_fvg_seen=True,
        retracement_seen=True,
    )


class Operation48FlexibleEntryTests(unittest.TestCase):
    def test_structural_one_to_one_plus_fvg_is_accepted_without_forcing_three_r(self):
        engine = FlexibleConfluenceEngine()
        ctx = context()
        history = [candle(i) for i in range(12)]
        swing_low = SwingPoint("NQ", "1m", "low", 99.0, BASE, 2)
        target = SwingPoint("NQ", "1m", "high", 103.0, BASE, 8)

        with patch(
            "src.strategies.flexible_confluence.detect_swings",
            return_value=[swing_low, target],
        ), patch(
            "src.strategies.flexible_confluence.nearest_target_swing",
            return_value=target,
        ):
            setup = engine._build_setup(history, ctx, entry_fvg(), BASE)

        self.assertIsNotNone(setup)
        self.assertEqual(setup.metadata["entry_type"], "FVG_MIDPOINT")
        self.assertGreaterEqual(setup.risk_reward, 1.0)
        self.assertLess(setup.risk_reward, 3.0)
        self.assertEqual(setup.target_price, 103.0)
        self.assertEqual(setup.metadata["target_rule"], "nearest valid opposing structural swing")

    def test_79_ote_is_fallback_when_fvg_midpoint_has_less_than_one_r(self):
        engine = FlexibleConfluenceEngine()
        ctx = context()
        history = [candle(i, 100.0, 101.0, 99.0, 100.75) for i in range(12)]
        swing_low = SwingPoint("NQ", "1m", "low", 95.0, BASE, 2)
        target = SwingPoint("NQ", "1m", "high", 104.0, BASE, 8)

        with patch(
            "src.strategies.flexible_confluence.detect_swings",
            return_value=[swing_low, target],
        ), patch(
            "src.strategies.flexible_confluence.nearest_target_swing",
            return_value=target,
        ):
            setup = engine._build_setup(history, ctx, entry_fvg(), BASE)

        self.assertIsNotNone(setup)
        self.assertEqual(setup.metadata["entry_type"], "OTE_79")
        self.assertAlmostEqual(setup.entry_price, 97.0)
        self.assertGreaterEqual(setup.risk_reward, 1.0)
        fvg_attempt = setup.metadata["entry_candidates"][0]
        self.assertEqual(fvg_attempt["entry_type"], "FVG_MIDPOINT")
        self.assertFalse(fvg_attempt["valid"])
        self.assertLess(fvg_attempt["risk_reward"], 1.0)

    def test_same_leg_order_block_can_rescue_geometry_after_ote_fails(self):
        # Displacement closes on candle index 6. Candle index 5 is the most
        # recent bearish candle and its body overlaps the 50-79% OTE zone.
        history = [candle(i, 98.0, 99.5, 97.5, 99.0) for i in range(5)]
        history.append(candle(5, 99.5, 100.0, 98.0, 98.5))
        history.append(candle(6, 98.5, 105.0, 98.0, 104.5))
        history.extend(candle(i, 101.0, 102.0, 100.0, 101.5) for i in range(7, 12))

        ctx = context(displacement_time=history[6].close_time)
        engine = FlexibleConfluenceEngine()
        swing_low = SwingPoint("NQ", "1m", "low", 98.0, BASE, 2)
        target = SwingPoint("NQ", "1m", "high", 102.25, BASE, 8)

        with patch(
            "src.strategies.flexible_confluence.detect_swings",
            return_value=[swing_low, target],
        ), patch(
            "src.strategies.flexible_confluence.nearest_target_swing",
            return_value=target,
        ):
            setup = engine._build_setup(history, ctx, entry_fvg(), BASE)

        self.assertIsNotNone(setup)
        self.assertEqual(setup.metadata["entry_type"], "ORDER_BLOCK")
        self.assertAlmostEqual(setup.entry_price, 99.0)
        attempts = {a["entry_type"]: a for a in setup.metadata["entry_candidates"]}
        self.assertFalse(attempts["FVG_MIDPOINT"]["valid"])
        self.assertFalse(attempts["OTE_79"]["valid"])
        self.assertTrue(attempts["ORDER_BLOCK"]["valid"])

    def test_replay_risk_tiers_never_exceed_guard_cap(self):
        engine = FlexibleConfluenceEngine()
        self.assertEqual(engine._risk_tier(1.10), ("RR_1_TO_1_5", 0.50))
        self.assertEqual(engine._risk_tier(1.70), ("RR_1_5_TO_2", 0.65))
        self.assertEqual(engine._risk_tier(2.40), ("RR_2_TO_3", 0.80))
        self.assertEqual(engine._risk_tier(3.00), ("RR_3_PLUS", 1.00))

        decision = SimpleNamespace(risk_dollars=250.0)
        setup = SimpleNamespace(metadata={"risk_multiplier": 0.50})
        self.assertEqual(_setup_risk(decision, setup), (125.0, 0.50))

        setup = SimpleNamespace(metadata={"risk_multiplier": 5.0})
        self.assertEqual(_setup_risk(decision, setup), (250.0, 1.00))


if __name__ == "__main__":
    unittest.main()
