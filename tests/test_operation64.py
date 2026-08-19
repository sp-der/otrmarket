import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Operation64RegressionTests(unittest.TestCase):
    def test_operation64_runtime_contracts_in_isolated_process(self):
        code = textwrap.dedent(
            r'''
            from datetime import datetime, timedelta, timezone
            from types import SimpleNamespace

            import src.main_64 as op64
            from src.execution import paper as paper_module

            BASE = datetime(2026, 8, 19, 5, 0, tzinfo=timezone.utc)

            assert paper_module._MAX_PREENTRY_TARGET_PROGRESS == 0.75

            displacement = SimpleNamespace(
                direction="bullish",
                candle_time=BASE - timedelta(minutes=1),
                body_ratio=1.95,
                range_ratio=1.55,
            )
            fvg = SimpleNamespace(
                direction="bullish",
                formed_at=BASE - timedelta(seconds=30),
            )
            setup = SimpleNamespace(
                setup_id="ct64",
                symbol="NQ",
                timeframe="5m",
                direction="bullish",
                created_at=BASE,
                trigger_type="liquidity_sweep",
                displacement=displacement,
                entry_fvg=fvg,
                entry_price=100.0,
                stop_price=95.0,
                target_price=110.5,
                risk_reward=2.10,
                status="PENDING",
                metadata={"strategy": "ICT_CONFLUENCE", "entry_type": "FVG_MIDPOINT"},
            )

            old_narrative = op64.op61._narrative
            op64.op61._narrative = lambda s, h: {
                "primary_timeframe": "15m",
                "primary": "bearish",
                "intermediate_timeframe": "30m",
                "intermediate": "bearish",
                "narrative_timeframe": "1h",
                "narrative": "bearish",
                "supports_setup": False,
                "strong_support": False,
            }
            score, details = op64._countertrend_score(setup, {})
            assert score >= 80, (score, details)
            assert details["entry_fvg_age_bars"] <= 2.0

            old_gate = op64._previous_quality_gate_64
            old_risk_guards = op64.op61._risk_guards
            old_post_loss = op64.op61._post_loss_risk
            op64._previous_quality_gate_64 = lambda c, s, h=None: (
                False,
                "15m context is bearish while the setup is bullish.",
            )
            op64.op61._risk_guards = lambda c, s: (True, "ok")
            op64.op61._post_loss_risk = lambda c, s: (True, "no loss")
            allowed, reason = op64._adaptive_quality_gate_64(None, setup, {})
            assert allowed, reason
            assert setup.metadata["execution_tier"] == "COUNTERTREND_REVERSAL_64"
            assert float(setup.metadata["risk_multiplier"]) <= 0.35

            op64._previous_quality_gate_64 = old_gate
            op64.op61._risk_guards = old_risk_guards
            op64.op61._post_loss_risk = old_post_loss
            op64.op61._narrative = old_narrative

            # Fast move already beyond 75% of its objective never enters the
            # pending order book. It is labeled as missed/extended and the
            # continuation engine is armed immediately.
            fast = SimpleNamespace(
                setup_id="fastgc",
                symbol="GC",
                timeframe="1m",
                direction="bullish",
                created_at=BASE,
                entry_price=100.0,
                stop_price=99.0,
                target_price=104.0,
                risk_reward=4.0,
                metadata={},
                status="PENDING",
            )
            old_arm = op64.continuation.arm_from_stale
            op64.continuation.arm_from_stale = lambda s, t: True
            old_price = op64.runtime.market_state["GC"]["price"]
            op64.runtime.market_state["GC"]["price"] = 104.4
            position = op64._register_setup_64(
                fast,
                risk_dollars=125.0,
                guard_reason="test",
            )
            assert position.status == "INVALIDATED"
            assert position.result == "MISSED_EXTENDED"
            assert fast.status == "MISSED_EXTENDED"
            assert fast.metadata["pre_registration_viability_64"]["target_progress_pct"] == 110.0
            assert fast.metadata["pre_registration_viability_64"]["continuation_armed"] is True
            assert "fastgc" not in op64.runtime.paper.positions

            op64.runtime.market_state["GC"]["price"] = old_price
            op64.continuation.arm_from_stale = old_arm

            print("operation64-regression-ok")
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
        self.assertIn("operation64-regression-ok", completed.stdout)

    def test_operation64_file_preserves_no_chase_and_recovery(self):
        text = (ROOT / "src/main_64.py").read_text()
        self.assertIn("_MAX_PREENTRY_TARGET_PROGRESS", text)
        self.assertIn("MISSED_EXTENDED", text)
        self.assertIn("COUNTERTREND_REVERSAL_64", text)
        self.assertIn("op63.op62._restore_progress_62()", text)


if __name__ == "__main__":
    unittest.main()
