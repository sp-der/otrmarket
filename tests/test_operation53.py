import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.execution.paper import PaperExecutor
from src.storage.intelligence import (
    ensure_intelligence_schema,
    intelligence_snapshot,
    upsert_trade_intelligence,
)


def setup(**overrides):
    created = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)
    values = dict(
        setup_id="intel-test",
        symbol="NQ",
        timeframe="1m",
        direction="bullish",
        created_at=created,
        trigger_type="smt",
        entry_price=100.0,
        stop_price=95.0,
        target_price=110.0,
        risk_reward=2.0,
        displacement=SimpleNamespace(body_ratio=2.0, range_ratio=1.6),
        metadata={
            "strategy": "ICT_CONFLUENCE",
            "entry_type": "FVG_MIDPOINT",
            "a_plus_context": {
                "context_timeframe": "5m",
                "higher_timeframe_bias": "bullish",
                "entry_fvg_age_bars": 1.0,
            },
        },
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class Operation53TradeIntelligenceTests(unittest.TestCase):
    def test_mfe_and_mae_are_tracked_in_r(self):
        executor = PaperExecutor()
        item = executor.register_setup(setup(), risk_dollars=250.0)
        t0 = item.setup.created_at

        executor.on_price("NQ", 100.0, t0 + timedelta(seconds=1))
        self.assertEqual(item.status, "OPEN")

        executor.on_price("NQ", 103.0, t0 + timedelta(seconds=2))
        executor.on_price("NQ", 98.0, t0 + timedelta(seconds=3))
        self.assertAlmostEqual(item.mfe_r, 0.6)
        self.assertAlmostEqual(item.mae_r, 0.4)

        executor.on_price("NQ", 95.0, t0 + timedelta(seconds=4))
        self.assertEqual(item.status, "CLOSED")
        self.assertEqual(item.result, "LOSS")
        self.assertAlmostEqual(item.mfe_r, 0.6)
        self.assertAlmostEqual(item.mae_r, 1.0)

    def test_48_shadow_can_preserve_pre_50_pending_behavior(self):
        executor = PaperExecutor(
            pending_expiry_enabled=False,
            stale_preentry_enabled=False,
        )
        item = executor.register_setup(setup(setup_id="shadow-old"), risk_dollars=250.0)
        t0 = item.setup.created_at

        executor.on_price("NQ", 101.0, t0 + timedelta(minutes=10))
        self.assertEqual(item.status, "PENDING")

        executor.on_price("NQ", 99.0, t0 + timedelta(minutes=11))
        self.assertEqual(item.status, "OPEN")

    def test_loss_forensics_classifies_instant_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "intel.db"
            connection = sqlite3.connect(db_path)
            ensure_intelligence_schema(connection)

            executor = PaperExecutor()
            item = executor.register_setup(setup(setup_id="instant"), risk_dollars=250.0)
            t0 = item.setup.created_at
            executor.on_price("NQ", 100.0, t0 + timedelta(seconds=1))
            executor.on_price("NQ", 95.0, t0 + timedelta(seconds=31))
            upsert_trade_intelligence(connection, item, (t0 + timedelta(seconds=31)).isoformat())
            connection.close()

            snapshot = intelligence_snapshot(db_path)
            self.assertEqual(snapshot["live"]["closed"], 1)
            self.assertEqual(snapshot["live"]["instant_stops"], 1)
            self.assertEqual(snapshot["recent_live"][0]["outcome_class"], "INSTANT_STOP")
            self.assertAlmostEqual(snapshot["recent_live"][0]["mae_r"], 1.0)


if __name__ == "__main__":
    unittest.main()
