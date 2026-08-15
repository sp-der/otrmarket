from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

Direction = Literal["bullish", "bearish"]
SwingKind = Literal["high", "low"]


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    ticks: int = 0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def direction(self) -> Direction | None:
        if self.close > self.open:
            return "bullish"
        if self.close < self.open:
            return "bearish"
        return None


@dataclass(frozen=True)
class SwingPoint:
    symbol: str
    timeframe: str
    kind: SwingKind
    price: float
    time: datetime
    index: int


@dataclass(frozen=True)
class FairValueGap:
    symbol: str
    timeframe: str
    direction: Direction
    lower: float
    upper: float
    formed_at: datetime
    candle1_time: datetime
    candle3_time: datetime

    @property
    def midpoint(self) -> float:
        return (self.lower + self.upper) / 2

    @property
    def width(self) -> float:
        return self.upper - self.lower

    def contains_price(self, price: float) -> bool:
        return self.lower <= price <= self.upper

    def overlaps_range(self, low: float, high: float) -> bool:
        return high >= self.lower and low <= self.upper


@dataclass(frozen=True)
class LiquiditySweep:
    symbol: str
    timeframe: str
    direction: Direction
    swept_level: float
    candle_time: datetime
    swing_time: datetime


@dataclass(frozen=True)
class Displacement:
    symbol: str
    timeframe: str
    direction: Direction
    candle_time: datetime
    low: float
    high: float
    body_ratio: float
    range_ratio: float


@dataclass(frozen=True)
class SMTDivergence:
    timeframe: str
    direction: Direction
    leader: str
    laggard: str
    event_time: datetime
    leader_level: float
    laggard_level: float
    description: str


@dataclass
class StrategySetup:
    setup_id: str
    symbol: str
    timeframe: str
    direction: Direction
    created_at: datetime
    pd_array: FairValueGap
    trigger_type: Literal["liquidity_sweep", "smt", "rejection_block"]
    trigger_details: dict[str, Any]
    displacement: Displacement
    entry_fvg: FairValueGap
    entry_price: float
    stop_price: float
    target_price: float
    risk_reward: float
    status: str = "PENDING"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = self.created_at.isoformat()
        for key in ("pd_array", "displacement", "entry_fvg"):
            nested = data[key]
            for time_key in list(nested.keys()):
                if isinstance(nested[time_key], datetime):
                    nested[time_key] = nested[time_key].isoformat()
        return data
