from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import NautilusShadowConfig, nautilus_available
from .parity import ExecutionSnapshot, ParityResult, compare_execution_snapshots


@dataclass
class NautilusShadowBridge:
    """Non-authoritative bridge used to compare OTR execution with NautilusTrader.

    This bridge is deliberately fail-open for OTR: missing Nautilus dependencies,
    conversion errors, or shadow failures must never block an OTR setup or order.
    """

    config: NautilusShadowConfig

    def __post_init__(self) -> None:
        self._available = nautilus_available()
        self._last_error: str | None = None

    @property
    def active(self) -> bool:
        return bool(self.config.enabled and self._available)

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def health(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "dependency_available": self._available,
            "active": self.active,
            "authoritative": False,
            "last_error": self._last_error,
        }

    def compare(
        self,
        otr_snapshot: ExecutionSnapshot,
        nautilus_snapshot: ExecutionSnapshot,
    ) -> ParityResult | None:
        if not self.active:
            return None
        try:
            result = compare_execution_snapshots(otr_snapshot, nautilus_snapshot)
            if self.config.strict_parity and not result.matched:
                self._last_error = "; ".join(result.differences)
            return result
        except Exception as exc:  # shadow errors may never stop OTR execution
            self._last_error = f"{type(exc).__name__}: {exc}"
            return None
