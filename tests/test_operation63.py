import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Operation63RegressionTests(unittest.TestCase):
    def test_production_entrypoint_uses_operation62_recovery_hook(self):
        text = (ROOT / "src/main_63.py").read_text()
        self.assertIn("op62._restore_progress_62()", text)
        self.assertNotIn("op62._restore_progress()", text)

    def test_operation63_runtime_contracts_in_isolated_process(self):
        code = textwrap.dedent(
            r'''
            from datetime import datetime, timedelta, timezone
            from types import SimpleNamespace

            import src.main_63 as op63
            from src.execution import paper as paper_module
            from src.execution.paper import PaperExecutor, PaperPosition

            BASE = datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc)

            assert op63._normalize_handled(None) == []
            marker = object()
            assert op63._normalize_handled(marker) == [marker]
            assert op63._normalize_handled([marker]) == [marker]

            assert paper_module._PENDING_BARS == {
                "1m": 15,
                "5m": 8,
                "15m": 4,
                "1h": 2,
            }
            assert paper_module._MAX_PREENTRY_TARGET_PROGRESS == 0.75

            def setup(setup_id, timeframe="1m", direction="bullish"):
                if direction == "bullish":
                    entry, stop, target = 100.0, 95.0, 110.0
                else:
                    entry, stop, target = 100.0, 105.0, 90.0
                return SimpleNamespace(
                    setup_id=setup_id,
                    symbol="NQ",
                    timeframe=timeframe,
                    direction=direction,
                    created_at=BASE,
                    entry_price=entry,
                    stop_price=stop,
                    target_price=target,
                    risk_reward=2.0,
                    metadata={},
                    status="PENDING",
                )

            def executor_with(item):
                executor = PaperExecutor()
                executor.positions[item.setup_id] = PaperPosition(
                    setup=item,
                    risk_dollars=200.0,
                )
                return executor

            # 1m no longer dies at the old six-minute limit.
            one = setup("one")
            ex = executor_with(one)
            assert ex.on_price("NQ", 103.0, BASE + timedelta(minutes=7)) == []
            assert ex.pending_count == 1
            changed = ex.on_price("NQ", 103.0, BASE + timedelta(minutes=16))
            assert len(changed) == 1
            assert changed[0].result == "EXPIRED_BEFORE_ENTRY"
            life = one.metadata["entry_lifecycle_63"]
            assert life["expiry_limit_bars"] == 15
            assert life["age_bars"] == 16.0

            # 5m survives the old four-bar / twenty-minute boundary and expires
            # only after the new eight-bar / forty-minute window.
            five = setup("five", timeframe="5m")
            ex = executor_with(five)
            assert ex.on_price("NQ", 103.0, BASE + timedelta(minutes=25)) == []
            changed = ex.on_price("NQ", 103.0, BASE + timedelta(minutes=41))
            assert changed[0].result == "EXPIRED_BEFORE_ENTRY"
            assert five.metadata["entry_lifecycle_63"]["expiry_limit_bars"] == 8

            # No-chase remains exactly 75% of the planned objective.
            stale = setup("stale")
            ex = executor_with(stale)
            changed = ex.on_price("NQ", 107.5, BASE + timedelta(minutes=2))
            assert changed[0].result == "STALE_MOVE_BEFORE_ENTRY"
            assert stale.metadata["entry_lifecycle_63"]["target_progress_pct"] == 75.0

            # Protective stop still invalidates immediately, regardless of the
            # longer entry-development window.
            stopped = setup("stopped")
            ex = executor_with(stopped)
            changed = ex.on_price("NQ", 94.75, BASE + timedelta(minutes=1))
            assert changed[0].result == "INVALIDATED_BEFORE_ENTRY"

            print("operation63-regression-ok")
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
        self.assertIn("operation63-regression-ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
