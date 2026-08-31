from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.strategies.reversal_guard72 import assess_one_minute_reversal_context


class OneMinuteReversalGuard72Tests(unittest.TestCase):
    def setup(self, *, direction="bullish", trigger="liquidity_sweep", strategy="MSS_REVERSAL", timeframe="1m"):
        return SimpleNamespace(
            metadata={"strategy": strategy},
            timeframe=timeframe,
            direction=direction,
            trigger_type=trigger,
            symbol="GC",
            created_at=datetime(2026, 8, 18, 5, 0, tzinfo=timezone.utc),
        )

    @staticmethod
    def regimes(mapping):
        def fake(symbol, timeframe, created_at, histories):  # noqa: ARG001
            direction = mapping[timeframe]
            return {"direction": direction, "regime": f"TEST_{direction.upper()}"}
        return fake

    def test_non_1m_reversal_is_unchanged(self):
        allowed, reason, details = assess_one_minute_reversal_context(
            self.setup(strategy="ICT_CONFLUENCE"), {}
        )
        self.assertTrue(allowed)
        self.assertFalse(details["applicable"])
        self.assertIn("not applicable", reason)

    def test_market_structure_shift_alone_is_blocked(self):
        mapping = {"5m": "bullish", "15m": "bullish", "30m": "bullish"}
        with patch("src.strategies.reversal_guard72._causal_regime", side_effect=self.regimes(mapping)):
            allowed, reason, _ = assess_one_minute_reversal_context(
                self.setup(trigger="market_structure_shift"), {}
            )
        self.assertFalse(allowed)
        self.assertIn("liquidity sweep", reason)

    def test_five_minute_must_align(self):
        mapping = {"5m": "bearish", "15m": "bullish", "30m": "neutral"}
        with patch("src.strategies.reversal_guard72._causal_regime", side_effect=self.regimes(mapping)):
            allowed, reason, details = assess_one_minute_reversal_context(self.setup(), {})
        self.assertFalse(allowed)
        self.assertEqual(details["context_directions"]["5m"], "bearish")
        self.assertIn("5m context", reason)

    def test_larger_timeframe_opposition_blocks(self):
        mapping = {"5m": "bullish", "15m": "bullish", "30m": "bearish"}
        with patch("src.strategies.reversal_guard72._causal_regime", side_effect=self.regimes(mapping)):
            allowed, reason, _ = assess_one_minute_reversal_context(self.setup(), {})
        self.assertFalse(allowed)
        self.assertIn("30m context remains bearish", reason)

    def test_neutral_larger_context_needs_confirmation(self):
        mapping = {"5m": "bullish", "15m": "neutral", "30m": "neutral"}
        with patch("src.strategies.reversal_guard72._causal_regime", side_effect=self.regimes(mapping)):
            allowed, reason, _ = assess_one_minute_reversal_context(self.setup(), {})
        self.assertFalse(allowed)
        self.assertIn("neither 15m nor 30m", reason)

    def test_confirmed_mtf_reversal_passes(self):
        mapping = {"5m": "bullish", "15m": "bullish", "30m": "neutral"}
        with patch("src.strategies.reversal_guard72._causal_regime", side_effect=self.regimes(mapping)):
            allowed, reason, details = assess_one_minute_reversal_context(self.setup(), {})
        self.assertTrue(allowed)
        self.assertTrue(details["applicable"])
        self.assertIn("1m reversal confirmed", reason)


if __name__ == "__main__":
    unittest.main()
