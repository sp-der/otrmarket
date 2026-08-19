import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Operation451SupervisorTests(unittest.TestCase):
    def test_dashboard_server_supervises_engine(self):
        text = (ROOT / "src/dashboard/server.py").read_text()
        self.assertIn('requested_module = os.getenv("OTR_ENGINE_MODULE", "src.main_65")', text)
        self.assertIn('"src.main_64"', text)
        self.assertIn('"src.main_62"', text)
        self.assertIn('[sys.executable, "-u", "-m", engine_module]', text)
        self.assertIn("otr-engine-watchdog", text)
        self.assertIn("ENGINE_PID_FILE", text)
        self.assertIn("os.kill(os.getpid(), signal.SIGTERM)", text)

    def test_run_all_uses_single_supervisor(self):
        text = (ROOT / "run_all.sh").read_text()
        self.assertIn('exec "$PYTHON_BIN" -m src.dashboard.server', text)
        self.assertNotIn('src.main > data/engine.log', text)

    def test_health_reports_engine_state(self):
        text = (ROOT / "src/dashboard/app.py").read_text()
        self.assertIn("engine_process_status", text)
        self.assertIn('"engine_running"', text)
        self.assertIn("response.status_code = 503", text)


if __name__ == "__main__":
    unittest.main()
