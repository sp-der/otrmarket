from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Operation65RegressionTests(unittest.TestCase):
    def test_intrabar_runtime_contracts_in_isolated_process(self):
        code = textwrap.dedent(
            r'''
            from datetime import datetime, timedelta, timezone

            import src.main_65 as op65
            from src.execution import paper as paper_module

            assert op65.INTRABAR_TIMEFRAMES == {"1m", "5m"}
            assert op65.INTRABAR_MIN_CONFIRMATIONS == 3
            assert op65.INTRABAR_MIN_STABILITY_SECONDS == 0.75
            assert op65.runtime.process_price is op65._process_price_65
            assert paper_module._MAX_PREENTRY_TARGET_PROGRESS == 0.75

            base = datetime(2026, 8, 19, 6, 30, tzinfo=timezone.utc)
            key = ("NQ", "1m")
            original = op65.runtime.candles.current.get(key)
            op65.runtime.candles.current[key] = {
                "open_time": base,
                "open": 25000.0,
                "high": 25020.0,
                "low": 24995.0,
                "close": 25015.0,
                "ticks": 42,
            }
            candle = op65._synthetic_candle("NQ", "1m", base + timedelta(seconds=10))
            assert candle is not None
            assert candle.open_time == base
            assert candle.close_time == base + timedelta(seconds=10)
            assert candle.close == 25015.0
            assert candle.ticks == 42

            if original is None:
                op65.runtime.candles.current.pop(key, None)
            else:
                op65.runtime.candles.current[key] = original

            op65._intrabar_candidates.clear()
            fingerprint = ("NQ", "1m", "bullish", "bucket", "smt", 1, 2, 3)
            ready1, count1, age1 = op65._stability_ready(key, fingerprint, base)
            ready2, count2, age2 = op65._stability_ready(key, fingerprint, base + timedelta(seconds=0.4))
            ready3, count3, age3 = op65._stability_ready(key, fingerprint, base + timedelta(seconds=0.8))
            assert not ready1 and count1 == 1 and age1 == 0.0
            assert not ready2 and count2 == 2
            assert ready3 and count3 == 3 and age3 >= 0.75

            print("operation65-intrabar-ok")
            '''
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        self.assertIn("operation65-intrabar-ok", completed.stdout)

    def test_dashboard_history_is_realized_only(self):
        cleanup = (ROOT / "src/dashboard/static/trade-history-cleanup.js").read_text()
        index = (ROOT / "src/dashboard/static/index.html").read_text()
        server = (ROOT / "src/dashboard/server.py").read_text()

        self.assertIn('new Set(["WIN", "LOSS"])', cleanup)
        self.assertIn("isRealizedTrade65", cleanup)
        self.assertIn("Missed / Rejected Attempts", cleanup)
        self.assertIn("Missed / Rejected Setups", cleanup)
        self.assertIn("No realized paper trades yet", cleanup)
        self.assertIn("MutationObserver", cleanup)
        self.assertIn("nonExecutedTradesBody65", cleanup)
        self.assertIn("trade-history-cleanup.js?v=6.5", index)
        self.assertIn('"src.main_65"', server)
        self.assertIn('"src.main_64"', server)

    def test_operation65_preserves_stable_state_and_no_chase(self):
        text = (ROOT / "src/main_65.py").read_text()
        self.assertIn("deepcopy(ict)", text)
        self.assertIn("durable_state_untouched", text)
        self.assertIn("no_chase_preserved", text)
        self.assertIn("runtime.paper.register_setup", text)
        self.assertIn("runtime.process_price = _process_price_65", text)


if __name__ == "__main__":
    unittest.main()
