from __future__ import annotations

from dataclasses import dataclass, field


FUTURES_SYMBOLS = {"NQ", "ES", "GC"}


@dataclass
class StrategySession:
    """Keeps replay research isolated from unrelated live markets.

    Once a NinjaTrader futures replay tick is observed, the session remains in
    replay-isolation mode until the engine restarts. BTC quotes/candles can keep
    recording, but BTC will not generate strategy diagnostics or paper actions
    during that replay session.
    """

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
        return not (self.replay_isolation and symbol == "BTC-USD")

    def paper_updates_enabled(self, symbol: str) -> bool:
        return self.strategy_enabled(symbol)
