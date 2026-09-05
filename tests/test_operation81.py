from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from src.execution.live.config import ExecutionConfig
from src.execution.live.models import ExecutionMode
from src.execution.live.sizing import build_execution_intent
from src.otr8.execution_policy81 import (
    FULL_RISK_DOLLARS,
    PENDING_BARS_81,
    REDUCED_RISK_DOLLARS,
    eval_risk81,
    pending_expiry81,
    prepare_execution_zone81,
    rr_decision81,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 4, 14, 30, tzinfo=UTC)


def setup_stub(*, grade="A+", rr=1.25, score=6, preview=False, entry_type="FVG_MIDPOINT"):
    fvg = SimpleNamespace(lower=102.5, upper=105.0)
    displacement = SimpleNamespace(low=100.0, high=110.0, body_ratio=2.0, range_ratio=1.6)
    return SimpleNamespace(
        setup_id="gc81test",
        symbol="GC",
        timeframe="5m",
        direction="bullish",
        created_at=NOW,
        entry_fvg=fvg,
        displacement=displacement,
        trigger_type="liquidity_sweep",
        entry_price=103.75,
        stop_price=100.0,
        target_price=120.0,
        risk_reward=rr,
        metadata={
            "strategy": "ICT_CONFLUENCE",
            "entry_type": entry_type,
            "checklist_score": score,
            "checklist_total": 6,
            "preview_only_80": preview,
            "a_plus_context": {"quality_grade": grade, "quality_score": 92 if grade == "A+" else 84},
            "gold_regime_80": {"regime": "TREND_EXPANSION", "direction": "bullish"},
        },
    )


class Operation81PolicyTests(unittest.TestCase):
    def test_dynamic_rr_allows_a_plus_at_1_20(self):
        setup = setup_stub(grade="A+", rr=1.20)
        decision = rr_decision81(setup, {"samples": 0, "usable": False})
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.grade, "A+")
        self.assertEqual(decision.floor, 1.20)

    def test_dynamic_rr_keeps_a_at_1_30_without_evidence(self):
        setup = setup_stub(grade="A", rr=1.25)
        decision = rr_decision81(setup, {"samples": 0, "usable": False})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.floor, 1.30)
        setup.risk_reward = 1.30
        self.assertTrue(rr_decision81(setup, {"samples": 0, "usable": False}).allowed)

    def test_a_evidence_can_relax_to_1_20_only_after_usable_positive_sample(self):
        setup = setup_stub(grade="A", rr=1.22)
        evidence = {"samples": 24, "wins": 14, "losses": 10, "win_rate": 14 / 24, "expectancy_r": 0.31, "usable": True}
        decision = rr_decision81(setup, evidence)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.floor, 1.20)

    def test_first_touch_zone_uses_conservative_fvg_edge(self):
        setup = setup_stub(grade="A+", rr=3.0)
        zone = prepare_execution_zone81(setup, 1.20)
        self.assertTrue(zone.allowed)
        self.assertEqual(zone.source, "FVG_X_50_TO_79")
        self.assertAlmostEqual(zone.high, 105.0, places=6)
        self.assertAlmostEqual(setup.entry_price, 105.0, places=6)
        self.assertGreaterEqual(setup.risk_reward, 1.20)
        self.assertEqual(setup.metadata["execution_zone_81"]["fill_rule"],
                         "First touch of the valid zone at the least-favorable R:R-safe edge")

    def test_pending_lifetime_begins_at_registration_time(self):
        setup = setup_stub()
        setup.timeframe = "1m"
        setup.metadata["pending_registered_at_81"] = "2026-08-04T15:00:00+00:00"
        expiry = pending_expiry81(setup)
        self.assertEqual(PENDING_BARS_81["1m"], 12)
        self.assertEqual(expiry.isoformat(), "2026-08-04T15:12:00+00:00")

    def test_eval_risk_contract_is_750_a_plus_500_a_and_zero_preview(self):
        decision = SimpleNamespace(risk_dollars=750.0)
        a_plus = setup_stub(grade="A+", score=6)
        risk, _ = eval_risk81(decision, a_plus)
        self.assertEqual(risk, FULL_RISK_DOLLARS)

        a = setup_stub(grade="A", score=6)
        risk, _ = eval_risk81(decision, a)
        self.assertEqual(risk, REDUCED_RISK_DOLLARS)

        early = setup_stub(grade="A+", score=5)
        early.metadata["candidate_source_80"] = "EARLY_ARM_72H"
        risk, _ = eval_risk81(decision, early)
        self.assertEqual(risk, REDUCED_RISK_DOLLARS)

        preview = setup_stub(grade="A+", score=4, preview=True)
        preview.metadata["candidate_source_80"] = "EARLY_ARM_72H"
        risk, _ = eval_risk81(decision, preview)
        self.assertEqual(risk, 0.0)

    def test_one_minute_reversal_is_explicitly_capped_at_500(self):
        decision = SimpleNamespace(risk_dollars=750.0)
        setup = setup_stub(grade="A+", score=6)
        setup.timeframe = "1m"
        setup.metadata["strategy"] = "MSS_REVERSAL"
        risk, _ = eval_risk81(decision, setup)
        self.assertEqual(risk, REDUCED_RISK_DOLLARS)

    def test_live_mgc_sizing_can_translate_750_budget_without_exceeding_it(self):
        setup = setup_stub(grade="A+", score=6)
        setup.entry_price = 4300.0
        setup.stop_price = 4295.0
        setup.target_price = 4306.0
        setup.risk_reward = 1.20
        config = ExecutionConfig(
            mode=ExecutionMode.PAPER,
            armed=False,
            live_allowed=False,
            certified=False,
            account="Sim101",
            max_micros=20,
            max_risk_dollars=750.0,
        )
        intent = build_execution_intent(setup, risk_dollars=750.0, config=config, now=NOW)
        # MGC is $10/point. A five-point stop is $50/contract, so 15 micros
        # consume the full $750 budget without ever crossing it.
        self.assertEqual(intent.execution_contract, "MGC")
        self.assertEqual(intent.quantity, 15)
        self.assertEqual(intent.per_contract_risk, 50.0)
        self.assertEqual(intent.risk_dollars, 750.0)
        self.assertLessEqual(intent.risk_dollars, intent.requested_risk)


class Operation81RuntimeTests(unittest.TestCase):
    def test_operation81_replaces_only_final_policy_hooks(self):
        script = r'''
import json
from src import main_81
main_81.install_operation81()
pipeline = main_81.op80._install_pipeline_80()
print(json.dumps({
    "quality_gate": getattr(pipeline.quality_gate, "__name__", ""),
    "setup_risk": getattr(pipeline.setup_risk, "__name__", ""),
    "pending_1m": main_81.paper_module._PENDING_BARS.get("1m"),
    "reversal_min_rr": main_81.runtime.strategy.reversal.min_rr,
    "continuation_min_rr": main_81.op62.continuation.min_rr,
}))
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=25,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["quality_gate"], "_quality_gate_81")
        self.assertEqual(payload["setup_risk"], "_setup_risk_81")
        self.assertEqual(payload["pending_1m"], 12)
        self.assertAlmostEqual(payload["reversal_min_rr"], 1.20)
        self.assertAlmostEqual(payload["continuation_min_rr"], 1.20)

    def test_server81_promotes_main81(self):
        script = r'''
import json
from src.dashboard import server_81
print(json.dumps({"engine": server_81._promote_engine_81()}))
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            timeout=25,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["engine"], "src.main_81")


if __name__ == "__main__":
    unittest.main()
