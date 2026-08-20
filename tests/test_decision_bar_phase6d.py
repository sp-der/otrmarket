from __future__ import annotations

import unittest

from src.research.decision_bar import enrich_decision_bar, summarize


class DecisionBarPhase6DTests(unittest.TestCase):
    def test_sequence_safe_entry_touch(self):
        rows = [{
            "event_time": "2026-06-01T10:01:00+00:00",
            "symbol": "NQ",
            "entry": 100.0,
            "stop": 99.0,
            "target": 102.0,
            "decision_progress_r": 0.8,
            "timeframe": "1m",
        }]
        bars = {"NQ": [{
            "close_time": "2026-06-01T10:01:00+00:00",
            "open": 100.5,
            "high": 101.0,
            "low": 99.75,
            "close": 100.8,
        }]}
        result = enrich_decision_bar(rows, bars)[0]
        self.assertTrue(result["decision_bar_found"])
        self.assertTrue(result["entry_touch"])
        self.assertFalse(result["stop_touch"])
        self.assertFalse(result["target_touch"])
        self.assertTrue(result["sequence_safe_entry_touch"])
        self.assertFalse(result["ambiguous_entry_touch"])

    def test_multiple_touches_are_marked_ambiguous(self):
        rows = [{
            "event_time": "2026-06-01T10:01:00+00:00",
            "symbol": "ES",
            "entry": 100.0,
            "stop": 99.0,
            "target": 102.0,
            "decision_progress_r": 1.0,
        }]
        bars = {"ES": [{
            "close_time": "2026-06-01T10:01:00+00:00",
            "open": 100.0,
            "high": 102.5,
            "low": 99.5,
            "close": 101.0,
        }]}
        result = enrich_decision_bar(rows, bars)[0]
        self.assertTrue(result["entry_touch"])
        self.assertTrue(result["target_touch"])
        self.assertTrue(result["ambiguous_entry_touch"])
        self.assertFalse(result["sequence_safe_entry_touch"])

    def test_summary_distinguishes_entry_left_behind(self):
        rows = [
            {"decision_bar_found": True, "entry_touch": True, "sequence_safe_entry_touch": True, "ambiguous_entry_touch": False, "target_touch": False, "stop_touch": False, "decision_progress_r": 0.5},
            {"decision_bar_found": True, "entry_touch": False, "sequence_safe_entry_touch": False, "ambiguous_entry_touch": False, "target_touch": False, "stop_touch": False, "decision_progress_r": 1.5},
            {"decision_bar_found": True, "entry_touch": False, "sequence_safe_entry_touch": False, "ambiguous_entry_touch": False, "target_touch": False, "stop_touch": False, "decision_progress_r": 2.0},
        ]
        result = summarize(rows)
        self.assertEqual(result["entry_touch_same_bar"], 1)
        self.assertEqual(result["sequence_safe_entry_touch"], 1)
        self.assertEqual(result["no_entry_touch_same_bar"], 2)
        self.assertAlmostEqual(result["no_entry_touch_same_bar_pct"], 66.66666666666666)


if __name__ == "__main__":
    unittest.main()
