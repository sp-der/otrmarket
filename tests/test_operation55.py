import unittest
from datetime import datetime, timedelta, timezone

from src.strategies.execution_quality import evaluate_ict_context
from src.strategies.models import Candle, Displacement, FairValueGap, StrategySetup


BASE = datetime(2026, 8, 17, 13, 30, tzinfo=timezone.utc)


def candles(timeframe, closes, minutes):
    rows = []
    opened = closes[0]
    for index, close in enumerate(closes):
        start = BASE - timedelta(minutes=minutes * (len(closes) - index))
        rows.append(Candle("NQ", timeframe, start, start + timedelta(minutes=minutes),
                           opened, max(opened, close) + 1, min(opened, close) - 1, close))
        opened = close
    return rows


def candidate(trigger="smt"):
    displacement_time = BASE - timedelta(minutes=2)
    fvg_time = BASE - timedelta(minutes=1)
    fvg = FairValueGap("NQ", "1m", "bullish", 104, 106, fvg_time,
                       fvg_time - timedelta(minutes=2), fvg_time)
    return StrategySetup(
        "op55", "NQ", "1m", "bullish", BASE, fvg, trigger, {},
        Displacement("NQ", "1m", "bullish", displacement_time, 100, 110, 2.0, 1.6),
        fvg, 105, 100, 115, 2.0,
        metadata={"strategy": "ICT_CONFLUENCE", "entry_type": "FVG_MIDPOINT"},
    )


class Operation55ChartIntelligenceTests(unittest.TestCase):
    def test_thirty_minute_narrative_is_scored(self):
        setup = candidate()
        histories = {
            ("NQ", "5m"): candles("5m", [100, 101, 102, 103, 105, 107, 109], 5),
            ("NQ", "30m"): candles("30m", [90, 92, 94, 96, 99, 103, 108], 30),
        }
        allowed, reason, details = evaluate_ict_context(setup, histories)
        self.assertTrue(allowed, reason)
        self.assertEqual(details["narrative_timeframe"], "30m")
        self.assertEqual(details["narrative_bias"], "bullish")
        self.assertGreaterEqual(details["quality_score"], 90)
        self.assertEqual(details["quality_grade"], "A+")

    def test_opposing_narrative_needs_smt_grade(self):
        histories = {
            ("NQ", "5m"): candles("5m", [100, 101, 102, 103, 105, 107, 109], 5),
            ("NQ", "30m"): candles("30m", [110, 108, 106, 104, 101, 98, 95], 30),
        }
        allowed, _, details = evaluate_ict_context(candidate("smt"), histories)
        self.assertTrue(allowed)
        self.assertEqual(details["quality_grade"], "A")
        allowed, reason, _ = evaluate_ict_context(candidate("liquidity_sweep"), histories)
        self.assertFalse(allowed)
        self.assertIn("require A or A+", reason)

    def test_narrative_warmup_does_not_silence_live_engine(self):
        setup = candidate()
        histories = {
            ("NQ", "5m"): candles("5m", [100, 101, 102, 103, 105, 107, 109], 5),
            ("NQ", "30m"): candles("30m", [100, 101], 30),
        }
        allowed, reason, details = evaluate_ict_context(setup, histories)
        self.assertTrue(allowed, reason)
        self.assertEqual(details["narrative_bias"], "unknown")


if __name__ == "__main__":
    unittest.main()
