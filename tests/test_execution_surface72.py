import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Operation72SafetySurfaceTests(unittest.TestCase):
    def test_kill_switch_reset_requires_explicit_confirmation(self):
        text = (ROOT / "src/execution/live/api.py").read_text()
        self.assertIn('KILL_SWITCH_RESET_CONFIRMATION = "RESET_EXECUTION_KILL_SWITCH"', text)
        self.assertIn("if not payload.enabled and payload.confirmation != KILL_SWITCH_RESET_CONFIRMATION", text)

    def test_dashboard_boot_installs_execution_safety_surface(self):
        text = (ROOT / "src/dashboard/server_72.py").read_text()
        self.assertIn("execution-safety.css?v=7.2", text)
        self.assertIn("execution-safety.js?v=7.2", text)
        self.assertIn('id="executionModeStatus"', text)
        self.assertIn('id="executionKillEngage"', text)
        self.assertIn('id="executionKillReset"', text)

    def test_dashboard_controls_cannot_arm_execution(self):
        text = (ROOT / "src/dashboard/static/execution-safety.js").read_text()
        self.assertNotIn("OTR_EXECUTION_ARMED", text)
        self.assertNotIn("live_allowed", text)
        self.assertIn("RESET_EXECUTION_KILL_SWITCH", text)


if __name__ == "__main__":
    unittest.main()
