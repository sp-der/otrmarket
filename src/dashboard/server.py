from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
ENGINE_LOG = DATA_DIR / "engine.log"
ENGINE_PID_FILE = DATA_DIR / "engine.pid"

_engine_process: subprocess.Popen | None = None
_engine_log_handle = None
_shutting_down = False


def _stop_engine() -> None:
    global _shutting_down, _engine_process, _engine_log_handle
    _shutting_down = True

    process = _engine_process
    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        except Exception:
            pass

    try:
        ENGINE_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    if _engine_log_handle is not None:
        try:
            _engine_log_handle.flush()
            _engine_log_handle.close()
        except Exception:
            pass
        _engine_log_handle = None


def _monitor_engine(process: subprocess.Popen) -> None:
    exit_code = process.wait()
    if _shutting_down:
        return

    try:
        with ENGINE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n[SUPERVISOR] Strategy engine exited unexpectedly with code {exit_code}. "
                "Stopping dashboard so the host restarts the service.\n"
            )
    except Exception:
        pass

    # Railway supervises the dashboard process. Terminating PID 1 here makes a
    # strategy-engine failure become a service failure instead of a zombie UI.
    os.kill(os.getpid(), signal.SIGTERM)


def _start_engine() -> None:
    global _engine_process, _engine_log_handle

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ENGINE_LOG.touch(exist_ok=True)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    _engine_log_handle = ENGINE_LOG.open("a", encoding="utf-8", buffering=1)
    _engine_process = subprocess.Popen(
        [sys.executable, "-u", "-m", "src.main"],
        cwd=str(ROOT),
        stdout=_engine_log_handle,
        stderr=subprocess.STDOUT,
        env=env,
    )
    ENGINE_PID_FILE.write_text(str(_engine_process.pid), encoding="utf-8")
    print(f"OTR strategy engine started (PID {_engine_process.pid})", flush=True)
    print(f"Engine log: {ENGINE_LOG}", flush=True)

    watcher = threading.Thread(
        target=_monitor_engine,
        args=(_engine_process,),
        name="otr-engine-watchdog",
        daemon=True,
    )
    watcher.start()


def _handle_shutdown(signum, frame) -> None:  # noqa: ARG001
    _stop_engine()
    raise KeyboardInterrupt


if __name__ == "__main__":
    atexit.register(_stop_engine)
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    _start_engine()
    os.environ["OTR_REQUIRE_ENGINE_HEALTH"] = "1"

    port = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT", "8000"))
    uvicorn.run(
        "src.dashboard.app:app",
        host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        port=port,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
