from __future__ import annotations

import unittest

from src.dashboard import server_72t


class Supervisor72TTests(unittest.TestCase):
    def test_supervisor_promotes_72t_engine(self):
        self.assertEqual(server_72t._promote_engine_72t(), "src.main_72t")

    def test_4h_is_context_only(self):
        # Lazy import prevents the inherited 7.2Q quality wrapper from mutating
        # module globals before older compatibility tests get to inspect them.
        from src import main_72t
        from src.strategies import candles as candle_module

        main_72t._install_4h_context_72t()
        self.assertEqual(candle_module.TIMEFRAME_SECONDS["4h"], 14400)
        self.assertIn("4h", main_72t.runtime.candles.timeframes)
        self.assertIsNone(main_72t.runtime.evaluate_strategy(None, "GC", "4h"))


if __name__ == "__main__":
    unittest.main()
