from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from src.strategies.confluence import PendingContext
from src.strategies.flexible_confluence import FlexibleConfluenceEngine
from src.strategies.models import Candle, Displacement, FairValueGap, StrategySetup
from src.strategies.ote_entry_policy_67 import OTEEntryPolicy67
from src.storage.intelligence_67 import operation67_failure_tags


T0 = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def _fvg(lower=30.0, upper=40.0):
    return FairValueGap(
        symbol="GC",
        timeframe="1m",
        direction="bullish",
        lower=lower,
        upper=upper,
        formed_at=T0,
        candle1_time=T0 - timedelta(minutes=2),
        candle3_time=T0,
    )


def _context():
    displacement = Displacement(
        symbol="GC",
        timeframe="1m",
        direction="bullish",
        candle_time=T0 - timedelta(minutes=1),
        low=0.0,
        high=100.0,
        body_ratio=2.0,
        range_ratio=1.6,
    )
    return PendingContext(
        symbol="GC",
        timeframe="1m",
        direction="bullish",
        pd_array=_fvg(25.0, 35.0),
        stage="WAIT_VALID_RR",
        started_bar_count=10,
        stage_bar_count=10,
        trigger_type="liquidity_sweep",
        trigger_details={"swept_level": 10.0},
        swept_level=10.0,
        displacement=displacement,
        entry_fvg_seen=True,
        retracement_seen=True,
    )


def _candles():
    values = [
        (40.0, 43.0, 39.0, 42.0),
        (42.0, 45.0, 41.0, 44.0),
        (44.0, 47.0, 43.0, 46.0),
        (46.0, 55.0, 36.0, 52.0),
    ]
    result = []
    for index, (open_, high, low, close) in enumerate(values):
        close_time = T0 - timedelta(minutes=3 - index)
        result.append(
            Candle(
                symbol="GC",
                timeframe="1m",
                open_time=close_time - timedelta(minutes=1),
                close_time=close_time,
                open=open_,
                high=high,
                low=low,
                close=close,
            )
        )
    return result


def _setup(candidates):
    return StrategySetup(
        setup_id="op67-test",
        symbol="GC",
        timeframe="1m",
        direction="bullish",
        created_at=T0,
        pd_array=_fvg(25.0, 35.0),
        trigger_type="liquidity_sweep",
        trigger_details={"swept_level": 10.0},
        displacement=_context().displacement,
        entry_fvg=_fvg(),
        entry_price=float(candidates[0]["entry"]),
        stop_price=float(candidates[0]["stop"]),
        target_price=float(candidates[0]["target"]),
        risk_reward=float(candidates[0]["risk_reward"]),
        metadata={
            "entry_type": candidates[0]["entry_type"],
            "entry_candidates": candidates,
            "lifetime_risk_cap": 1.0,
            "risk_multiplier": 1.0,
        },
    )


class Operation67Tests(unittest.TestCase):
    def test_fib_template_and_deep_79_preference(self):
        policy = OTEEntryPolicy67()
        context = _context()
        candidates = [
            {"entry_type": "FVG_MIDPOINT", "entry": 38.2, "stop": 10.0, "target": 95.0, "risk_reward": 2.0, "valid": True, "details": {}},
            {"entry_type": "ORDER_BLOCK", "entry": 29.5, "stop": 10.0, "target": 95.0, "risk_reward": 2.5, "valid": True, "details": {}},
            {"entry_type": "OTE_79", "entry": 21.0, "stop": 10.0, "target": 95.0, "risk_reward": 3.0, "valid": True, "details": {"retracement": 0.79}},
        ]
        fake = _setup(candidates)
        with patch.object(FlexibleConfluenceEngine, "_build_setup", return_value=fake):
            result = policy._build_setup(_candles(), context, _fvg(), T0)
        self.assertIsNotNone(result)
        self.assertEqual(result.entry_price, 21.0)
        self.assertEqual(result.metadata["entry_zone"], "OTE_705_79")
        self.assertAlmostEqual(result.metadata["retracement_fraction"], 0.79)
        self.assertEqual(result.metadata["fib_profile"]["levels"], [0.0, 0.5, 0.618, 0.705, 0.79, 0.88, 1.0])
        self.assertFalse(result.metadata["aggressive_entry"])

    def test_shallow_618_requires_full_confirmation_and_caps_risk(self):
        policy = OTEEntryPolicy67()
        context = _context()
        candidates = [
            {"entry_type": "FVG_MIDPOINT", "entry": 38.2, "stop": 10.0, "target": 95.0, "risk_reward": 3.0, "valid": True, "details": {}},
        ]
        fake = _setup(candidates)
        with patch.object(FlexibleConfluenceEngine, "_build_setup", return_value=fake):
            result = policy._build_setup(_candles(), context, _fvg(), T0)
        self.assertIsNotNone(result)
        self.assertTrue(result.metadata["aggressive_entry"])
        self.assertTrue(result.metadata["aggressive_confirmation"]["all_confirmed"])
        self.assertAlmostEqual(result.metadata["risk_multiplier"], 0.60)
        self.assertEqual(result.metadata["entry_zone"], "SHALLOW_AGGRESSIVE")

    def test_shallow_entry_waits_for_ote_when_confirmation_missing(self):
        policy = OTEEntryPolicy67()
        context = _context()
        context.trigger_type = "smt"
        context.swept_level = None
        candidates = [
            {"entry_type": "FVG_MIDPOINT", "entry": 38.2, "stop": 10.0, "target": 95.0, "risk_reward": 3.0, "valid": True, "details": {}},
        ]
        fake = _setup(candidates)
        with patch.object(FlexibleConfluenceEngine, "_build_setup", return_value=fake):
            result = policy._build_setup(_candles(), context, _fvg(), T0)
        self.assertIsNone(result)
        self.assertIn("shallow aggressive entry", policy.risk_rejections[("GC", "1m")])
        self.assertIn("liquidity_sweep_complete", policy.risk_rejections[("GC", "1m")])

    def test_26_second_stop_activates_same_day_shallow_penalty_and_tags(self):
        policy = OTEEntryPolicy67()
        context = _context()
        candidates = [
            {"entry_type": "FVG_MIDPOINT", "entry": 38.2, "stop": 10.0, "target": 95.0, "risk_reward": 3.0, "valid": True, "details": {}},
        ]
        setup = _setup(candidates)
        setup.metadata.update(
            retracement_fraction=0.618,
            entry_zone="SHALLOW_AGGRESSIVE",
            aggressive_entry=True,
        )
        position = SimpleNamespace(
            setup=setup,
            result="LOSS",
            opened_at=T0,
            closed_at=T0 + timedelta(seconds=26),
        )
        policy.record_instant_stop(position)
        tags = operation67_failure_tags(position)
        self.assertIn("INSTANT_STOP", tags)
        self.assertIn("ENTRY_TOO_EARLY", tags)
        self.assertIn("PREMATURE_RETRACEMENT_FILL", tags)
        self.assertIn("NOISE_SWEEP_BEFORE_CONFIRMATION", tags)
        self.assertIn("GC_1M_BULLISH_INSTANT_STOP", tags)

        fake = _setup(candidates)
        with patch.object(FlexibleConfluenceEngine, "_build_setup", return_value=fake):
            result = policy._build_setup(_candles(), context, _fvg(), T0 + timedelta(minutes=10))
        self.assertIsNone(result)
        self.assertIn("instant-stop penalty", policy.risk_rejections[("GC", "1m")])


if __name__ == "__main__":
    unittest.main()
