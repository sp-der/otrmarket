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


def _symbols(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = tuple(item.strip().upper() for item in raw.split(",") if item.strip())
    # An empty allowlist means nothing may be transmitted, never "everything".
    return values


DATA_CLOCKS = {"WALL", "REPLAY"}


def _data_clock(value: str | None) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if candidate in DATA_CLOCKS else "WALL"


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
    # Pre-live audit hardening. These are execution-layer (broker command)
    # limits enforced independently of the paper/strategy ledger. They only
    # matter once broker transmission is possible (SIM_BRIDGE/LIVE + armed).
    allowed_symbols: tuple[str, ...] = ("GC",)
    max_active_commands: int = 1
    # Account-level daily loss ceiling for broker commands. from_env falls back
    # to the paper guard's EVAL_INTERNAL_DAILY_STOP so the two layers can never
    # silently disagree. This is OTR's internal ceiling, not a firm rule.
    max_daily_loss_dollars: float = 750.0
    # 0 = unlimited. OTR deliberately does not cap qualified trades per day;
    # daily loss, open risk and the one-active-idea limit govern instead.
    max_trades_per_day: int = 0
    max_data_age_seconds: int = 10
    # Explicit market-data clock policy (never auto-detected):
    #   WALL   - LIVE / real-time SIM. Exchange timestamps must be current
    #            relative to the real wall clock.
    #   REPLAY - intentional NinjaTrader historical Market Replay. Chronology
    #            is judged against the replay stream's own event time.
    # Any unknown value falls back to WALL, which refuses historical data.
    data_clock: str = "WALL"
    max_signal_lag_seconds: int = 120
    commission_per_contract_rt: float = 0.0
    session_tz: str = "America/New_York"
    trading_day_roll_hour: int = 18

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
            allowed_symbols=_symbols("OTR_EXECUTION_ALLOWED_SYMBOLS", ("GC",)),
            max_active_commands=max(1, _int("OTR_EXECUTION_MAX_ACTIVE_COMMANDS", 1)),
            max_daily_loss_dollars=max(
                1.0,
                _float("OTR_EXECUTION_MAX_DAILY_LOSS", _float("EVAL_INTERNAL_DAILY_STOP", 750.0)),
            ),
            max_trades_per_day=max(0, _int("OTR_EXECUTION_MAX_TRADES_PER_DAY", 0)),
            max_data_age_seconds=max(1, _int("OTR_EXECUTION_MAX_DATA_AGE_SECONDS", 10)),
            data_clock=_data_clock(os.getenv("OTR_EXECUTION_DATA_CLOCK", "WALL")),
            max_signal_lag_seconds=max(1, _int("OTR_EXECUTION_MAX_SIGNAL_LAG_SECONDS", 120)),
            commission_per_contract_rt=max(0.0, _float("OTR_EXECUTION_COMMISSION_PER_CONTRACT_RT", 0.0)),
            session_tz=os.getenv("OTR_SESSION_TZ", "America/New_York").strip() or "America/New_York",
            trading_day_roll_hour=min(23, max(0, _int("OTR_EXECUTION_TRADING_DAY_ROLL_HOUR", 18))),
        )

    @property
    def replay_clock(self) -> bool:
        return self.data_clock == "REPLAY"

    @property
    def is_sim_account(self) -> bool:
        return self.account.strip().lower().startswith("sim")
