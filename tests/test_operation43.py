import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Operation43PermanentHostingTests(unittest.TestCase):
    def test_server_honors_railway_port(self):
        text = (ROOT / "src" / "dashboard" / "server.py").read_text()
        self.assertIn('os.getenv("PORT")', text)
        self.assertIn('os.getenv("DASHBOARD_PORT", "8000")', text)

    def test_run_all_does_not_require_local_virtualenv(self):
        text = (ROOT / "run_all.sh").read_text()
        self.assertIn('if [ -f ".venv/bin/activate" ]', text)
        self.assertIn('export DASHBOARD_PORT="$PORT"', text)
        self.assertIn('"$PYTHON_BIN" -m src.main', text)

    def test_railway_config_has_healthcheck_and_restart_policy(self):
        config = json.loads((ROOT / "railway.json").read_text())
        self.assertEqual(config["build"]["builder"], "DOCKERFILE")
        self.assertEqual(config["deploy"]["healthcheckPath"], "/market/api/health")
        self.assertEqual(config["deploy"]["restartPolicyType"], "ALWAYS")

    def test_deploy_guide_requires_persistent_volume(self):
        text = (ROOT / "DEPLOY-RAILWAY.md").read_text()
        self.assertIn("/app/data", text)
        self.assertIn("market.otrservices.com", text)
        self.assertIn("OTR_BRIDGE_KEY", text)


if __name__ == "__main__":
    unittest.main()
