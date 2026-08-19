from __future__ import annotations

import atexit
from datetime import datetime, timezone
import json
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
STATIC_DIR = ROOT / "src" / "dashboard" / "static"
RUNTIME_MANIFEST_FILE = STATIC_DIR / "runtime-build.json"
RESET_STATE_KEY = "last_evaluation_reset_token"

_engine_process: subprocess.Popen | None = None
_shutting_down = False


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


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


def _write_runtime_manifest(engine_module: str) -> None:
    """Publish a non-secret audit of the rules the production service is using."""
    execution_timeframe = os.getenv("OTR_EXECUTION_TIMEFRAME", "ALL").strip() or "ALL"
    manifest = {
        "build": {
            "engine_module": engine_module,
            "operation": "Operation 6.6",
            "commit_sha": (
                os.getenv("RAILWAY_GIT_COMMIT_SHA", "").strip()
                or os.getenv("OTR_BUILD_SHA", "").strip()
                or "unknown"
            ),
            "execution_mode": "PAPER ONLY",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "rules": [
            {
                "name": "Active markets",
                "value": "NQ / ES / GC (BTC execution disabled)",
                "source": "src/main_59.py",
            },
            {
                "name": "Autonomous timeframes",
                "value": f"{execution_timeframe} · 1m / 5m / 15m / 1h supported and eligible when profile is ALL",
                "source": "src/risk/session_consistency.py",
            },
            {
                "name": "Intrabar acceleration",
                "value": "1m / 5m / 15m / 1h · probes every 0.25s · 3 confirmations · ≥0.75s stable",
                "source": "src/main_66.py + src/main_65.py",
            },
            {
                "name": "Base risk / trade",
                "value": f"${_env_float('EVAL_RISK_PER_TRADE', 300.0):.0f}",
                "source": "src/risk/evaluation.py",
            },
            {
                "name": "Fast-eval session profit lock",
                "value": (
                    f"${_env_float('EVAL_SESSION_PROFIT_CAP', 1500.0):.0f} realized max per bucket · "
                    "Asia 18-21 ET · Tokyo 21-02 ET · London 02-08 ET · New York 08-16:30 ET"
                ),
                "source": "src/risk/evaluation.py",
            },
            {
                "name": "Fast-eval profit pacing",
                "value": "Planned trade profit is tapered to the remaining session budget instead of intentionally oversizing past the session cap",
                "source": "src/main_66.py",
            },
            {
                "name": "Internal daily stop",
                "value": f"${_env_float('EVAL_INTERNAL_DAILY_STOP', 900.0):.0f}",
                "source": "src/risk/evaluation.py",
            },
            {
                "name": "Daily / loss locks",
                "value": (
                    f"{_env_int('EVAL_MAX_TRADES_PER_DAY', 8)} trades max · "
                    f"{_env_int('EVAL_MAX_CONSECUTIVE_LOSSES', 2)} consecutive losses max · 1 concurrent position"
                ),
                "source": "src/risk/evaluation.py",
            },
            {
                "name": "ICT entry sequence",
                "value": "PD array → sweep/SMT → displacement → FVG → 50-79% retracement → valid R:R",
                "source": "src/strategies/confluence.py",
            },
            {
                "name": "Entry development window",
                "value": "15 bars after displacement for FVG / 50-79 / R:R development",
                "source": "src/strategies/confluence.py",
            },
            {
                "name": "No-chase protection",
                "value": "Suppress original entry once ≥75% of planned objective already traded",
                "source": "src/main_64.py + src/execution/paper.py",
            },
            {
                "name": "B+ tier",
                "value": "Reduced risk only · 1.50R+ adaptive eligibility · max 2 B+ futures trades/day · disabled after a realized futures loss",
                "source": "src/main_61.py + src/main_59.py",
            },
            {
                "name": "Countertrend tier",
                "value": "1m/5m only · 1.75R+ · 80/100 quality floor · reduced risk 35-50%",
                "source": "src/main_64.py",
            },
            {
                "name": "Post-loss behavior",
                "value": "30-minute futures-wide reset plus stronger follow-up quality / reduced-risk logic",
                "source": "src/main_59.py + src/main_61.py",
            },
        ],
    }
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_MANIFEST_FILE.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        f"Runtime build manifest published for {engine_module} at {RUNTIME_MANIFEST_FILE}",
        flush=True,
    )


def _start_engine() -> None:
    global _engine_process

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    # Operation 6.6 is the current production engine. Promote known legacy
    # values automatically so an old Railway variable cannot silently boot an
    # older strategy chain.
    requested_module = os.getenv("OTR_ENGINE_MODULE", "src.main_66").strip() or "src.main_66"
    engine_module = (
        "src.main_66"
        if requested_module in {
            "src.main_58",
            "src.main_59",
            "src.main_61",
            "src.main_62",
            "src.main_63",
            "src.main_64",
            "src.main_65",
        }
        else requested_module
    )
    os.environ["OTR_ACTIVE_ENGINE_MODULE"] = engine_module
    env["OTR_ACTIVE_ENGINE_MODULE"] = engine_module

    _engine_process = subprocess.Popen(
        [sys.executable, "-u", "-m", engine_module],
        cwd=str(ROOT),
        stdout=None,
        stderr=None,
        env=env,
    )
    ENGINE_PID_FILE.write_text(str(_engine_process.pid), encoding="utf-8")
    _write_runtime_manifest(engine_module)
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
