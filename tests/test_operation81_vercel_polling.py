from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Operation81VercelPollingTests(unittest.TestCase):
    def test_polling_fallback_renders_full_snapshot(self):
        text = (ROOT / "src/dashboard/static/connection-poll81.js").read_text(encoding="utf-8")
        self.assertIn("/market/api/snapshot?connection_probe=", text)
        self.assertIn("window.render(snapshot)", text)
        self.assertIn("Live dashboard", text)

    def test_supervisor_cache_busts_full_polling_fallback(self):
        text = (ROOT / "src/dashboard/server_81.py").read_text(encoding="utf-8")
        self.assertIn("connection-poll81.js?v=8.1-live2", text)
        # The overnight replay reset is explicit-intent (operator-set token),
        # not a hardcoded generation string bumped in code. See the OTR-81
        # safety audit: a code-only bump used to be enough to silently DELETE
        # active trading tables on the next boot.
        self.assertIn("OTR_OPERATION81_RESET_TOKEN", text)


if __name__ == "__main__":
    unittest.main()
