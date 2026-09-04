from __future__ import annotations

from fastapi import Request

from src.dashboard import server_72s as base
from src.research.live_training72t import training_snapshot_72t


def _promote_engine_72t() -> str:
    # 72t -> 72s -> 72r -> 72q -> 72n -> 72, which owns the promotion hook.
    base.base.base.base.base.promoted_engine_module = lambda requested=None: "src.main_72t"
    return base.base.base.base.base.promoted_engine_module()


def _install_training_api_72t() -> None:
    from src.dashboard import app as dashboard

    # Keep 4H chart/context and Strategy Lab available, but let the normal
    # dashboard render its original pre-7.2T Overview again.
    dashboard.CHART_TIMEFRAMES.add("4h")
    expected = f"{dashboard.BASE_PATH}/api/training"
    if any(getattr(route, "path", None) == expected for route in dashboard.app.routes):
        return

    async def training_lab_snapshot(request: Request):
        dashboard.require_http_auth(request)
        return training_snapshot_72t(dashboard.DB_PATH, dashboard.RESEARCH_DB_PATH)

    dashboard.app.add_api_route(
        expected,
        training_lab_snapshot,
        methods=["GET"],
        name="training_lab_snapshot_72t",
    )


def main() -> None:
    _install_training_api_72t()
    # Do not inject dashboard-minimal72t.css/js. The base 7.2S dashboard owns
    # the visible Overview again: Total R, Win Rate, All-Time P/L, Today's P/L,
    # Markets, Equity Curve, Trade Queue, Strategy Progress and recent trades.
    base._promote_engine_72s = _promote_engine_72t
    print(
        "Operation 7.2T supervisor: classic pre-simplify Overview restored + Strategy Lab + 4H macro context; "
        "7.2S VERIFY accounting preserved; engine=src.main_72t",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
