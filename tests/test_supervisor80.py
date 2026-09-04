from __future__ import annotations

from pathlib import Path
import unittest

from src.dashboard import server_80


class Supervisor80Tests(unittest.TestCase):
    def test_promotes_clean_engine_on_actual_server72_owner(self):
        original = server_80.core72.promoted_engine_module
        try:
            self.assertEqual(server_80._promote_engine_80(), "src.main_80")
            self.assertEqual(server_80.core72.promoted_engine_module(), "src.main_80")
        finally:
            server_80.core72.promoted_engine_module = original

    def test_future_full_wipes_include_otr8_decision_traces(self):
        self.assertIn("decision_traces_80", server_80.legacy.FULL_WIPE_TABLES_72T)

    def test_supervisor_bypasses_wrapper_repromotion(self):
        text = (Path(__file__).resolve().parents[1] / "src" / "dashboard" / "server_80.py").read_text(encoding="utf-8")
        self.assertIn("dashboard72n.main()", text)
        self.assertNotIn("legacy.main()", text)
        self.assertIn("core72.promoted_engine_module", text)
        self.assertIn("Operation 8.0 supervisor", text)


if __name__ == "__main__":
    unittest.main()
