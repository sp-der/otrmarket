from __future__ import annotations

import unittest

from src.dashboard import server_80


class Supervisor80Tests(unittest.TestCase):
    def test_promotes_clean_engine(self):
        self.assertEqual(server_80._promote_engine_80(), "src.main_80")

    def test_future_full_wipes_include_otr8_decision_traces(self):
        self.assertIn("decision_traces_80", server_80.legacy.FULL_WIPE_TABLES_72T)


if __name__ == "__main__":
    unittest.main()
