import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import main_68 as op68


def _setup(timeframe: str, strategy: str = "ICT_CONFLUENCE"):
    return SimpleNamespace(
        timeframe=timeframe,
        metadata={"strategy": strategy},
    )


class Operation68Tests(unittest.TestCase):
    def test_one_minute_candidates_are_scout_only(self):
        with patch.object(
            op68,
            "_previous_quality_gate_68",
            side_effect=lambda connection, setup, histories=None: (True, "prior quality passed"),
        ):
            setup = _setup("1m", "MSS_REVERSAL")
            allowed, reason = op68._adaptive_quality_gate_68(None, setup, {})

        self.assertFalse(allowed)
        self.assertIn("scout", reason.lower())
        self.assertFalse(setup.metadata["one_minute_firewall_68"]["autonomous_execution"])
        self.assertEqual(setup.metadata["one_minute_firewall_68"]["strategy"], "MSS_REVERSAL")

    def test_five_minute_candidates_keep_existing_quality_decision(self):
        with patch.object(
            op68,
            "_previous_quality_gate_68",
            side_effect=lambda connection, setup, histories=None: (True, "prior quality passed"),
        ):
            setup = _setup("5m")
            allowed, reason = op68._adaptive_quality_gate_68(None, setup, {})

        self.assertTrue(allowed)
        self.assertEqual(reason, "prior quality passed")
        self.assertNotIn("one_minute_firewall_68", setup.metadata)

    def test_existing_rejections_are_preserved(self):
        with patch.object(
            op68,
            "_previous_quality_gate_68",
            side_effect=lambda connection, setup, histories=None: (False, "existing gate blocked"),
        ):
            setup = _setup("1m")
            allowed, reason = op68._adaptive_quality_gate_68(None, setup, {})

        self.assertFalse(allowed)
        self.assertEqual(reason, "existing gate blocked")
        self.assertNotIn("one_minute_firewall_68", setup.metadata)

    def test_intrabar_acceleration_is_five_minute_only(self):
        self.assertEqual(op68.op65.INTRABAR_TIMEFRAMES, {"5m"})


if __name__ == "__main__":
    unittest.main()
