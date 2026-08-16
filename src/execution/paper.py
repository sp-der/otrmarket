from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.risk.geometry import validate_trade_geometry
from src.strategies.models import StrategySetup


_PENDING_BARS = {
    "1m": 6,
    "5m": 4,
    "15m": 3,
    "1h": 2,
}

_BAR_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}

_MAX_PREENTRY_TARGET_PROGRESS = 0.75


@dataclass
class PaperPosition:
    setup: StrategySetup
    status: str = "PENDING"
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    exit_price: float | None = None
    result_r: float | None = None
    result: str | None = None
    risk_dollars: float | None = None
    result_dollars: float | None = None
    guard_reason: str | None = None
    mfe_r: float = 0.0
    mae_r: float = 0.0
    max_favorable_price: float | None = None
    max_adverse_price: float | None = None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _pending_expiry(setup: StrategySetup) -> datetime:
    bars = _PENDING_BARS.get(setup.timeframe, 4)
    seconds = _BAR_SECONDS.get(setup.timeframe, 60) * bars
    return _aware_utc(setup.created_at) + timedelta(seconds=seconds)


def _preentry_target_progress(setup: StrategySetup, price: float) -> float:
    target_distance = abs(setup.target_price - setup.entry_price)
    if target_distance <= 0:
        return 0.0
    if setup.direction == "bullish" and price > setup.entry_price:
        return (price - setup.entry_price) / target_distance
    if setup.direction == "bearish" and price < setup.entry_price:
        return (setup.entry_price - price) / target_distance
    return 0.0


def _update_excursion(position: PaperPosition, price: float) -> None:
    """Track maximum favorable/adverse excursion in R while a trade is open."""
    setup = position.setup
    risk_distance = abs(float(setup.entry_price) - float(setup.stop_price))
    if risk_distance <= 0:
        return

    if setup.direction == "bullish":
        favorable = max(0.0, (float(price) - float(setup.entry_price)) / risk_distance)
        adverse = max(0.0, (float(setup.entry_price) - float(price)) / risk_distance)
        if position.max_favorable_price is None or price > position.max_favorable_price:
            position.max_favorable_price = float(price)
        if position.max_adverse_price is None or price < position.max_adverse_price:
            position.max_adverse_price = float(price)
    else:
        favorable = max(0.0, (float(setup.entry_price) - float(price)) / risk_distance)
        adverse = max(0.0, (float(price) - float(setup.entry_price)) / risk_distance)
        if position.max_favorable_price is None or price < position.max_favorable_price:
            position.max_favorable_price = float(price)
        if position.max_adverse_price is None or price > position.max_adverse_price:
            position.max_adverse_price = float(price)

    position.mfe_r = max(float(position.mfe_r or 0.0), favorable)
    position.mae_r = max(float(position.mae_r or 0.0), adverse)


def _invalidate_pending(
    position: PaperPosition,
    *,
    timestamp: datetime,
    price: float,
    result: str,
) -> None:
    position.status = "INVALIDATED"
    position.closed_at = timestamp
    position.exit_price = price
    position.result = result
    position.result_dollars = 0.0 if position.risk_dollars is not None else None


class PaperExecutor:
    """Research-only execution. Never sends orders to a broker."""

    def __init__(
        self,
        *,
        pending_expiry_enabled: bool = True,
        stale_preentry_enabled: bool = True,
    ):
        self.positions: dict[str, PaperPosition] = {}
        self.closed: list[PaperPosition] = []
        self.pending_expiry_enabled = pending_expiry_enabled
        self.stale_preentry_enabled = stale_preentry_enabled

    def register_setup(
        self,
        setup: StrategySetup,
        *,
        risk_dollars: float | None = None,
        guard_reason: str | None = None,
    ) -> PaperPosition:
        # Defense in depth: even if upstream setup construction regresses, an
        # inverted stop/target is never allowed into the paper order book.
        geometry = validate_trade_geometry(
            setup.symbol,
            setup.direction,
            setup.entry_price,
            setup.stop_price,
            setup.target_price,
        )
        if not geometry.valid:
            raise ValueError(geometry.reason)
        position = PaperPosition(
            setup=setup,
            risk_dollars=risk_dollars,
            guard_reason=guard_reason,
        )
        self.positions[setup.setup_id] = position
        return position

    def on_price(self, symbol: str, price: float, timestamp: datetime | None = None) -> list[PaperPosition]:
        timestamp = _aware_utc(timestamp or datetime.now(timezone.utc))
        changed: list[PaperPosition] = []

        for setup_id, position in list(self.positions.items()):
            setup = position.setup
            if setup.symbol != symbol:
                continue

            if position.status == "PENDING":
                # Live 5.x execution expires stale limits using replay market
                # time. The 4.8 shadow executor can disable this to reproduce
                # the older entry behavior without changing live execution.
                if self.pending_expiry_enabled and timestamp > _pending_expiry(setup):
                    _invalidate_pending(
                        position,
                        timestamp=timestamp,
                        price=price,
                        result="EXPIRED_BEFORE_ENTRY",
                    )
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)
                    continue

                # Avoid fills that have already blown through the protective stop.
                invalid = (
                    price <= setup.stop_price
                    if setup.direction == "bullish"
                    else price >= setup.stop_price
                )
                if invalid:
                    _invalidate_pending(
                        position,
                        timestamp=timestamp,
                        price=price,
                        result="INVALIDATED_BEFORE_ENTRY",
                    )
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)
                    continue

                # Operation 5.0+ cancels an entry after most of its objective has
                # already traded. The 4.8 shadow executor can intentionally turn
                # this off for an apples-to-apples strategy baseline.
                progress = _preentry_target_progress(setup, price)
                if self.stale_preentry_enabled and progress >= _MAX_PREENTRY_TARGET_PROGRESS:
                    _invalidate_pending(
                        position,
                        timestamp=timestamp,
                        price=price,
                        result="STALE_MOVE_BEFORE_ENTRY",
                    )
                    changed.append(position)
                    self.closed.append(position)
                    self.positions.pop(setup_id, None)
                    continue

                touched = (
                    price <= setup.entry_price
                    if setup.direction == "bullish"
                    else price >= setup.entry_price
                )
                if touched:
                    position.status = "OPEN"
                    position.opened_at = timestamp
                    position.max_favorable_price = float(setup.entry_price)
                    position.max_adverse_price = float(setup.entry_price)
                    changed.append(position)

            if position.status == "OPEN":
                _update_excursion(position, price)

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
                    if position.risk_dollars is not None:
                        position.result_dollars = (
                            -float(position.risk_dollars)
                            if stop_hit
                            else float(position.risk_dollars) * float(setup.risk_reward)
                        )
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

    @property
    def total_dollars(self) -> float:
        return sum(item.result_dollars or 0.0 for item in self.closed)
