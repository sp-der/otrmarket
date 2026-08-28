from __future__ import annotations

import importlib
import os
import runpy


LEGACY_ENGINE_MODULES = {
    "",
    "src.main_58",
    "src.main_59",
    "src.main_61",
    "src.main_62",
    "src.main_63",
    "src.main_64",
    "src.main_65",
    "src.main_66",
    "src.main_67",
    "src.main_68",
    "src.main_69",
    "src.main_70",
    "src.main_71",
}


def promoted_engine_module(requested: str | None = None) -> str:
    value = (requested if requested is not None else os.getenv("OTR_ENGINE_MODULE", "")).strip()
    if value in LEGACY_ENGINE_MODULES:
        return "src.main_72"
    return value


def _install_execution_routes() -> None:
    dashboard = importlib.import_module("src.dashboard.app")
    expected_path = "/market/api/execution/status"
    if any(getattr(route, "path", None) == expected_path for route in dashboard.app.routes):
        return
    from src.execution.live.api import build_router

    dashboard.app.include_router(
        build_router(
            require_http_auth=dashboard.require_http_auth,
            require_bridge_key=dashboard.require_bridge_key,
        )
    )


def main() -> None:
    os.environ["OTR_ENGINE_MODULE"] = promoted_engine_module()
    _install_execution_routes()
    runpy.run_module("src.dashboard.server", run_name="__main__")


if __name__ == "__main__":
    main()
