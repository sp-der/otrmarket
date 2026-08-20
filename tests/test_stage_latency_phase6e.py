from __future__ import annotations

import unittest

from src.research.stage_latency import enrich_stage_latency, summarize_stage_latency


class StageLatencyPhase6ETests(unittest.TestCase):
    def test_episode_is_bounded_by_latest_scanner_reset(self):
        rows = [{
            "event_time": "2026-06-01T10:10:00+00:00",
            "symbol": "NQ",
            "timeframe": "1m",
            "strategy": "ICT_CONFLUENCE",
            "gate": "QUALITY",
        }]
        states = {("NQ", "1m", "ICT_CONFLUENCE"): [
            {"event_time": "2026-06-01T09:00:00+00:00", "stage": "WAIT_SIGNAL"},
            {"event_time": "2026-06-01T10:00:00+00:00", "stage": "WAIT_PD_ARRAY"},
            {"event_time": "2026-06-01T10:02:00+00:00", "stage": "WAIT_SIGNAL"},
            {"event_time": "2026-06-01T10:05:00+00:00", "stage": "WAIT_DISPLACEMENT"},
            {"event_time": "2026-06-01T10:08:00+00:00", "stage": "WAIT_ENTRY_FVG"},
        ]}
        result = enrich_stage_latency(rows, states)[0]
        self.assertTrue(result["stage_episode_found"])
        self.assertEqual(result["episode_minutes"], 8.0)
        self.assertEqual(result["pre_entry_minutes"], 6.0)
        self.assertEqual(result["entry_search_minutes"], 2.0)
        self.assertEqual(result["dominant_stage"], "WAIT_SIGNAL")

    def test_exact_strategy_linkage_does_not_mix_rejection_block_with_ict(self):
        rows = [{
            "event_time": "2026-06-01T10:10:00+00:00",
            "symbol": "NQ",
            "timeframe": "1m",
            "strategy": "ICT_CONFLUENCE",
            "gate": "QUALITY",
        }]
        states = {
            ("NQ", "1m", "REJECTION_BLOCK_10_10"): [
                {"event_time": "2026-05-20T10:00:00+00:00", "stage": "RB_WAIT_DISPLACEMENT"},
                {"event_time": "2026-06-01T10:09:00+00:00", "stage": "RB_WAIT_RETRACE"},
            ],
            ("NQ", "1m", "ICT_CONFLUENCE"): [
                {"event_time": "2026-06-01T10:05:00+00:00", "stage": "WAIT_SIGNAL"},
                {"event_time": "2026-06-01T10:08:00+00:00", "stage": "WAIT_DISPLACEMENT"},
            ],
        }
        result = enrich_stage_latency(rows, states)[0]
        self.assertTrue(result["stage_episode_found"])
        self.assertEqual(result["episode_minutes"], 5.0)
        self.assertNotIn("RB_WAIT_DISPLACEMENT", result["stage_dwell_minutes"])

    def test_rejection_block_idle_state_bounds_fresh_context(self):
        rows = [{
            "event_time": "2026-06-01T10:20:00+00:00",
            "symbol": "ES",
            "timeframe": "1m",
            "strategy": "REJECTION_BLOCK_10_10",
            "gate": "QUALITY",
        }]
        states = {("ES", "1m", "REJECTION_BLOCK_10_10"): [
            {"event_time": "2026-06-01T09:00:00+00:00", "stage": "RB_WAIT_DISPLACEMENT"},
            {"event_time": "2026-06-01T10:10:00+00:00", "stage": "RB_WAIT_SWEEP"},
            {"event_time": "2026-06-01T10:12:00+00:00", "stage": "RB_WAIT_DISPLACEMENT"},
            {"event_time": "2026-06-01T10:18:00+00:00", "stage": "RB_WAIT_RETRACE"},
        ]}
        result = enrich_stage_latency(rows, states)[0]
        self.assertTrue(result["stage_episode_found"])
        self.assertEqual(result["episode_minutes"], 8.0)
        self.assertEqual(result["pre_entry_minutes"], 6.0)
        self.assertEqual(result["entry_search_minutes"], 2.0)

    def test_entry_search_can_be_primary_latency_bucket(self):
        rows = [
            {
                "stage_episode_found": True,
                "exact_strategy_match": True,
                "episode_truncated_to_plausibility_bound": False,
                "episode_minutes": 10.0,
                "pre_entry_minutes": 2.0,
                "entry_search_minutes": 8.0,
                "dominant_stage": "WAIT_VALID_RR",
                "stage_dwell_minutes": {"WAIT_SIGNAL": 1.0, "WAIT_DISPLACEMENT": 1.0, "WAIT_VALID_RR": 8.0},
                "timeframe": "1m",
                "gate": "QUALITY",
                "symbol": "NQ",
                "strategy": "ICT_CONFLUENCE",
            },
            {
                "stage_episode_found": True,
                "exact_strategy_match": True,
                "episode_truncated_to_plausibility_bound": False,
                "episode_minutes": 8.0,
                "pre_entry_minutes": 2.0,
                "entry_search_minutes": 6.0,
                "dominant_stage": "WAIT_ENTRY_FVG",
                "stage_dwell_minutes": {"WAIT_SIGNAL": 2.0, "WAIT_ENTRY_FVG": 6.0},
                "timeframe": "1m",
                "gate": "QUALITY",
                "symbol": "ES",
                "strategy": "ICT_CONFLUENCE",
            },
        ]
        summary = summarize_stage_latency(rows)
        self.assertEqual(summary["primary_latency_bucket"], "ENTRY_FORMATION_OR_ENTRY_DEPTH")
        self.assertEqual(summary["dominant_stage_counts"]["WAIT_ENTRY_FVG"], 1)
        self.assertEqual(summary["dominant_stage_counts"]["WAIT_VALID_RR"], 1)

    def test_unmatched_episode_is_reported_without_inventing_latency(self):
        rows = [{
            "event_time": "2026-06-01T10:10:00+00:00",
            "symbol": "GC",
            "timeframe": "5m",
            "strategy": "ICT_CONFLUENCE",
        }]
        result = enrich_stage_latency(rows, {})[0]
        self.assertFalse(result["stage_episode_found"])
        self.assertFalse(result["exact_strategy_match"])
        self.assertIsNone(result["episode_minutes"])
        self.assertEqual(result["stage_dwell_minutes"], {})


if __name__ == "__main__":
    unittest.main()
