import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScannerDecisionSyncTests(unittest.TestCase):
    def test_decision_sync_loads_after_scanner_clarity(self):
        html = (ROOT / "src/dashboard/static/index.html").read_text()
        clarity = html.index("scanner-clarity.js?v=6.4")
        sync = html.index("scanner-decision-live.js?v=6.4")
        self.assertLess(clarity, sync)

    def test_live_scanner_joins_final_setup_decision_by_setup_id(self):
        text = (ROOT / "src/dashboard/static/scanner-decision-live.js").read_text()
        self.assertIn("otrScannerDecisionBySetupId", text)
        self.assertIn("d?.setup_id", text)
        self.assertIn("QUALITY BLOCKED", text)
        self.assertIn("TRADE ARMED", text)
        self.assertIn("MISSED / EXTENDED", text)
        self.assertIn("setupDecisionReason(setup)", text)
        self.assertIn("renderSetupsDecisionSync", text)


if __name__ == "__main__":
    unittest.main()
