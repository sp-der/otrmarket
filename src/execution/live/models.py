from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    SIM_BRIDGE = "SIM_BRIDGE"
    LIVE = "LIVE"


class CommandStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    WORKING = "WORKING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL_STATUSES = {
    CommandStatus.CLOSED.value,
    CommandStatus.CANCELLED.value,
    CommandStatus.REJECTED.value,
    CommandStatus.EXPIRED.value,
}


@dataclass(frozen=True)
class ExecutionIntent:
    command_id: str
    setup_id: str
    mode: str
    account: str
    root_symbol: str
    signal_contract: str
    execution_contract: str
    direction: str
    side: str
    quantity: int
    order_type: str
    entry_price: float
    stop_price: float
    target_price: float
    risk_dollars: float
    per_contract_risk: float
    requested_risk: float
    setup_grade: str
    quality_score: float | None
    timeframe: str
    strategy: str
    created_at: datetime
    expires_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["created_at"] = iso(self.created_at)
        data["expires_at"] = iso(self.expires_at)
        return data


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    code: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
