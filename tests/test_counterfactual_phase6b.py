from __future__ import annotations

import unittest

from src.research.counterfactual import classify_gate, evaluate_shadow, geometry, summarize


class CounterfactualPhase6BTests(unittest.TestCase):
    def test_gate_classification_prefers_specific_controls(self):
        self.assertEqual(
            classify_gate({"decision": "QUALITY_BLOCKED", "reason": "score"}),
            "QUALITY",
        )
        self.assertEqual(
            classify_gate({"decision": "GUARD_BLOCKED", "reason": "daily stop"}),
            "EVALUATION_GUARD",
        )
        self.assertEqual(
            classify_gate({"decision": "SESSION_BLOCKED", "reason": "closed"}),
            "SESSION",
        )

    def test_geometry_requires_directional_ordering(self):
        for direction in ("LONG", "bullish"):
            self.assertEqual(
                geometry(
                    {
                        "direction": direction,
                        "planned_entry": 100,
                        "stop": 99,
                        "target": 102,
                    }
                ),
                (100.0, 99.0, 102.0),
            )
        for direction in ("SHORT", "bearish"):
            self.assertEqual(
                geometry(
                    {
                        "direction": direction,
                        "planned_entry": 100,
                        "stop": 101,
                        "target": 98,
                    }
                ),
                (100.0, 101.0, 98.0),
            )
        self.assertIsNone(
            geometry(
                {
                    "direction": "bullish",
                    "planned_entry": 100,
                    "stop": 101,
                    "target": 102,
                }
            )
        )

    def test_shadow_win_and_stop_first_ambiguity(self):
        trace = {
            "event_type": "SETUP_DECISION",
            "event_time": "2026-06-01T10:00:00+00:00",
            "symbol": "NQ",
            "timeframe": "1m",
            "direction": "bullish",
            "planned_entry": 100,
            "stop": 99,
            "target": 102,
            "decision": "QUALITY_BLOCKED",
        }
        win = evaluate_shadow(
            trace,
            [
                {
                    "close_time": "2026-06-01T10:01:00+00:00",
                    "low": 99.5,
                    "high": 100.5,
                },
                {
                    "close_time": "2026-06-01T10:02:00+00:00",
                    "low": 100,
                    "high": 102.5,
                },
            ],
            "2026-06-01T11:00:00+00:00",
        )
        self.assertEqual(win["direction"], "LONG")
        self.assertEqual(win["outcome"], "WIN")

        ambiguous = evaluate_shadow(
            trace,
            [
                {
                    "close_time": "2026-06-01T10:01:00+00:00",
                    "low": 98.5,
                    "high": 102.5,
                }
            ],
            "2026-06-01T11:00:00+00:00",
        )
        self.assertEqual(ambiguous["outcome"], "LOSS_AMBIGUOUS_STOP_FIRST")

    def test_summary_never_promotes_small_samples(self):
        rows = [
            {
                "outcome": "WIN",
                "resolved": True,
                "r_outcome": 2.0,
                "gate": "QUALITY",
                "symbol": "NQ",
                "strategy": "ICT",
                "timeframe": "1m",
                "grade": "A",
            }
            for _ in range(3)
        ]
        result = summarize(rows)
        self.assertEqual(
            result["by_gate"]["QUALITY"]["research_verdict"],
            "INSUFFICIENT_SAMPLE",
        )


if __name__ == "__main__":
    unittest.main()
