from __future__ import annotations

import unittest

from src.integrations.nautilus_shadow.execution_parity import ShadowBracketIntent


class NautilusExecutionParityIntentTests(unittest.TestCase):
    def test_valid_bullish_intent(self):
        intent = ShadowBracketIntent(
            setup_id="gc-bull",
            direction="bullish",
            entry_price=3500.0,
            stop_price=3498.0,
            target_price=3504.0,
            quantity=2,
            execution_contract="MGC DEC26",
        )
        intent.validate()

    def test_valid_bearish_intent(self):
        intent = ShadowBracketIntent(
            setup_id="gc-bear",
            direction="bearish",
            entry_price=3500.0,
            stop_price=3502.0,
            target_price=3496.0,
            quantity=1,
            execution_contract="MGC DEC26",
        )
        intent.validate()

    def test_inverted_bullish_geometry_is_rejected(self):
        intent = ShadowBracketIntent(
            setup_id="bad",
            direction="bullish",
            entry_price=3500.0,
            stop_price=3502.0,
            target_price=3504.0,
        )
        with self.assertRaises(ValueError):
            intent.validate()

    def test_zero_quantity_is_rejected(self):
        intent = ShadowBracketIntent(
            setup_id="bad-qty",
            direction="bearish",
            entry_price=3500.0,
            stop_price=3502.0,
            target_price=3496.0,
            quantity=0,
        )
        with self.assertRaises(ValueError):
            intent.validate()


if __name__ == "__main__":
    unittest.main()
