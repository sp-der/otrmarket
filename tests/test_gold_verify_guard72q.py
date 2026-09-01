from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.strategies.gold_verify_guard72q import assess_gold_1m_verify


def setup(strategy="ICT_CONFLUENCE", direction="bullish", tier=None):
    metadata = {"strategy": strategy}
    if tier:
        metadata["execution_tier"] = tier
    return SimpleNamespace(
        symbol="GC",
        timeframe="1m",
        direction=direction,
        created_at=datetime(2026, 8, 11, 5, 10, tzinfo=timezone.utc),
        metadata=metadata,
    )


class GoldVerifyGuard72QTests(unittest.TestCase):
    def test_recovery_cannot_rescue_original_5m_context_rejection(self):
        candidate = setup(tier="ACCOUNT_RECOVERY_70")
        details = {
            "quality_grade": None,
            "context_timeframe": "5m",
            "higher_timeframe_bias": "bearish",
        }
        with patch(
            "src.strategies.gold_verify_guard72q.evaluate_ict_context",
            return_value=(False, "5m context is bearish while the setup is bullish.", details),
        ):
            allowed, reason, _ = assess_gold_1m_verify(candidate, {}, "VERIFY")
        self.assertFalse(allowed)
        self.assertIn("original 5m quality contract", reason)

    def test_aligned_b_plus_ict_remains_eligible(self):
        candidate = setup(tier="ONE_MINUTE_B_PLUS_REDUCED_69", direction="bearish")
        details = {
            "quality_grade": "B+",
            "context_timeframe": "5m",
            "higher_timeframe_bias": "bearish",
        }
        with patch(
            "src.strategies.gold_verify_guard72q.evaluate_ict_context",
            return_value=(True, "B+ aligned.", details),
        ):
            allowed, reason, _ = assess_gold_1m_verify(candidate, {}, "VERIFY")
        self.assertTrue(allowed, reason)
        self.assertIn("B+", reason)

    def test_dedicated_mss_reversal_lane_is_preserved(self):
        candidate = setup(strategy="MSS_REVERSAL", direction="bearish")
        allowed, reason, _ = assess_gold_1m_verify(candidate, {}, "VERIFY")
        self.assertTrue(allowed, reason)
        self.assertIn("reversal", reason.lower())

    def test_continuation_rearm_must_still_match_5m(self):
        candidate = setup(strategy="TREND_CONTINUATION_REARM", direction="bullish")
        candle = SimpleNamespace(close_time=candidate.created_at)
        histories = {("GC", "5m"): [candle]}
        with patch(
            "src.strategies.gold_verify_guard72q._structure_bias",
            return_value=("bearish", {"source": "test"}),
        ):
            allowed, reason, _ = assess_gold_1m_verify(candidate, histories, "VERIFY")
        self.assertFalse(allowed)
        self.assertIn("5m is bearish", reason)

    def test_eval_mode_is_not_changed_by_verify_firewall(self):
        candidate = setup(tier="COUNTERTREND_REVERSAL_64")
        allowed, _, _ = assess_gold_1m_verify(candidate, {}, "EVAL")
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
