from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
import unittest
from unittest.mock import patch

from src.storage import learning as base_learning
from src.storage.learning_71 import observe_market_opportunity_71


NOW = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)


class Learning71Tests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        base_learning.ensure_learning_schema(self.connection)

    def tearDown(self):
        self.connection.close()

    @staticmethod
    def _market_map():
        return {
            "profile": "MARKET_INTELLIGENCE_1_0",
            "symbol": "NQ",
            "execution_timeframe": "5m",
            "market_time": NOW.isoformat(),
            "pair_smt": {"direction": "bullish", "leader": "NQ", "laggard": "ES"},
            "session_liquidity": {"session": "NEW_YORK_AM"},
            "timeframes": {
                "5m": {
                    "structure": {"direction": "bullish"},
                    "dealing_range": {"zone": "discount"},
                    "equal_liquidity": {
                        "equal_highs": [{"price": 20100.0, "touches": 2}],
                        "equal_lows": [],
                    },
                    "rejection": {"signal": "bullish"},
                    "fvgs": {
                        "active": [{"direction": "bullish"}],
                        "inverse": [{"direction": "bullish", "retested": True}],
                    },
                    "order_blocks": {
                        "active": [{"direction": "bullish"}],
                        "breaker_candidates": [{"direction": "bullish"}],
                    },
                },
                "15m": {"structure": {"direction": "bullish"}},
                "30m": {"structure": {"direction": "bullish"}},
                "1h": {"structure": {"direction": "bullish"}},
            },
        }

    def _fake_base_lesson(self, connection, symbol, timeframe, histories):
        lesson_id = "lesson71"
        features = {"liquidity_sweep": True, "fvg": True}
        connection.execute(
            """
            INSERT INTO market_lessons (
                lesson_id,symbol,timeframe,direction,started_at,ended_at,move_points,
                threshold_points,setup_found,setup_status,block_reason,features_json,summary,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                lesson_id,
                symbol,
                timeframe,
                "bullish",
                NOW.isoformat(),
                NOW.isoformat(),
                85.0,
                30.0,
                1,
                "CLOSED",
                None,
                json.dumps(features),
                "Bullish NQ move.",
                NOW.isoformat(),
            ),
        )
        connection.commit()
        return {"lesson_id": lesson_id, "summary": "Bullish NQ move.", "features": features}

    def test_lesson_is_enriched_without_replacing_base_features(self):
        with patch(
            "src.storage.learning_71.base_learning.observe_market_opportunity",
            side_effect=self._fake_base_lesson,
        ), patch(
            "src.storage.learning_71.build_market_map",
            return_value=self._market_map(),
        ):
            lesson = observe_market_opportunity_71(
                self.connection,
                "NQ",
                "5m",
                {},
            )

        row = self.connection.execute(
            "SELECT features_json, summary FROM market_lessons WHERE lesson_id='lesson71'"
        ).fetchone()
        features = json.loads(row[0])
        self.assertTrue(features["liquidity_sweep"])
        intelligence = features["market_intelligence"]
        self.assertEqual(intelligence["dealing_range_zone"], "discount")
        self.assertTrue(intelligence["target_equal_liquidity"])
        self.assertTrue(intelligence["same_direction_inverse_fvg_retest"])
        self.assertTrue(intelligence["same_direction_order_block"])
        self.assertTrue(intelligence["same_direction_breaker_candidate"])
        self.assertIn("Market map:", row[1])
        self.assertIn("MTF_STRUCTURE_ALIGNED", lesson["market_intelligence_features"])

        stats = {
            row[0]: row[1]
            for row in self.connection.execute(
                "SELECT feature, lesson_hits FROM learning_feature_stats"
            ).fetchall()
        }
        self.assertEqual(stats["FAVORABLE_DEALING_RANGE"], 1)
        self.assertEqual(stats["TARGET_EQUAL_LIQUIDITY"], 1)
        self.assertEqual(stats["INVERSE_FVG_RETEST_ALIGNED"], 1)
        self.assertEqual(stats["ORDER_BLOCK_ALIGNED"], 1)
        self.assertEqual(stats["BREAKER_CANDIDATE_ALIGNED"], 1)
        self.assertEqual(stats["REJECTION_ALIGNED"], 1)


if __name__ == "__main__":
    unittest.main()
