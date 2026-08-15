from datetime import datetime, timedelta, timezone
import unittest

from src.risk.evaluation import EvaluationConfig
from src.strategies.candles import CandleBuilder
from src.strategies.models import Candle


class Operation46ReplayTests(unittest.TestCase):
    def test_rewind_discards_future_history_and_active_bucket(self):
        builder = CandleBuilder(timeframes=("1m",))
        start = datetime(2026, 8, 14, 14, 30, tzinfo=timezone.utc)
        builder.seed_history(
            [
                Candle("NQ", "1m", start, start + timedelta(minutes=1), 1, 2, 1, 2, 2),
                Candle("NQ", "1m", start + timedelta(minutes=1), start + timedelta(minutes=2), 2, 3, 2, 3, 2),
            ]
        )
        builder.update("NQ", 3.0, start + timedelta(minutes=3))

        self.assertTrue(builder.has_future_history("NQ", start + timedelta(minutes=1)))

        builder.rewind_symbol("NQ", start + timedelta(minutes=1))

        self.assertNotIn(("NQ", "1m"), builder.current)
        history = builder.get_history("NQ", "1m")
        self.assertEqual(1, len(history))
        self.assertLessEqual(history[-1].close_time, start + timedelta(minutes=1))
        self.assertFalse(builder.has_future_history("NQ", start + timedelta(minutes=1)))

    def test_capital_plan_defaults_are_250_risk_and_750_daily_stop(self):
        config = EvaluationConfig()
        self.assertEqual(250.0, config.risk_per_trade)
        self.assertEqual(250.0, config.min_risk_per_trade)
        self.assertEqual(750.0, config.internal_daily_stop)


if __name__ == "__main__":
    unittest.main()
