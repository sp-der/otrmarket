from __future__ import annotations

import os
from dataclasses import dataclass


_TRUE = {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class NautilusShadowConfig:
    """Configuration for the non-authoritative Nautilus shadow engine."""

    enabled: bool = False
    strict_parity: bool = False
    record_matches: bool = False

    @classmethod
    def from_env(cls) -> "NautilusShadowConfig":
        return cls(
            enabled=os.getenv("OTR_NAUTILUS_SHADOW", "0").strip().lower() in _TRUE,
            strict_parity=os.getenv("OTR_NAUTILUS_STRICT_PARITY", "0").strip().lower() in _TRUE,
            record_matches=os.getenv("OTR_NAUTILUS_RECORD_MATCHES", "0").strip().lower() in _TRUE,
        )


def nautilus_available() -> bool:
    """Return True only when the optional NautilusTrader package is installed."""
    try:
        import nautilus_trader  # noqa: F401
    except ImportError:
        return False
    return True
