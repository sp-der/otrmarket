from __future__ import annotations

from pathlib import Path

from fastapi import Request

from src.dashboard import server_72s as base
from src.research.live_training72t import training_snapshot_72t


def _promote_engine_72t() -> str:
    # 72t -> 72s -> 72r -> 72q -> 72n -> 72, which owns the promotion hook.
    base.base.base.base.base.promoted_engine_module = lambda requested=None: "src.main_72t"
    return base.base.base.base.base.promoted_engine_module()


def _install_training_api_72t() -> None:
    from src.dashboard import app as dashboard

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


def _patch_minimal_dashboard_assets_72t() -> None:
    static_dir = Path(__file__).resolve().parent / "static"
    path = static_dir / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    css_tag = '<link rel="stylesheet" href="/market/assets/dashboard-minimal72t.css?v=7.2t4">'
    js_tag = '<script src="/market/assets/dashboard-minimal72t.js?v=7.2t4" defer></script>'
    changed = False
    if css_tag not in text and "</head>" in text:
        text = text.replace("</head>", f"  {css_tag}\n</head>", 1)
        changed = True
    if js_tag not in text and "</body>" in text:
        text = text.replace("</body>", f"{js_tag}\n</body>", 1)
        changed = True
    if changed:
        path.write_text(text, encoding="utf-8")


def main() -> None:
    _install_training_api_72t()
    _patch_minimal_dashboard_assets_72t()
    # Reuse 7.2S's stable wipe-scoped run and unified calendar contract while
    # promoting only the engine module and the visible dashboard surface.
    base._promote_engine_72s = _promote_engine_72t
    print(
        "Operation 7.2T supervisor: minimal Gold monitor + Strategy Lab training cockpit + 4H macro context; "
        "7.2S VERIFY accounting preserved; engine=src.main_72t",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
