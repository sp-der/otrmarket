from __future__ import annotations

import json

from fastapi import Request

from src.dashboard import server_72t as legacy
from src.research.execution_lab80 import execution_lab_snapshot80
from src.storage.database import get_connection


if "decision_traces_80" not in legacy.FULL_WIPE_TABLES_72T:
    legacy.FULL_WIPE_TABLES_72T = tuple(legacy.FULL_WIPE_TABLES_72T) + ("decision_traces_80",)


def _promote_engine_80() -> str:
    legacy.base.base.base.base.base.promoted_engine_module = lambda requested=None: "src.main_80"
    return legacy.base.base.base.base.base.promoted_engine_module()


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
    _install_otr8_api()
    legacy._promote_engine_72t = _promote_engine_80
    print(
        "Operation 8.0 supervisor: classic dashboard + Strategy Lab + Execution Lab APIs; "
        "engine=src.main_80; 7.2T full-reset compatibility preserved",
        flush=True,
    )
    legacy.main()


if __name__ == "__main__":
    main()
