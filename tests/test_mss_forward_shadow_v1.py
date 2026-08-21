from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.execution.paper import PaperPosition
from src.research.mss_forward_shadow import (
    BASELINE_PROFILE,
    CANDIDATE_PROFILE,
    MSSForwardShadowHarness,
    forward_shadow_snapshot,
    fvg_shallow_25_entry,
    marketable_chase,
)
from src.strategies.models import Displacement, FairValueGap, StrategySetup


NOW = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)


def make_setup(direction="bullish"):
    fvg = FairValueGap(
        symbol="NQ", timeframe="1m", direction=direction,
        lower=100.0, upper=102.0, formed_at=NOW,
        candle1_time=NOW, candle3_time=NOW,
    )
    displacement = Displacement(
        symbol="NQ", timeframe="1m", direction=direction,
        candle_time=NOW, low=99.0, high=103.0,
        body_ratio=2.0, range_ratio=1.6,
    )
    if direction == "bullish":
        entry, stop, target = 100.5, 99.0, 106.0
    else:
        entry, stop, target = 101.5, 103.0, 96.0
    return StrategySetup(
        setup_id=f"source_{direction}", symbol="NQ", timeframe="1m",
        direction=direction, created_at=NOW, pd_array=fvg,
        trigger_type="market_structure_shift", trigger_details={},
        displacement=displacement, entry_fvg=fvg,
        entry_price=entry, stop_price=stop, target_price=target,
        risk_reward=abs(target-entry)/abs(entry-stop),
        metadata={"strategy": "MSS_REVERSAL", "entry_type": "OTE_79"},
    )


class MSSForwardShadowV1Tests(unittest.TestCase):
    def test_shallow_fvg_entry_is_direction_aware(self):
        self.assertEqual(fvg_shallow_25_entry(make_setup("bullish")), 101.5)
        self.assertEqual(fvg_shallow_25_entry(make_setup("bearish")), 100.5)

    def test_marketable_candidate_is_rejected_without_hindsight_fill(self):
        self.assertTrue(marketable_chase("bullish", 101.5, ask=101.25))
        self.assertFalse(marketable_chase("bullish", 101.5, ask=102.0))
        self.assertTrue(marketable_chase("bearish", 100.5, bid=100.75))
        self.assertFalse(marketable_chase("bearish", 100.5, bid=100.0))

    def test_same_source_registers_matched_baseline_and_candidate_once(self):
        harness = MSSForwardShadowHarness()
        connection = sqlite3.connect(":memory:")
        try:
            setup = make_setup("bullish")
            live = PaperPosition(setup=setup, risk_dollars=100.0)
            harness.on_price(connection, "NQ", 103.0, 102.75, 103.0, NOW)
            self.assertTrue(harness.register_live_position(connection, live, NOW.isoformat()))
            self.assertFalse(harness.register_live_position(connection, live, NOW.isoformat()))
            pair = connection.execute(
                "SELECT candidate_eligible,candidate_entry FROM mss_forward_shadow_pairs"
            ).fetchone()
            self.assertEqual(pair[0], 1)
            self.assertEqual(pair[1], 101.5)
            profiles = {
                row[0] for row in connection.execute("SELECT profile FROM shadow_trades").fetchall()
            }
            self.assertEqual(profiles, {BASELINE_PROFILE, CANDIDATE_PROFILE})
        finally:
            connection.close()

    def test_candidate_can_fill_forward_without_granting_baseline_hindsight(self):
        harness = MSSForwardShadowHarness()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shadow.db"
            connection = sqlite3.connect(path)
            try:
                setup = make_setup("bullish")
                live = PaperPosition(setup=setup, risk_dollars=100.0)
                harness.on_price(connection, "NQ", 103.0, 102.75, 103.0, NOW)
                harness.register_live_position(connection, live, NOW.isoformat())
                harness.on_price(connection, "NQ", 101.5, 101.25, 101.5, NOW.replace(second=10))
                harness.on_price(connection, "NQ", 106.0, 105.75, 106.0, NOW.replace(second=20))
            finally:
                connection.close()
            snapshot = forward_shadow_snapshot(path)
            self.assertEqual(snapshot["matched_setups"], 1)
            self.assertEqual(snapshot["candidate_only_fills"], 1)
            self.assertEqual(snapshot["candidate"]["wins"], 1)
            self.assertEqual(snapshot["baseline"]["wins"], 0)
            self.assertFalse(snapshot["production_behavior_changed"])


if __name__ == "__main__":
    unittest.main()
