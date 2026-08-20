from __future__ import annotations

import unittest

from src.research.missed_move import enrich_missed_moves, summarize_missed_rows


class MissedMovePhase6CTests(unittest.TestCase):
    def test_enrich_missed_move_calculates_latency_and_progress(self):
        rows = [{
            "outcome": "MISSED_MOVE_BEFORE_ENTRY",
            "event_time": "2026-06-01T10:00:00+00:00",
            "terminal_time": "2026-06-01T10:04:00+00:00",
            "symbol": "NQ",
            "direction": "LONG",
            "entry": 100.0,
            "stop": 99.0,
            "target": 102.0,
            "planned_r": 2.0,
            "gate": "QUALITY",
        }]
        bars = {"NQ": [
            {"close_time": "2026-06-01T10:00:00+00:00", "close": 100.75},
            {"close_time": "2026-06-01T10:01:00+00:00", "close": 101.0},
        ]}
        enriched = enrich_missed_moves(rows, bars)[0]
        self.assertEqual(enriched["minutes_to_target"], 4.0)
        self.assertEqual(enriched["decision_progress_r"], 0.75)
        self.assertEqual(enriched["decision_target_remaining_r"], 1.25)

    def test_short_progress_is_directional(self):
        rows = [{
            "outcome": "MISSED_MOVE_BEFORE_ENTRY",
            "event_time": "2026-06-01T10:00:00+00:00",
            "terminal_time": "2026-06-01T10:05:00+00:00",
            "symbol": "ES",
            "direction": "SHORT",
            "entry": 100.0,
            "stop": 101.0,
            "target": 98.0,
            "planned_r": 2.0,
        }]
        bars = {"ES": [{"close_time": "2026-06-01T10:00:00+00:00", "close": 99.25}]}
        enriched = enrich_missed_moves(rows, bars)[0]
        self.assertEqual(enriched["decision_progress_r"], 0.75)

    def test_summary_reports_speed_and_extension(self):
        rows = [
            {"minutes_to_target": 1.0, "decision_progress_r": 0.25},
            {"minutes_to_target": 4.0, "decision_progress_r": 0.75},
            {"minutes_to_target": 10.0, "decision_progress_r": 1.5},
            {"minutes_to_target": 20.0, "decision_progress_r": -0.25},
        ]
        summary = summarize_missed_rows(rows)
        self.assertEqual(summary["setups"], 4)
        self.assertEqual(summary["within_5m"], 2)
        self.assertEqual(summary["within_5m_pct"], 50.0)
        self.assertEqual(summary["already_beyond_entry"], 3)
        self.assertEqual(summary["already_1r_or_more"], 1)


if __name__ == "__main__":
    unittest.main()
