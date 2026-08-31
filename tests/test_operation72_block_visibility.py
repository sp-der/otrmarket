import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Operation72BlockVisibilityTests(unittest.TestCase):
    def test_eval_limit_has_distinct_dashboard_bucket(self):
        text = (ROOT / "src/dashboard/queries_59.py").read_text()
        self.assertIn('"daily primary-trade limit"', text)
        self.assertIn('return "eval_limit"', text)
        self.assertIn('"latest_decisions": []', text)
        self.assertIn('"created_at": row["created_at"]', text)

    def test_decision_panel_renders_replay_time_and_reason(self):
        text = (ROOT / "src/dashboard/static/decision-telemetry.js").read_text()
        self.assertIn('eval_limit: "Eval limit"', text)
        self.assertIn("item.created_at", text)
        self.assertIn("item.reason", text)

    def test_supervisor_audits_latest_eval_slot_block_before_reset(self):
        text = (ROOT / "src/dashboard/server_72.py").read_text()
        self.assertIn("def _audit_latest_eval_limit_block", text)
        self.assertIn("REPLAY AUDIT latest eval-slot block", text)
        self.assertLess(
            text.index("_audit_latest_eval_limit_block()", text.index("def main")),
            text.index("runpy.run_module", text.index("def main")),
        )


if __name__ == "__main__":
    unittest.main()
