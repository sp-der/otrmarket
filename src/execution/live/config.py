from __future__ import annotations

from dataclasses import dataclass
import os

from .models import ExecutionMode


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class ExecutionConfig:
    mode: ExecutionMode = ExecutionMode.PAPER
    armed: bool = False
    live_allowed: bool = False
    certified: bool = False
    account: str = "Sim101"
    max_micros: int = 1
    max_risk_dollars: float = 350.0
    command_ttl_seconds: int = 45
    heartbeat_ttl_seconds: int = 15
    reconciliation_ttl_seconds: int = 15
    claimed_redelivery_seconds: int = 5

    @classmethod
    def from_env(cls) -> "ExecutionConfig":
        raw_mode = os.getenv("OTR_EXECUTION_MODE", "PAPER").strip().upper()
        try:
            mode = ExecutionMode(raw_mode)
        except ValueError:
            mode = ExecutionMode.PAPER
        return cls(
            mode=mode,
            armed=_bool("OTR_EXECUTION_ARMED", False),
            live_allowed=_bool("OTR_EXECUTION_LIVE_ALLOWED", False),
            certified=_bool("OTR_EXECUTION_CERTIFIED", False),
            account=os.getenv("OTR_EXECUTION_ACCOUNT", "Sim101").strip() or "Sim101",
            max_micros=max(1, _int("OTR_EXECUTION_MAX_MICROS", 1)),
            max_risk_dollars=max(1.0, _float("OTR_EXECUTION_MAX_RISK_DOLLARS", 350.0)),
            command_ttl_seconds=max(5, _int("OTR_EXECUTION_COMMAND_TTL_SECONDS", 45)),
            heartbeat_ttl_seconds=max(5, _int("OTR_EXECUTION_HEARTBEAT_TTL_SECONDS", 15)),
            reconciliation_ttl_seconds=max(5, _int("OTR_EXECUTION_RECONCILIATION_TTL_SECONDS", 15)),
            claimed_redelivery_seconds=max(2, _int("OTR_EXECUTION_CLAIM_REDELIVERY_SECONDS", 5)),
        )

    @property
    def is_sim_account(self) -> bool:
        return self.account.strip().lower().startswith("sim")
