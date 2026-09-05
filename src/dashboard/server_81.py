from __future__ import annotations

from fastapi import Request

from src.dashboard import server_80 as base
from src.research.conversion_funnel81 import conversion_funnel81
from src.storage.database import get_connection


def _promote_engine_81() -> str:
    base.core72.promoted_engine_module = lambda requested=None: "src.main_81"
    return base.core72.promoted_engine_module()


def _install_conversion_api_81() -> None:
    from src.dashboard import app as dashboard

    path = f"{dashboard.BASE_PATH}/api/otr81/conversion"
    if any(getattr(route, "path", None) == path for route in dashboard.app.routes):
        return

    async def conversion_snapshot(request: Request):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            return conversion_funnel81(connection, symbol="GC")
        finally:
            connection.close()

    dashboard.app.add_api_route(
        path,
        conversion_snapshot,
        methods=["GET"],
        name="gold_execution_conversion_81",
    )


def main() -> None:
    # server_80 still owns the proven dashboard/API/UI setup. Replace only its
    # engine promotion hook so the same supervisor launches Operation 8.1, then
    # add the 8.1 candidate-to-fill conversion microscope alongside it.
    base._promote_engine_80 = _promote_engine_81
    _install_conversion_api_81()
    print(
        "Operation 8.1 supervisor: Operation 8.0 dashboard + Gold Execution Conversion engine; "
        "first-touch zones, registration-time entry life, dynamic R:R, $750/$500 eval sizing, "
        "and detected->qualified->selected->registered->filled conversion telemetry enabled.",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
