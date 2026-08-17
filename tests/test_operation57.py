import unittest

from src.strategies.confluence import ConfluenceEngine
from src.strategies.rejection_block import RejectionBlockEngine


class Operation57ExpirationTests(unittest.TestCase):
    def test_ict_post_signal_stages_default_to_fifteen_bars(self):
        engine = ConfluenceEngine()
        for stage in (
            "WAIT_DISPLACEMENT",
            "WAIT_ENTRY_FVG",
            "WAIT_QUALIFYING_FVG",
            "WAIT_VALID_RR",
        ):
            with self.subTest(stage=stage):
                self.assertEqual(engine.stage_expiry_bars[stage], 15)

    def test_rejection_block_displacement_defaults_to_fifteen_bars(self):
        engine = RejectionBlockEngine()
        self.assertEqual(engine.stage_expiry_bars["WAIT_DISPLACEMENT"], 15)

    def test_explicit_replay_calibration_can_still_override_default(self):
        engine = ConfluenceEngine(
            displacement_expiry_bars=8,
            entry_fvg_expiry_bars=8,
        )
        self.assertEqual(engine.stage_expiry_bars["WAIT_DISPLACEMENT"], 8)
        self.assertEqual(engine.stage_expiry_bars["WAIT_ENTRY_FVG"], 8)


if __name__ == "__main__":
    unittest.main()
