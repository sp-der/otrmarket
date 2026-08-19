from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import uvicorn

from src.storage.database import get_connection, get_engine_state, set_engine_state
from src.storage.intelligence import ensure_intelligence_schema


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = Path(os.getenv("OTR_RUNTIME_DIR", "/tmp/otrmarket"))
ENGINE_PID_FILE = RUNTIME_DIR / "engine.pid"
RESET_STATE_KEY = "last_evaluation_reset_token"

_engine_process: subprocess.Popen | None = None
_shutting_down = False


def _reset_evaluation_history_if_requested() -> None:
    """Clear the paper evaluation ledger once for a new replay run.

    Set OTR_RESET_EVAL_TOKEN to a new unique value when a fresh evaluation is
    wanted. The token is persisted in engine_state, so ordinary Railway
    restarts/redeploys cannot accidentally erase trades collected afterward.

    Raw quotes, candles, and learning lessons are intentionally preserved.
    Replay rewind handling trims future in-memory candle/scanner state as the
    replay clock moves back.
    """
    token = os.getenv("OTR_RESET_EVAL_TOKEN", "").strip()
    if not token:
        return

    connection = get_connection()
    try:
        previous = get_engine_state(connection, RESET_STATE_KEY, "") or ""
        if previous == token:
            print("Fresh-eval reset token already applied; preserving current test trades", flush=True)
            return

        ensure_intelligence_schema(connection)
        counts = {}
        tables = (
            "paper_trades",
            "strategy_setups",
            "strategy_diagnostics",
            "trade_intelligence",
            "shadow_trades",
        )
        for table in tables:
            counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            connection.execute(f"DELETE FROM {table}")

        set_engine_state(connection, RESET_STATE_KEY, token)
        print(
            "Fresh evaluation reset complete: "
            f"{counts['paper_trades']} trades, "
            f"{counts['strategy_setups']} setups, "
            f"{counts['strategy_diagnostics']} scanner rows, "
            f"{counts['trade_intelligence']} intelligence rows, "
            f"{counts['shadow_trades']} shadow rows cleared; learning memory preserved",
            flush=True,
        )
    finally:
        connection.close()


def _stop_engine() -> None:
    global _shutting_down, _engine_process
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


def _monitor_engine(process: subprocess.Popen) -> None:
    exit_code = process.wait()
    if _shutting_down:
        return

    print(
        f"[SUPERVISOR] Strategy engine exited unexpectedly with code {exit_code}. "
        "Stopping dashboard so Railway restarts the service.",
        flush=True,
    )
    os.kill(os.getpid(), signal.SIGTERM)


def _start_engine() -> None:
    global _engine_process

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    # Operation 6.3 is the current production engine. Railway may still retain
    # an older explicit OTR_ENGINE_MODULE value, so promote known legacy values
    # automatically instead of silently booting old logic.
    requested_module = os.getenv("OTR_ENGINE_MODULE", "src.main_63").strip() or "src.main_63"
    engine_module = (
        "src.main_63"
        if requested_module in {"src.main_58", "src.main_59", "src.main_61", "src.main_62"}
        else requested_module
    )

    _engine_process = subprocess.Popen(
        [sys.executable, "-u", "-m", engine_module],
        cwd=str(ROOT),
        stdout=None,
        stderr=None,
        env=env,
    )
    ENGINE_PID_FILE.write_text(str(_engine_process.pid), encoding="utf-8")
    print(
        f"OTR strategy engine started (PID {_engine_process.pid}, module {engine_module})",
        flush=True,
    )
    print("Engine logs stream directly into Railway deploy logs", flush=True)

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

    _reset_evaluation_history_if_requested()
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
