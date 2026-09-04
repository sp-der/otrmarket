from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.otr8.arbiter import SetupArbiter80
from src.otr8.models import RegimeSnapshot80


class Arbiter80Tests(unittest.TestCase):
    def _setup(self, setup_id, strategy, rr):
        metadata = {"strategy": strategy, "a_plus_context": {"quality_grade": "A"}}
        if strategy == "REJECTION_BLOCK_10_10":
            metadata.update(checklist_score=10, checklist_total=10)
        return SimpleNamespace(
            setup_id=setup_id,
            timeframe="5m",
            direction="bullish",
            risk_reward=rr,
            trigger_type="liquidity_sweep",
            metadata=metadata,
        )

    def test_quality_context_can_beat_rejection_block_priority(self):
        ict = self._setup("ict", "ICT_CONFLUENCE", 2.2)
        rb = self._setup("rb", "REJECTION_BLOCK_10_10", 3.5)

        def narrative(setup, histories):
            score = 95 if setup.setup_id == "ict" else 70
            return {
                "score": score,
                "grade": "A",
                "market_map": {"timeframes": {"4h": {"structure": {"direction": "bullish"}}}},
            }

        regime = RegimeSnapshot80(
            symbol="GC",
            timeframe="5m",
            regime="TREND_EXPANSION",
            direction="bullish",
            confidence=86,
            directional_efficiency=0.7,
            range_expansion=1.5,
            overlap_ratio=0.2,
            alternation_ratio=0.2,
            higher_timeframe_direction="bullish",
            legacy_regime="TRENDING_UP",
        )
        arbiter = SetupArbiter80(narrative_fn=narrative)
        chosen, assessments = arbiter.choose(
            [rb, ict],
            {},
            {"ict": regime, "rb": regime},
        )
        self.assertEqual(chosen.setup_id, "ict")
        self.assertTrue(ict.metadata["setup_arbiter_80"]["selected"])
        self.assertFalse(rb.metadata["setup_arbiter_80"]["selected"])
        self.assertEqual(len(assessments), 2)


if __name__ == "__main__":
    unittest.main()
