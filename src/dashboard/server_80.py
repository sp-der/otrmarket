from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Request

from src.storage.database_concurrency80 import install as install_sqlite_concurrency80

# Install the storage contract before any inherited dashboard module binds
# get_connection by value. This keeps schema/migration work out of live API
# requests and prevents 15x Replay from turning SQLite into a write-lock jam.
install_sqlite_concurrency80()

from src.dashboard import server_72 as core72
from src.dashboard import server_72n as dashboard72n
from src.dashboard import server_72q as verify72q
from src.dashboard import server_72s as verify72s
from src.dashboard import server_72t as legacy
from src.research.execution_lab80 import execution_lab_snapshot80
from src.storage.database import get_connection


if "decision_traces_80" not in legacy.FULL_WIPE_TABLES_72T:
    legacy.FULL_WIPE_TABLES_72T = tuple(legacy.FULL_WIPE_TABLES_72T) + ("decision_traces_80",)


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _promote_engine_80() -> str:
    """Own the final promotion hook directly instead of relying on wrapper chaining."""
    core72.promoted_engine_module = lambda requested=None: "src.main_80"
    return core72.promoted_engine_module()


def _install_overview_chart80_assets() -> None:
    """Add the NinjaTrader-backed OTR decision chart to the classic dashboard.

    The existing VERIFY accounting remains available through the API/database, but
    the visible BOT VERIFICATION card is replaced by a chart surface that shows
    the exact stored candles/setups/trades OTR receives from the bridge.
    """
    path = Path(__file__).resolve().parent / "static" / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    css_tag = '<link rel="stylesheet" href="/market/assets/overview-chart80.css?v=8.0-live1">'
    resize_css_tag = '<link rel="stylesheet" href="/market/assets/overview-chart-resize80.css?v=8.0-live2">'
    js_tag = '<script src="/market/assets/overview-chart80.js?v=8.0-live1" defer></script>'
    resize_js_tag = '<script src="/market/assets/overview-chart-resize80.js?v=8.0-live2" defer></script>'
    live_js_tag = '<script src="/market/assets/overview-chart-live80.js?v=8.0-live3" defer></script>'
    changed = False
    for tag in (css_tag, resize_css_tag):
        if tag not in text and "</head>" in text:
            text = text.replace("</head>", f"  {tag}\n</head>", 1)
            changed = True
    for tag in (js_tag, resize_js_tag, live_js_tag):
        if tag not in text and "</body>" in text:
            text = text.replace("</body>", f"{tag}\n</body>", 1)
            changed = True
    if changed:
        path.write_text(text, encoding="utf-8")


def _install_otr8_api() -> None:
    from src.dashboard import app as dashboard

    summary_path = f"{dashboard.BASE_PATH}/api/otr8"
    execution_lab_path = f"{dashboard.BASE_PATH}/api/execution-lab"

    if not any(getattr(route, "path", None) == summary_path for route in dashboard.app.routes):
        async def otr8_snapshot(request: Request):
            dashboard.require_http_auth(request)
            connection = get_connection()
            try:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_traces_80'"
                ).fetchone()
                rows = []
                counts = {}
                if exists:
                    rows = connection.execute(
                        "SELECT setup_id,symbol,timeframe,strategy,direction,source,final_status,trace_json,created_at "
                        "FROM decision_traces_80 ORDER BY created_at DESC LIMIT 30"
                    ).fetchall()
                    counts = {
                        str(status): int(count)
                        for status, count in connection.execute(
                            "SELECT final_status,COUNT(*) FROM decision_traces_80 GROUP BY final_status"
                        ).fetchall()
                    }
                recent = []
                for row in rows:
                    try:
                        trace = json.loads(row[7] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        trace = {}
                    recent.append(
                        {
                            "setup_id": row[0],
                            "symbol": row[1],
                            "timeframe": row[2],
                            "strategy": row[3],
                            "direction": row[4],
                            "source": row[5],
                            "final_status": row[6],
                            "created_at": row[8],
                            "trace": trace,
                        }
                    )
                return {
                    "profile": "OTR_8_0",
                    "engine": "src.main_80",
                    "pipeline": [
                        "candidate_collection",
                        "gold_regime",
                        "session",
                        "quality",
                        "setup_arbiter",
                        "account_guard",
                        "trade_plan",
                        "execution_handoff",
                    ],
                    "status_counts": counts,
                    "recent_decisions": recent,
                }
            finally:
                connection.close()

        dashboard.app.add_api_route(summary_path, otr8_snapshot, methods=["GET"], name="otr8_snapshot")

    if not any(getattr(route, "path", None) == execution_lab_path for route in dashboard.app.routes):
        async def execution_lab(request: Request):
            dashboard.require_http_auth(request)
            return execution_lab_snapshot80(dashboard.DB_PATH)

        dashboard.app.add_api_route(
            execution_lab_path,
            execution_lab,
            methods=["GET"],
            name="execution_lab_snapshot_80",
        )


def main() -> None:
    # A deploy/restart should not silently erase an in-progress replay. Full
    # verification wipes are now opt-in via OTR_FULL_VERIFY_WIPE_ON_BOOT.
    if _truthy_env("OTR_FULL_VERIFY_WIPE_ON_BOOT", False):
        reset_counts = legacy._full_verify_wipe_72t()
    else:
        reset_counts = {}

    # Reproduce the proven 7.2T/S/Q supervisor preparation explicitly, then hand
    # directly to 7.2N once the actual server_72 promotion hook points at 8.0.
    # This avoids another wrapper silently overwriting the requested 8.0 engine.
    legacy._install_training_api_72t()
    verify72q._normalize_verify_environment_72q()
    verify72q._wipe_verify_test_state_72q()
    run_id = verify72s._stable_verify_run_id_72s()
    verify72s._install_verify_calendar_contract_72s()
    _install_overview_chart80_assets()
    _install_otr8_api()
    engine_module = _promote_engine_80()
    print(
        "Operation 8.0 supervisor: direct clean handoff + NinjaTrader-backed Gold decision chart + Strategy Lab + Execution Lab APIs; "
        "chart live-refresh hotfix active; boot wipe disabled unless OTR_FULL_VERIFY_WIPE_ON_BOOT=true; "
        f"engine={engine_module} verify_run_id={run_id or 'inactive'} full_reset_rows={sum(reset_counts.values())}",
        flush=True,
    )
    dashboard72n.main()


if __name__ == "__main__":
    main()
