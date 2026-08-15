import unittest
from datetime import datetime, timedelta, timezone

from src.runtime.clock import MarketClock
from src.strategies.confluence import ConfluenceEngine
from src.strategies.ict import detect_smt, find_active_fvgs
from src.strategies.models import Candle

BASE = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)


def c(i, o, h, l, close, symbol="NQ", timeframe="1m"):
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=BASE + timedelta(minutes=i),
        close_time=BASE + timedelta(minutes=i + 1),
        open=o,
        high=h,
        low=l,
        close=close,
        ticks=10,
    )


class Operation4Tests(unittest.TestCase):
    def test_market_clock_detects_replay(self):
        clock = MarketClock()
        event = BASE
        ingest = BASE + timedelta(days=2)
        clock.update("NQ", event, ingest)
        self.assertEqual(clock.mode("NQ"), "REPLAY")

    def test_active_fvg_removed_after_full_mitigation(self):
        candles = [
            c(0, 100, 101, 99, 100),
            c(1, 100, 105, 100, 104),
            c(2, 103, 106, 102, 105),  # bullish FVG 101 -> 102
        ]
        self.assertTrue(find_active_fvgs(candles))
        candles.append(c(3, 105, 106, 100.5, 101))  # trades through lower edge
        self.assertFalse(find_active_fvgs(candles))

    def test_smt_uses_latest_shared_close_time(self):
        nq = []
        es = []
        for i in range(9):
            nq.append(c(i, 100, 101 + i * 0.1, 99, 100, symbol="NQ"))
            es.append(c(i, 200, 201 + i * 0.1, 199, 200, symbol="ES"))
        # Shared 10th candle: NQ breaks the prior high, ES does not.
        nq.append(c(9, 100, 105, 99, 104, symbol="NQ"))
        es.append(c(9, 200, 201.5, 199, 200, symbol="ES"))
        # Add an extra unsynchronized ES candle. Detector should still use the
        # latest shared close-time rather than simply comparing list tails.
        es.append(c(10, 200, 202, 199, 201, symbol="ES"))
        smt = detect_smt(nq, es, lookback=8)
        self.assertIsNotNone(smt)
        self.assertEqual(smt.direction, "bearish")
        self.assertEqual(smt.leader, "NQ")

    def test_confluence_exposes_warmup_diagnostic(self):
        engine = ConfluenceEngine()
        histories = {("NQ", "1m"): [c(0, 100, 101, 99, 100)]}
        self.assertIsNone(engine.on_candle("NQ", "1m", histories))
        diag = engine.diagnostic("NQ", "1m")
        self.assertEqual(diag["stage"], "WARMUP")
        self.assertEqual(diag["symbol"], "NQ")


if __name__ == "__main__":
    unittest.main()
