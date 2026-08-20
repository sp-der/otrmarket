from __future__ import annotations

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
}


def promoted_engine_module(requested: str | None = None) -> str:
    value = (requested if requested is not None else os.getenv("OTR_ENGINE_MODULE", "")).strip()
    if value in LEGACY_ENGINE_MODULES:
        return "src.main_70"
    return value


def main() -> None:
    os.environ["OTR_ENGINE_MODULE"] = promoted_engine_module()
    runpy.run_module("src.dashboard.server", run_name="__main__")


if __name__ == "__main__":
    main()
