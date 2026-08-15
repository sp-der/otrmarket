from datetime import datetime, timedelta, timezone
import unittest

from src.strategies.models import Candle
from src.strategies.rejection_block import RejectionBlockEngine


class Operation47RejectionBlockTests(unittest.TestCase):
    @staticmethod
    def candle(symbol, timeframe, start, minutes, open_, high, low, close):
        return Candle(
            symbol,
            timeframe,
            start,
            start + timedelta(minutes=minutes),
            open_,
            high,
            low,
            close,
        )

    def test_rejection_helpers_are_directional(self):
        engine = RejectionBlockEngine()
        start = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
        bullish = self.candle("NQ", "1m", start, 1, 100, 105, 90, 104)
        bearish = self.candle("NQ", "1m", start, 1, 100, 110, 95, 96)

        self.assertTrue(engine._clean_rejection(bullish, "bullish", 95)[0])
        self.assertTrue(engine._clean_rejection(bearish, "bearish", 105)[0])
        self.assertEqual("ES", engine._pair("NQ"))
        self.assertEqual("NQ", engine._pair("ES"))
        self.assertIsNone(engine._pair("GC"))

    def test_full_sequence_emits_only_10_of_10_and_preserves_three_r(self):
        engine = RejectionBlockEngine()
        start = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)

        # 15m HH/HL structure supplies a deterministic bullish directional bias.
        bias_mid = [
            100, 101, 105, 102, 99, 102, 107, 104,
            101, 104, 109, 106, 103, 105, 106, 107,
        ]
        bias = []
        for i, mid in enumerate(bias_mid):
            opened = start + timedelta(minutes=15 * i)
            bias.append(
                self.candle(
                    "GC", "15m", opened, 15,
                    mid, mid + 0.5, mid - 0.5, mid + 0.1,
                )
            )

        # Quiet 1m history has a confirmed 112 swing high and 103 swing low.
        mids = [
            106, 106.2, 106.4, 106.6, 106.8, 107, 107.2, 107.4,
            107.6, 108, 109, 110, 111, 110, 109, 107, 105, 103.4,
            103.7, 104, 104.1, 104.2,
        ]
        one_minute = []
        for i, mid in enumerate(mids):
            high = 112 if i == 12 else mid + 0.2
            low = 103 if i == 17 else mid - 0.2
            opened = start + timedelta(minutes=i)
            one_minute.append(
                self.candle(
                    "GC", "1m", opened, 1,
                    mid - 0.1, high, low, mid + 0.1,
                )
            )

        histories = {("GC", "1m"): one_minute, ("GC", "15m"): bias}

        # 1-5: bias, meaningful SSL, sweep, clean rejection, correlation checked.
        one_minute.append(
            self.candle(
                "GC", "1m", start + timedelta(minutes=22), 1,
                103.5, 105, 102.5, 104.5,
            )
        )
        self.assertIsNone(engine.on_candle("GC", "1m", histories))
        self.assertEqual(5, engine.diagnostic("GC", "1m")["checklist_score"])

        # 6-7: strong displacement makes an FVG and breaks the 112 swing high.
        one_minute.append(
            self.candle(
                "GC", "1m", start + timedelta(minutes=23), 1,
                105, 114, 105, 113.8,
            )
        )
        self.assertIsNone(engine.on_candle("GC", "1m", histories))
        self.assertEqual(7, engine.diagnostic("GC", "1m")["checklist_score"])

        # 8-10: a later retracement closes in the FVG, stop remains beyond the
        # rejection invalidation, and the old 112 high offers a true tick-safe 3R.
        one_minute.append(
            self.candle(
                "GC", "1m", start + timedelta(minutes=24), 1,
                108, 108.5, 104.6, 104.8,
            )
        )
        setup = engine.on_candle("GC", "1m", histories)

        self.assertIsNotNone(setup)
        self.assertEqual("rejection_block", setup.trigger_type)
        self.assertEqual("REJECTION_BLOCK_10_10", setup.metadata["strategy"])
        self.assertEqual(10, setup.metadata["checklist_score"])
        self.assertTrue(all(setup.metadata["checklist"].values()))
        self.assertGreaterEqual(setup.risk_reward, 3.0)
        self.assertEqual("RB_SETUP_READY", engine.diagnostic("GC", "1m")["stage"])


if __name__ == "__main__":
    unittest.main()
