from __future__ import annotations

import sqlite3
import unittest

from src.research import live_training72t


class TrainingLab72TTests(unittest.TestCase):
    def test_training_schema_is_persistent_and_run_scoped(self):
        connection = sqlite3.connect(":memory:")
        try:
            # Minimal base tables required by the existing learning/intelligence schemas.
            live_training72t._ensure_training_schema(connection)
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            self.assertIn("training_decisions_72t", tables)
            self.assertIn("training_trades_72t", tables)
            self.assertIn("training_trade_metrics_72t", tables)
            self.assertIn("training_counterfactuals_72t", tables)
            self.assertIn("training_shadow_72t", tables)
        finally:
            connection.close()

    def test_shadow_ranker_shrinks_small_samples(self):
        rows = [
            {
                "timeframe": "5m",
                "strategy": "ICT_CONFLUENCE",
                "result": "WIN",
                "result_r": 3.0,
                "mfe_r": 3.2,
                "mae_r": -0.2,
            }
        ]
        ranked = live_training72t._shadow_ranker(rows)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["status"], "COLLECTING")
        self.assertLess(ranked[0]["confidence"], 10)
        self.assertLess(ranked[0]["evidence_score"], 60)

    def test_walk_forward_waits_for_enough_outcomes(self):
        rows = [
            {"timeframe": "5m", "strategy": "ICT_CONFLUENCE", "result_r": 1.0}
            for _ in range(10)
        ]
        result = live_training72t._walk_forward(rows)
        self.assertEqual(result["status"], "COLLECTING")
        self.assertEqual(result["minimum_sample"], 20)


if __name__ == "__main__":
    unittest.main()
