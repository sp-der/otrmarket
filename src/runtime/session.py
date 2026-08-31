from __future__ import annotations

import os
from dataclasses import dataclass, field


FUTURES_SYMBOLS = {"NQ", "ES", "GC"}


def _active_strategy_symbols() -> set[str]:
    """Markets allowed to originate new strategy decisions.

    Operation 7.2I defaults the active strategy lane to Gold only while keeping
    NQ/ES market ingestion and paper-position updates alive. Set
    OTR_ACTIVE_STRATEGY_SYMBOLS=NQ,ES,GC later to restore all futures without a
    code change.
    """
    raw = os.getenv("OTR_ACTIVE_STRATEGY_SYMBOLS", "GC")
    requested = {item.strip().upper() for item in raw.split(",") if item.strip()}
    return requested & FUTURES_SYMBOLS or {"GC"}


ACTIVE_STRATEGY_SYMBOLS = _active_strategy_symbols()


@dataclass
class StrategySession:
    """Keeps replay research isolated and controls active strategy markets."""

    replay_isolation: bool = False
    replay_symbols: set[str] = field(default_factory=set)

    def observe(self, symbol: str, mode: str) -> bool:
        """Observe a stream mode and return True on first replay activation."""
        activated = False
        if symbol in FUTURES_SYMBOLS and mode == "REPLAY":
            self.replay_symbols.add(symbol)
            if not self.replay_isolation:
                self.replay_isolation = True
                activated = True
        return activated

    def strategy_enabled(self, symbol: str) -> bool:
        if symbol in FUTURES_SYMBOLS:
            return symbol in ACTIVE_STRATEGY_SYMBOLS
        return not (self.replay_isolation and symbol == "BTC-USD")

    def paper_updates_enabled(self, symbol: str) -> bool:
        # Existing NQ/ES paper positions must still receive ticks so stops,
        # targets, and exits can finish normally even while new setups are off.
        if symbol in FUTURES_SYMBOLS:
            return True
        return self.strategy_enabled(symbol)
