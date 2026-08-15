# OTR Market Operation 4.5.1 - Railway Engine Supervisor

Fixes the production runtime issue where Railway could keep the FastAPI dashboard
online after the OTR strategy engine had stopped or had never been launched.

## Changes

- `src.dashboard.server` now launches `src.main` itself.
- The dashboard process owns a watchdog thread for the strategy engine.
- If the engine exits unexpectedly, the dashboard terminates too so Railway's
  restart policy restarts the entire OTR service.
- The engine PID is stored in `data/engine.pid`.
- Engine stdout/stderr appends to `data/engine.log` with unbuffered Python.
- `/market/api/health` reports `engine_running` and `engine_pid` in supervised
  mode and returns HTTP 503 if the engine is missing.
- `run_all.sh` now uses the same single-supervisor runtime locally and in
  production, preventing duplicate engine processes.

Live broker execution remains disabled.
