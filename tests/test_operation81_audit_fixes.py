from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.otr8.execution_policy81 import REDUCED_RISK_DOLLARS, eval_risk81


class Operation81AuditFixTests(unittest.TestCase):
    def test_gold_momentum_a_plus_context_stays_reduced_risk(self):
        setup = SimpleNamespace(
            timeframe="15m",
            direction="bullish",
            risk_reward=1.56,
            metadata={
                "strategy": "GOLD_MOMENTUM_PULLBACK_72R",
                "checklist_score": 6,
                "checklist_total": 6,
                "a_plus_context": {"quality_grade": "A+", "quality_score": 92},
                "execution_tier": "GOLD_MOMENTUM_REDUCED_72R",
                "risk_multiplier": 0.75,
            },
        )
        decision = SimpleNamespace(risk_dollars=750.0)

        risk, multiplier = eval_risk81(decision, setup)

        self.assertEqual(risk, REDUCED_RISK_DOLLARS)
        self.assertAlmostEqual(multiplier, REDUCED_RISK_DOLLARS / 750.0, places=6)
        self.assertIn(
            REDUCED_RISK_DOLLARS,
            setup.metadata["risk_policy_81"]["explicit_safety_caps"],
        )


if __name__ == "__main__":
    unittest.main()
