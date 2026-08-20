import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import main_69 as op69


def _setup(timeframe: str, strategy: str = "ICT_CONFLUENCE", grade: str | None = "A+"):
    metadata = {"strategy": strategy, "risk_multiplier": 1.0}
    if grade is not None:
        metadata["a_plus_context"] = {"quality_grade": grade, "quality_score": 92 if grade == "A+" else 84 if grade == "A" else 75}
    return SimpleNamespace(
        timeframe=timeframe,
        metadata=metadata,
    )


class Operation69Tests(unittest.TestCase):
    def test_a_plus_one_minute_candidate_can_execute(self):
        with patch.object(
            op69,
            "_previous_quality_gate_69",
            side_effect=lambda connection, setup, histories=None: (True, "prior quality passed"),
        ):
            setup = _setup("1m", "MSS_REVERSAL", "A+")
            allowed, reason = op69._adaptive_quality_gate_69(None, setup, {})

        self.assertTrue(allowed)
        self.assertIn("1m precision execution", reason.lower())
        self.assertTrue(setup.metadata["one_minute_precision_69"]["autonomous_execution"])
        self.assertFalse(setup.metadata["one_minute_precision_69"]["five_minute_confirmation_required"])
        self.assertEqual(setup.metadata["one_minute_precision_69"]["quality_grade"], "A+")
        self.assertEqual(setup.metadata["risk_multiplier"], 1.0)

    def test_b_plus_one_minute_candidate_is_reduced_risk(self):
        with patch.object(
            op69,
            "_previous_quality_gate_69",
            side_effect=lambda connection, setup, histories=None: (True, "prior quality passed"),
        ):
            setup = _setup("1m", "ICT_CONFLUENCE", "B+")
            allowed, reason = op69._adaptive_quality_gate_69(None, setup, {})

        self.assertTrue(allowed)
        self.assertIn("40%", reason)
        self.assertEqual(setup.metadata["risk_multiplier"], 0.40)
        self.assertEqual(setup.metadata["execution_tier"], "ONE_MINUTE_B_PLUS_REDUCED_69")
        self.assertEqual(setup.metadata["one_minute_precision_69"]["mode"], "AUTONOMOUS_REDUCED")

    def test_existing_quality_rejection_is_still_preserved(self):
        with patch.object(
            op69,
            "_previous_quality_gate_69",
            side_effect=lambda connection, setup, histories=None: (False, "existing gate blocked"),
        ):
            setup = _setup("1m", "MSS_REVERSAL", "A+")
            allowed, reason = op69._adaptive_quality_gate_69(None, setup, {})

        self.assertFalse(allowed)
        self.assertEqual(reason, "existing gate blocked")
        self.assertNotIn("one_minute_precision_69", setup.metadata)

    def test_five_minute_candidate_keeps_prior_decision(self):
        with patch.object(
            op69,
            "_previous_quality_gate_69",
            side_effect=lambda connection, setup, histories=None: (True, "prior quality passed"),
        ):
            setup = _setup("5m", "ICT_CONFLUENCE", "A")
            allowed, reason = op69._adaptive_quality_gate_69(None, setup, {})

        self.assertTrue(allowed)
        self.assertEqual(reason, "prior quality passed")
        self.assertNotIn("one_minute_precision_69", setup.metadata)

    def test_strict_rejection_block_one_minute_pass_can_execute(self):
        with patch.object(
            op69,
            "_previous_quality_gate_69",
            side_effect=lambda connection, setup, histories=None: (True, "strict 10/10 prior gate passed"),
        ):
            setup = _setup("1m", "REJECTION_BLOCK_10_10", None)
            allowed, _ = op69._adaptive_quality_gate_69(None, setup, {})

        self.assertTrue(allowed)
        self.assertEqual(setup.metadata["one_minute_precision_69"]["quality_grade"], "A+")

    def test_intrabar_acceleration_remains_five_minute_only(self):
        self.assertEqual(op69.op65.INTRABAR_TIMEFRAMES, {"5m"})


if __name__ == "__main__":
    unittest.main()
