from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.strategies.models import StrategySetup


@dataclass
class PaperPosition:
    setup: StrategySetup
    status: str = "PENDING"
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_price: float | None = None
    result_r: float | None = None
    result: str | None = None


class PaperExecutor:
    """Research-only execution. Never sends orders to a broker."""

    def __init__(self):
        self.positions: dict[str, PaperPosition] = {}
        self.closed: list[PaperPosition] = []

    def register_setup(self, setup: StrategySetup) -> PaperPosition:
        position = PaperPosition(setup=setup)
        self.positions[setup.setup_id] = position
        return position

    def on_price(self, symbol: str, price: float, timestamp: datetime | None = None) -> list[PaperPosition]:
        timestamp = timestamp or datetime.now(timezone.utc)
        changed: list[PaperPosition] = []

        for setup_id, position in list(self.positions.items()):
            setup = position.setup
            if setup.symbol != symbol:
                continue

            if position.status == "PENDING":
                touched = (
                    price <= setup.entry_price
                    if setup.direction == "bullish"
                    else price >= setup.entry_price
                )
                # Avoid fills that have already blown through the stop.
                invalid = (
                    price <= setup.stop_price
                    if setup.direction == "bullish"
                    else price >= setup.stop_price
                )
                if invalid:
                    position.status = "INVALIDATED"
                    position.closed_at = timestamp
                    position.exit_price = price
                    position.result = "INVALIDATED_BEFORE_ENTRY"
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)
                    continue
                if touched:
                    position.status = "OPEN"
                    position.opened_at = timestamp
                    changed.append(position)

            if position.status == "OPEN":
                if setup.direction == "bullish":
                    stop_hit = price <= setup.stop_price
                    target_hit = price >= setup.target_price
                else:
                    stop_hit = price >= setup.stop_price
                    target_hit = price <= setup.target_price

                if stop_hit or target_hit:
                    position.status = "CLOSED"
                    position.closed_at = timestamp
                    position.exit_price = setup.stop_price if stop_hit else setup.target_price
                    position.result = "LOSS" if stop_hit else "WIN"
                    position.result_r = -1.0 if stop_hit else setup.risk_reward
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)

        return changed

    @property
    def open_count(self) -> int:
        return sum(1 for item in self.positions.values() if item.status == "OPEN")

    @property
    def pending_count(self) -> int:
        return sum(1 for item in self.positions.values() if item.status == "PENDING")

    @property
    def total_r(self) -> float:
        return sum(item.result_r or 0.0 for item in self.closed)
