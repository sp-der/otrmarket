from __future__ import annotations

import unittest

from src.research.entry_depth import candidate_entries, evaluate_entry_variants, summarize_entry_depth


class EntryDepthPhase6GTests(unittest.TestCase):
    def _trace(self):
        return {
            "setup_id": "depth1",
            "event_time": "2026-06-01T10:00:00+00:00",
            "symbol": "NQ",
            "timeframe": "1m",
            "strategy_type": "MSS_REVERSAL",
            "direction": "bullish",
            "setup_grade": "A",
            "quality_score": 84,
            "planned_entry": 100.0,
            "stop": 99.0,
            "target": 104.5,
            "fvg": {"lower": 100.5, "upper": 101.5},
            "displacement": {"low": 99.5, "high": 102.5},
            "metadata": {"entry_type": "OTE_79"},
            "lifecycle": {
                "filled": False,
                "cancellation_reason": "STALE_AT_REGISTRATION",
            },
        }

    def test_candidate_entries_include_structural_shallow_variants(self):
        names = [name for name, _ in candidate_entries(self._trace())]
        self.assertEqual(names[0], "ORIGINAL")
        self.assertIn("FVG_SHALLOW_25", names)
        self.assertIn("FVG_MIDPOINT", names)
        self.assertIn("OTE_50", names)
        self.assertIn("OTE_62", names)
        self.assertIn("OTE_70_5", names)
        self.assertIn("OTE_79", names)

    def test_future_only_shallow_limit_can_rescue_stale_source(self):
        bars = [
            {
                "close_time": "2026-06-01T10:00:00+00:00",
                "open": 101.0, "high": 101.8, "low": 100.8, "close": 101.5,
            },
            {
                "close_time": "2026-06-01T10:01:00+00:00",
                "open": 101.5, "high": 102.2, "low": 101.2, "close": 102.0,
            },
            {
                "close_time": "2026-06-01T10:02:00+00:00",
                "open": 102.0, "high": 104.75, "low": 101.75, "close": 104.5,
            },
        ]
        rows = evaluate_entry_variants(self._trace(), bars, "2026-06-01T10:10:00+00:00")
        shallow = next(row for row in rows if row["variant"] == "FVG_SHALLOW_25")
        original = next(row for row in rows if row["variant"] == "ORIGINAL")
        self.assertTrue(shallow["eligible"])
        self.assertEqual(shallow["outcome"], "WIN")
        self.assertIsNotNone(shallow["entry_time"])
        self.assertEqual(original["outcome"], "NO_ENTRY_WITHIN_HORIZON")

    def test_marketable_candidate_is_rejected_as_chase(self):
        trace = self._trace()
        trace["fvg"] = {"lower": 101.75, "upper": 102.25}
        bars = [{
            "close_time": "2026-06-01T10:00:00+00:00",
            "open": 101.0, "high": 101.8, "low": 100.8, "close": 101.5,
        }]
        rows = evaluate_entry_variants(trace, bars, "2026-06-01T10:10:00+00:00")
        midpoint = next(row for row in rows if row["variant"] == "FVG_MIDPOINT")
        self.assertTrue(midpoint["marketable_chase"])
        self.assertFalse(midpoint["eligible"])
        self.assertEqual(midpoint["outcome"], "INELIGIBLE")

    def test_summary_reports_stale_rescue_without_account_feedback_claim(self):
        rows = [
            {
                "eligible": True, "entry_time": "t1", "resolved": True,
                "outcome": "WIN", "r_outcome": 1.5, "planned_r": 1.5,
                "original_cancel_reason": "TARGET_PROGRESS_75", "variant": "FVG_SHALLOW_25",
                "strategy": "MSS_REVERSAL", "timeframe": "1m", "symbol": "NQ",
                "original_entry_type": "OTE_79",
            },
            {
                "eligible": True, "entry_time": "t2", "resolved": True,
                "outcome": "LOSS", "r_outcome": -1.0, "planned_r": 1.5,
                "original_cancel_reason": "TARGET_PROGRESS_75", "variant": "FVG_SHALLOW_25",
                "strategy": "MSS_REVERSAL", "timeframe": "1m", "symbol": "ES",
                "original_entry_type": "OTE_79",
            },
        ]
        summary = summarize_entry_depth(rows)
        variant = summary["by_variant"]["FVG_SHALLOW_25"]
        self.assertEqual(variant["filled"], 2)
        self.assertEqual(variant["stale_source_rescued_to_entry"], 2)
        self.assertEqual(variant["wins"], 1)
        self.assertEqual(variant["losses"], 1)
        self.assertAlmostEqual(variant["diagnostic_net_r"], 0.5)


if __name__ == "__main__":
    unittest.main()
