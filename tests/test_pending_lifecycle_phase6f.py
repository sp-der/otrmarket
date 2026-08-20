from __future__ import annotations

import unittest

from src.research.pending_lifecycle import collapse_order_states, summarize_lifecycles


class PendingLifecyclePhase6FTests(unittest.TestCase):
    def test_immediate_stale_registration_is_classified(self):
        setup_id = "abc123"
        states = [
            {
                "_sequence_no": 20,
                "setup_id": setup_id,
                "symbol": "NQ",
                "timeframe": "1m",
                "strategy_type": "MSS_REVERSAL",
                "direction": "bullish",
                "decision": "STALE_MOVE_BEFORE_ENTRY",
                "status": "INVALIDATED",
                "cancellation": {
                    "cancellation_reason": "STALE_AT_REGISTRATION",
                    "bars_elapsed": 0,
                    "configured_max_bars": 15,
                    "progress_to_target": 0.82,
                    "entry": 100.0,
                    "stop": 99.0,
                    "target": 102.0,
                },
            }
        ]
        setups = {
            setup_id: [
                {
                    "_sequence_no": 19,
                    "setup_id": setup_id,
                    "strategy_type": "MSS_REVERSAL",
                    "setup_grade": "A",
                    "quality_score": 84,
                    "metadata": {"entry_type": "FVG_MIDPOINT"},
                }
            ]
        }
        row = collapse_order_states(states, setups)[0]
        self.assertTrue(row["immediate_registration_stale"])
        self.assertFalse(row["filled"])
        self.assertEqual(row["grade"], "A")
        self.assertEqual(row["entry_type"], "FVG_MIDPOINT")
        self.assertEqual(row["progress_to_target"], 0.82)

    def test_filled_order_is_not_counted_as_never_filled(self):
        setup_id = "filled1"
        states = [
            {
                "_sequence_no": 10,
                "setup_id": setup_id,
                "symbol": "ES",
                "timeframe": "5m",
                "strategy_type": "ICT_CONFLUENCE",
                "decision": "PENDING",
                "status": "PENDING",
            },
            {
                "_sequence_no": 11,
                "setup_id": setup_id,
                "symbol": "ES",
                "timeframe": "5m",
                "strategy_type": "ICT_CONFLUENCE",
                "decision": "OPEN",
                "status": "OPEN",
            },
            {
                "_sequence_no": 12,
                "setup_id": setup_id,
                "symbol": "ES",
                "timeframe": "5m",
                "strategy_type": "ICT_CONFLUENCE",
                "decision": "WIN",
                "status": "CLOSED",
            },
        ]
        rows = collapse_order_states(states, {})
        summary = summarize_lifecycles(rows)
        self.assertEqual(summary["registered_orders"], 1)
        self.assertEqual(summary["filled_orders"], 1)
        self.assertEqual(summary["never_filled_orders"], 0)
        self.assertEqual(summary["cancelled_before_or_without_fill"], 0)

    def test_summary_separates_stale_expiry_and_stop_invalidation(self):
        rows = [
            {
                "registered": True,
                "filled": False,
                "cancelled": True,
                "immediate_registration_stale": True,
                "target_progress_stale": False,
                "pending_expired": False,
                "stop_breached_before_entry": False,
                "cancellation_reason": "STALE_AT_REGISTRATION",
                "progress_to_target": 0.80,
                "strategy": "MSS_REVERSAL",
                "timeframe": "1m",
                "symbol": "NQ",
                "grade": "A",
                "entry_type": "FVG_MIDPOINT",
            },
            {
                "registered": True,
                "filled": False,
                "cancelled": True,
                "immediate_registration_stale": False,
                "target_progress_stale": False,
                "pending_expired": True,
                "stop_breached_before_entry": False,
                "cancellation_reason": "PENDING_EXPIRED",
                "progress_to_target": 0.20,
                "strategy": "ICT_CONFLUENCE",
                "timeframe": "5m",
                "symbol": "ES",
                "grade": "A+",
                "entry_type": "OTE_79",
            },
            {
                "registered": True,
                "filled": False,
                "cancelled": True,
                "immediate_registration_stale": False,
                "target_progress_stale": False,
                "pending_expired": False,
                "stop_breached_before_entry": True,
                "cancellation_reason": "STOP_BREACHED_BEFORE_ENTRY",
                "progress_to_target": 0.0,
                "strategy": "ICT_CONFLUENCE",
                "timeframe": "1m",
                "symbol": "GC",
                "grade": "B+",
                "entry_type": "FVG_MIDPOINT",
            },
        ]
        summary = summarize_lifecycles(rows)
        self.assertEqual(summary["registered_orders"], 3)
        self.assertEqual(summary["immediate_registration_stale"], 1)
        self.assertEqual(summary["pending_expired"], 1)
        self.assertEqual(summary["stop_breached_before_entry"], 1)
        self.assertEqual(summary["cancellation_reasons"]["STALE_AT_REGISTRATION"], 1)


if __name__ == "__main__":
    unittest.main()
