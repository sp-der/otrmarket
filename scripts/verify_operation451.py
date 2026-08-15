from pathlib import Path

root = Path(__file__).resolve().parents[1]
server = (root / "src/dashboard/server.py").read_text()
runner = (root / "run_all.sh").read_text()
app = (root / "src/dashboard/app.py").read_text()

assert '"-u", "-m", "src.main"' in server
assert "otr-engine-watchdog" in server
assert "ENGINE_PID_FILE" in server
assert "OTR_REQUIRE_ENGINE_HEALTH" in server
assert 'exec "$PYTHON_BIN" -m src.dashboard.server' in runner
assert "engine_process_status" in app
assert '"engine_running"' in app
assert "response.status_code = 503" in app

print("Operation 4.5.1 verification: OK")
print("Runtime model: dashboard supervisor + strategy engine watchdog")
