from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.integrations.nautilus_shadow.config import NautilusShadowConfig
from src.integrations.nautilus_shadow.normalize import from_paper_position
from src.integrations.nautilus_shadow.parity import ExecutionSnapshot, compare_execution_snapshots


class NautilusShadowConfigTests(unittest.TestCase):
    def test_shadow_defaults_off(self):
        with patch.dict(os.environ, {}, clear=True):
            config = NautilusShadowConfig.from_env()
        self.assertFalse(config.enabled)
        self.assertFalse(config.strict_parity)
        self.assertFalse(config.record_matches)

    def test_shadow_env_toggle(self):
        with patch.dict(
            os.environ,
            {
                "OTR_NAUTILUS_SHADOW": "1",
                "OTR_NAUTILUS_STRICT_PARITY": "true",
                "OTR_NAUTILUS_RECORD_MATCHES": "yes",
            },
            clear=True,
        ):
            config = NautilusShadowConfig.from_env()
        self.assertTrue(config.enabled)
        self.assertTrue(config.strict_parity)
        self.assertTrue(config.record_matches)


class NautilusParityTests(unittest.TestCase):
    def test_matching_execution(self):
        otr = ExecutionSnapshot("gc-1", "CLOSED", 2500.0, 2504.0, "WIN", 2.0, 500.0)
        shadow = ExecutionSnapshot("gc-1", "closed", 2500.0, 2504.0, "win", 2.0, 500.0)
        result = compare_execution_snapshots(otr, shadow)
        self.assertTrue(result.matched)
        self.assertEqual(result.differences, ())

    def test_divergence_reports_specific_fields(self):
        otr = ExecutionSnapshot("gc-2", "CLOSED", 2500.0, 2498.0, "LOSS", -1.0, -250.0)
        shadow = ExecutionSnapshot("gc-2", "OPEN", 2500.0, None, None, None, None)
        result = compare_execution_snapshots(otr, shadow)
        self.assertFalse(result.matched)
        joined = "|".join(result.differences)
        self.assertIn("status:", joined)
        self.assertIn("exit_price:", joined)
        self.assertIn("result:", joined)
        self.assertIn("result_r:", joined)
        self.assertIn("result_dollars:", joined)

    def test_pending_position_has_no_fill_price(self):
        setup = SimpleNamespace(setup_id="gc-3", entry_price=2500.0)
        position = SimpleNamespace(
            setup=setup,
            status="PENDING",
            opened_at=None,
            exit_price=None,
            result=None,
            result_r=None,
            result_dollars=None,
        )
        snapshot = from_paper_position(position)
        self.assertIsNone(snapshot.entry_price)
        self.assertEqual(snapshot.status, "PENDING")


if __name__ == "__main__":
    unittest.main()
