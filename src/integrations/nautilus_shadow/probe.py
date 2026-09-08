from __future__ import annotations

import importlib
import importlib.metadata
from typing import Any

from .config import NautilusShadowConfig, nautilus_available


_CAPABILITY_MODULES = {
    "backtest": "nautilus_trader.backtest.engine",
    "execution": "nautilus_trader.execution.engine",
    "risk": "nautilus_trader.risk.engine",
    "portfolio": "nautilus_trader.portfolio.portfolio",
    "strategy": "nautilus_trader.trading.strategy",
}


def probe_nautilus() -> dict[str, Any]:
    """Return runtime capabilities without affecting OTR execution authority."""
    config = NautilusShadowConfig.from_env()
    available = nautilus_available()
    version: str | None = None
    error: str | None = None
    capabilities: dict[str, bool] = {}

    if available:
        try:
            version = importlib.metadata.version("nautilus-trader")
            for name, module_name in _CAPABILITY_MODULES.items():
                try:
                    importlib.import_module(module_name)
                    capabilities[name] = True
                except Exception:
                    capabilities[name] = False
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

    active = bool(
        config.enabled
        and available
        and error is None
        and capabilities
        and all(capabilities.values())
    )

    return {
        "enabled": config.enabled,
        "available": available,
        "active": active,
        "version": version,
        "capabilities": capabilities,
        "strict_parity": config.strict_parity,
        "record_matches": config.record_matches,
        "authoritative": False,
        "error": error,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(probe_nautilus(), indent=2, sort_keys=True))
