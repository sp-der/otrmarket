from __future__ import annotations

from dataclasses import dataclass

ALLOWED_FUTURES = {"NQ", "ES", "GC"}


def normalize_bridge_symbol(value: str) -> str:
    symbol = (value or "").strip().upper()
    if symbol not in ALLOWED_FUTURES:
        raise ValueError(f"Unsupported futures symbol: {value}")
    return symbol


def source_name(contract: str) -> str:
    contract = (contract or "").strip()
    return f"ninjatrader:{contract}" if contract else "ninjatrader:unknown"


@dataclass(frozen=True)
class NormalizedBridgeTick:
    symbol: str
    contract: str
    timestamp: str
    last: float
    bid: float | None
    ask: float | None
    volume: int | None
