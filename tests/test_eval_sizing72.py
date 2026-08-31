import unittest
from types import SimpleNamespace

from src.risk.eval_sizing72 import apply_eval_sizing72


class EvalSizing72Tests(unittest.TestCase):
    def _setup(self, grade, rr=3.0, mode="EVAL"):
        return SimpleNamespace(
            risk_reward=rr,
            metadata={
                "operating_mode": {"mode": mode, "quality_grade": grade},
                "profit_objective_dollars": 1500.0,
            },
        )

    def _decision(self, risk=500.0):
        return SimpleNamespace(risk_dollars=risk)

    def test_a_plus_can_use_full_500_eval_risk(self):
        setup = self._setup("A+")
        risk, multiplier = apply_eval_sizing72(self._decision(), setup, 500.0, 1.0)
        self.assertEqual(risk, 500.0)
        self.assertEqual(multiplier, 1.0)
        self.assertEqual(setup.metadata["eval_sizing_72"]["projected_profit_dollars"], 1500.0)
        self.assertTrue(setup.metadata["eval_sizing_72"]["profit_objective_met"])

    def test_a_is_capped_at_350(self):
        setup = self._setup("A")
        risk, multiplier = apply_eval_sizing72(self._decision(), setup, 500.0, 1.0)
        self.assertEqual(risk, 350.0)
        self.assertAlmostEqual(multiplier, 0.7)

    def test_b_plus_is_capped_at_100(self):
        setup = self._setup("B+", rr=2.5)
        risk, multiplier = apply_eval_sizing72(self._decision(), setup, 200.0, 0.4)
        self.assertEqual(risk, 100.0)
        self.assertAlmostEqual(multiplier, 0.2)

    def test_existing_session_or_reversal_reduction_still_wins(self):
        setup = self._setup("A+")
        risk, multiplier = apply_eval_sizing72(self._decision(), setup, 250.0, 0.5)
        self.assertEqual(risk, 250.0)
        self.assertAlmostEqual(multiplier, 0.5)

    def test_daily_headroom_still_wins(self):
        setup = self._setup("A+")
        risk, multiplier = apply_eval_sizing72(self._decision(250.0), setup, 250.0, 1.0)
        self.assertEqual(risk, 250.0)
        self.assertEqual(multiplier, 1.0)

    def test_funded_mode_is_unchanged(self):
        setup = self._setup("A+", mode="FUNDED")
        risk, multiplier = apply_eval_sizing72(self._decision(), setup, 175.0, 0.35)
        self.assertEqual(risk, 175.0)
        self.assertEqual(multiplier, 0.35)
        self.assertNotIn("eval_sizing_72", setup.metadata)


if __name__ == "__main__":
    unittest.main()
