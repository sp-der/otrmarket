from __future__ import annotations

from typing import Any

from .config import NautilusShadowConfig, nautilus_available


def probe_nautilus() -> dict[str, Any]:
    """Return a small runtime capability report without affecting OTR execution."""
    config = NautilusShadowConfig.from_env()
    available = nautilus_available()
    version: str | None = None
    error: str | None = None

    if available:
        try:
            import nautilus_trader

            version = getattr(nautilus_trader, "__version__", None)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

    return {
        "enabled": config.enabled,
        "available": available,
        "active": bool(config.enabled and available and error is None),
        "version": version,
        "strict_parity": config.strict_parity,
        "record_matches": config.record_matches,
        "authoritative": False,
        "error": error,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(probe_nautilus(), indent=2, sort_keys=True))
