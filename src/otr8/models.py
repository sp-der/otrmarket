from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RegimeSnapshot80:
    symbol: str
    timeframe: str
    regime: str
    direction: str
    confidence: int
    directional_efficiency: float
    range_expansion: float
    overlap_ratio: float
    alternation_ratio: float
    higher_timeframe_direction: str
    legacy_regime: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateAssessment80:
    setup_id: str
    strategy: str
    timeframe: str
    direction: str
    score: float
    risk_reward: float
    narrative_score: float
    higher_timeframe_score: float
    regime_score: float
    quality_score: float
    strategy_score: float
    reasons: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reasons"] = list(self.reasons)
        return data


@dataclass(frozen=True)
class TradePlan80:
    """Strategy-side statement of WHAT OTR wants, before broker translation."""

    setup_id: str
    symbol: str
    timeframe: str
    strategy: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    risk_reward: float
    risk_dollars: float
    quality_grade: str
    arbiter_score: float
    regime: str
    created_at: datetime
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = _iso(self.created_at)
        return data


@dataclass
class DecisionTrace80:
    setup_id: str
    symbol: str
    timeframe: str
    strategy: str
    direction: str
    created_at: datetime
    source: str
    stages: list[dict[str, Any]] = field(default_factory=list)
    final_status: str = "OBSERVED"

    def add(self, stage: str, outcome: str, reason: str, details: dict[str, Any] | None = None) -> None:
        self.stages.append(
            {
                "stage": str(stage),
                "outcome": str(outcome),
                "reason": str(reason),
                "details": details or {},
            }
        )

    def finish(self, status: str) -> None:
        self.final_status = str(status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_id": self.setup_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "direction": self.direction,
            "created_at": _iso(self.created_at),
            "source": self.source,
            "stages": list(self.stages),
            "final_status": self.final_status,
        }
