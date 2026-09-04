from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.otr8.regime import GoldRegimeEngine80
from src.strategies.models import Candle


class Regime80Tests(unittest.TestCase):
    def _candles(self, timeframe: str, values: list[tuple[float, float]]):
        start = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        result = []
        for idx, (open_price, close_price) in enumerate(values):
            opened = start + timedelta(minutes=idx)
            high = max(open_price, close_price) + 0.25
            low = min(open_price, close_price) - 0.25
            result.append(Candle("GC", timeframe, opened, opened + timedelta(minutes=1), open_price, high, low, close_price, 10))
        return result

    def test_trend_expansion_with_higher_timeframe_alignment(self):
        rising = [(3500 + i * 2.0, 3501.6 + i * 2.0) for i in range(24)]
        histories = {("GC", "5m"): self._candles("5m", rising)}
        for tf in ("15m", "1h", "4h"):
            histories[("GC", tf)] = self._candles(tf, [(3500 + i * 3.0, 3502 + i * 3.0) for i in range(12)])
        regime = GoldRegimeEngine80().classify(histories, "GC", "5m")
        self.assertEqual(regime.regime, "TREND_EXPANSION")
        self.assertEqual(regime.direction, "bullish")
        self.assertEqual(regime.higher_timeframe_direction, "bullish")

    def test_overlapping_alternation_is_chop(self):
        alternating = []
        for i in range(24):
            alternating.append((3500.0, 3500.6 if i % 2 == 0 else 3499.4))
        histories = {("GC", "5m"): self._candles("5m", alternating)}
        regime = GoldRegimeEngine80().classify(histories, "GC", "5m")
        self.assertEqual(regime.regime, "CHOP")
        self.assertEqual(regime.direction, "neutral")


if __name__ == "__main__":
    unittest.main()
