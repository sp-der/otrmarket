from __future__ import annotations

import unittest

from src.execution.paper import CANNOT_SIZE_RESULT
from src.research.lab_v01 import (
    EXPIRED_BEFORE_ENTRY,
    INVALIDATED_BEFORE_ENTRY,
    STALE_MOVE_BEFORE_ENTRY,
    fill_rate_metrics,
    fill_rate_segments,
)


def _row(
    *,
    status: str,
    result: str | None = None,
    opened_at: str | None = None,
    setup_family: str = "FVG",
    grade: str = "A",
    timeframe: str = "5m",
    mfe_r: float | None = None,
    mae_r: float | None = None,
    hold_seconds: float | None = None,
) -> dict:
    return {
        "status": status,
        "result": result,
        "opened_at": opened_at,
        "setup_family": setup_family,
        "grade": grade,
        "timeframe": timeframe,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "hold_seconds": hold_seconds,
    }


def _mixed_funnel_rows() -> list[dict]:
    return [
        _row(status="CLOSED", result="WIN", opened_at="t1", setup_family="FVG", mfe_r=2.0, mae_r=0.2, hold_seconds=300),
        _row(status="CLOSED", result="WIN", opened_at="t2", setup_family="OTE", mfe_r=1.8, mae_r=0.1, hold_seconds=400),
        _row(status="CLOSED", result="LOSS", opened_at="t3", setup_family="FVG", mfe_r=0.4, mae_r=1.0, hold_seconds=200),
        _row(status="OPEN", opened_at="t4", setup_family="FVG", mfe_r=0.6, mae_r=0.3),
        _row(status="PENDING", setup_family="OTE"),
        _row(status="INVALIDATED", result=EXPIRED_BEFORE_ENTRY, setup_family="FVG"),
        _row(status="INVALIDATED", result=STALE_MOVE_BEFORE_ENTRY, setup_family="OTE"),
        _row(status="INVALIDATED", result=INVALIDATED_BEFORE_ENTRY, setup_family="FVG"),
        _row(status="INVALIDATED", result=CANNOT_SIZE_RESULT, setup_family="ORDER_BLOCK"),
    ]


class FillRateMetricsTests(unittest.TestCase):
    def test_correct_fill_rate_over_mixed_funnel(self):
        rows = _mixed_funnel_rows()
        metrics = fill_rate_metrics(rows)

        self.assertEqual(metrics["registered"], 9)
        self.assertEqual(metrics["filled"], 4)  # 3 CLOSED + 1 OPEN all have opened_at
        self.assertAlmostEqual(metrics["fill_rate"], 4 / 9, places=4)
        self.assertEqual(metrics["expired_before_entry"], 1)
        self.assertEqual(metrics["stale_move_before_entry"], 1)
        self.assertEqual(metrics["invalidated_before_entry"], 1)
        self.assertEqual(metrics["cannot_size_mgc"], 1)
        self.assertEqual(metrics["pending"], 1)
        self.assertEqual(metrics["open"], 1)
        self.assertEqual(metrics["closed"], 3)

    def test_invalidated_rows_are_never_counted_as_wins_or_losses(self):
        rows = _mixed_funnel_rows()
        metrics = fill_rate_metrics(rows)

        self.assertEqual(metrics["wins"], 2)
        self.assertEqual(metrics["losses"], 1)
        # 4 INVALIDATED-status rows exist (expired/stale/invalidated/cannot-size);
        # none of them should leak into wins/losses.
        self.assertEqual(metrics["wins"] + metrics["losses"], metrics["closed"])

    def test_avg_mfe_mae_and_hold_time_only_use_filled_samples(self):
        rows = _mixed_funnel_rows()
        metrics = fill_rate_metrics(rows)

        # 4 filled rows have mfe_r/mae_r; only the 3 CLOSED ones have hold_seconds.
        self.assertEqual(metrics["mfe_mae_samples"], 4)
        self.assertEqual(metrics["hold_time_samples"], 3)
        self.assertAlmostEqual(metrics["avg_mfe_r"], (2.0 + 1.8 + 0.4 + 0.6) / 4, places=6)
        self.assertAlmostEqual(metrics["avg_hold_seconds"], (300 + 400 + 200) / 3, places=6)

    def test_no_divide_by_zero_on_empty_input(self):
        metrics = fill_rate_metrics([])

        self.assertEqual(metrics["registered"], 0)
        self.assertIsNone(metrics["fill_rate"])
        self.assertIsNone(metrics["avg_mfe_r"])
        self.assertIsNone(metrics["avg_mae_r"])
        self.assertIsNone(metrics["avg_hold_seconds"])

    def test_no_divide_by_zero_when_nothing_ever_fills(self):
        rows = [
            _row(status="INVALIDATED", result=EXPIRED_BEFORE_ENTRY),
            _row(status="PENDING"),
        ]
        metrics = fill_rate_metrics(rows)

        self.assertEqual(metrics["registered"], 2)
        self.assertEqual(metrics["filled"], 0)
        self.assertEqual(metrics["fill_rate"], 0.0)
        self.assertIsNone(metrics["avg_mfe_r"])

    def test_segmentation_partitions_registered_rows_by_field(self):
        rows = _mixed_funnel_rows()
        segments = fill_rate_segments(rows, "setup_family")
        by_family = {item["setup_family"]: item for item in segments}

        self.assertEqual(set(by_family), {"FVG", "OTE", "ORDER_BLOCK"})
        self.assertEqual(by_family["FVG"]["registered"], 5)
        self.assertEqual(by_family["OTE"]["registered"], 3)
        self.assertEqual(by_family["ORDER_BLOCK"]["registered"], 1)
        self.assertEqual(by_family["ORDER_BLOCK"]["cannot_size_mgc"], 1)
        # Every row is accounted for exactly once across segments.
        self.assertEqual(sum(item["registered"] for item in by_family.values()), len(rows))

    def test_segmentation_never_raises_on_missing_field(self):
        rows = [_row(status="CLOSED", result="WIN", opened_at="t1")]
        for row in rows:
            row.pop("timeframe")
        segments = fill_rate_segments(rows, "timeframe")
        self.assertEqual(segments[0]["timeframe"], "UNKNOWN")
        self.assertEqual(segments[0]["registered"], 1)


if __name__ == "__main__":
    unittest.main()
