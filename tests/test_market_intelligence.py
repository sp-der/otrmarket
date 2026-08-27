from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest

from src.strategies.market_intelligence import (
    build_market_map,
    detect_equal_liquidity,
    rejection_snapshot,
)
from src.strategies.models import Candle


BASE = datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc)


def candle(index: int, *, high: float, low: float, open_: float = 100.0, close: float = 100.5, timeframe: str = "1m"):
    start = BASE + timedelta(minutes=index)
    return Candle(
        symbol="NQ",
        timeframe=timeframe,
        open_time=start,
        close_time=start + timedelta(minutes=1),
        open=open_,
        high=high,
        low=low,
        close=close,
    )


class MarketIntelligenceTests(unittest.TestCase):
    def test_equal_high_liquidity_cluster_is_detected(self):
        highs = [101, 102, 105.00, 102, 101, 102, 105.05, 102, 101, 100.8, 100.6]
        candles = [candle(i, high=value, low=99.0) for i, value in enumerate(highs)]
        result = detect_equal_liquidity(candles)
        self.assertTrue(result["equal_highs"])
        self.assertGreaterEqual(result["equal_highs"][-1]["touches"], 2)
        self.assertAlmostEqual(result["equal_highs"][-1]["price"], 105.025, places=2)

    def test_lower_wick_rejection_is_bullish(self):
        item = candle(0, high=105.0, low=95.0, open_=100.0, close=104.0)
        result = rejection_snapshot([item])
        self.assertEqual(result["signal"], "bullish")
        self.assertGreaterEqual(result["lower_wick_fraction"], 0.35)

    def test_market_map_is_json_serializable(self):
        one_minute = []
        for i in range(40):
            base = 100 + i * 0.2
            one_minute.append(
                candle(
                    i,
                    high=base + 1.0,
                    low=base - 1.0,
                    open_=base - 0.2,
                    close=base + 0.3,
                )
            )
        histories = {("NQ", "1m"): one_minute}
        market_map = build_market_map("NQ", "1m", histories, one_minute[-1].close_time)
        encoded = json.dumps(market_map)
        self.assertIn("MARKET_INTELLIGENCE_1_0", encoded)
        self.assertEqual(market_map["symbol"], "NQ")


if __name__ == "__main__":
    unittest.main()
