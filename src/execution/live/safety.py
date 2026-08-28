from __future__ import annotations

from datetime import datetime, timezone
import os

from src.risk.geometry import validate_trade_geometry

from .config import ExecutionConfig
from .models import ExecutionIntent, ExecutionMode, SafetyDecision, utc_now
from .store import get_state


def _age_seconds(timestamp: datetime | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max(0.0, (now - timestamp.astimezone(timezone.utc)).total_seconds())


def evaluate_intent(connection, intent: ExecutionIntent, config: ExecutionConfig, *, now: datetime | None = None) -> SafetyDecision:
    now = now or utc_now()

    kill_switch, _ = get_state(connection, "kill_switch", False)
    if bool(kill_switch) or os.getenv("OTR_EXECUTION_KILL_SWITCH", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return SafetyDecision(False, "KILL_SWITCH", "OTR execution kill switch is active.")

    if intent.mode != config.mode.value:
        return SafetyDecision(False, "MODE_MISMATCH", f"Intent mode {intent.mode!r} does not match configured mode {config.mode.value!r}.")
    if intent.account != config.account:
        return SafetyDecision(False, "ACCOUNT_MISMATCH", f"Intent account {intent.account!r} does not match configured account {config.account!r}.")

    geometry = validate_trade_geometry(intent.root_symbol, intent.direction, intent.entry_price, intent.stop_price, intent.target_price)
    if not geometry.valid:
        return SafetyDecision(False, "INVALID_GEOMETRY", geometry.reason)

    if intent.quantity < 1 or intent.quantity > config.max_micros:
        return SafetyDecision(False, "SIZE_LIMIT", f"Intent quantity {intent.quantity} exceeds the configured 1-{config.max_micros} micro range.")
    if intent.risk_dollars <= 0 or intent.risk_dollars > config.max_risk_dollars + 1e-9:
        return SafetyDecision(False, "RISK_LIMIT", f"Intent risk ${intent.risk_dollars:.2f} exceeds the ${config.max_risk_dollars:.2f} execution cap.")

    if config.mode == ExecutionMode.PAPER:
        return SafetyDecision(False, "PAPER_ONLY", "Operation 7.2 is in PAPER mode; broker commands are not emitted.")
    if not config.armed:
        return SafetyDecision(False, "NOT_ARMED", "Broker execution is not armed. Set OTR_EXECUTION_ARMED=1 only during supervised certification.")

    if config.mode == ExecutionMode.SIM_BRIDGE:
        if not config.is_sim_account:
            return SafetyDecision(False, "SIM_ACCOUNT_REQUIRED", f"SIM_BRIDGE refuses non-simulation account {config.account!r}.")
    elif config.mode == ExecutionMode.LIVE:
        if not (config.live_allowed and config.certified):
            return SafetyDecision(False, "LIVE_INTERLOCK", "LIVE mode requires both OTR_EXECUTION_LIVE_ALLOWED=1 and OTR_EXECUTION_CERTIFIED=1.")
    else:
        return SafetyDecision(False, "UNKNOWN_MODE", f"Unsupported execution mode: {config.mode!s}")

    if intent.expires_at <= now:
        return SafetyDecision(False, "EXPIRED", "Execution command expired before dispatch.")

    reconciliation, reconciliation_at = get_state(connection, "reconciliation", None)
    recon_age = _age_seconds(reconciliation_at, now)
    if not reconciliation or not bool(reconciliation.get("ok")):
        return SafetyDecision(False, "RECONCILIATION_REQUIRED", "No clean broker reconciliation snapshot is available.", {"reconciliation": reconciliation})
    if recon_age is None or recon_age > config.reconciliation_ttl_seconds:
        return SafetyDecision(False, "RECONCILIATION_STALE", f"Broker reconciliation is older than {config.reconciliation_ttl_seconds}s.", {"age_seconds": recon_age})
    if str(reconciliation.get("account") or "") != config.account:
        return SafetyDecision(False, "ACCOUNT_MISMATCH", "Reconciled broker account does not match OTR execution account.")

    return SafetyDecision(True, "APPROVED", "Execution intent passed Operation 7.2 fail-closed safety interlocks.", {"mode": config.mode.value, "account": config.account, "quantity": intent.quantity, "risk_dollars": intent.risk_dollars})


def bridge_dispatch_ready(connection, config: ExecutionConfig, *, now: datetime | None = None) -> SafetyDecision:
    now = now or utc_now()
    kill_switch, _ = get_state(connection, "kill_switch", False)
    if bool(kill_switch) or os.getenv("OTR_EXECUTION_KILL_SWITCH", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return SafetyDecision(False, "KILL_SWITCH", "OTR execution kill switch is active.")
    if config.mode == ExecutionMode.PAPER:
        return SafetyDecision(False, "PAPER_ONLY", "Execution bridge is disabled in PAPER mode.")
    if not config.armed:
        return SafetyDecision(False, "NOT_ARMED", "Execution bridge is not armed.")
    if config.mode == ExecutionMode.SIM_BRIDGE and not config.is_sim_account:
        return SafetyDecision(False, "SIM_ACCOUNT_REQUIRED", "SIM_BRIDGE requires a simulation account.")
    if config.mode == ExecutionMode.LIVE and not (config.live_allowed and config.certified):
        return SafetyDecision(False, "LIVE_INTERLOCK", "LIVE execution interlock is not fully satisfied.")

    reconciliation, reconciliation_at = get_state(connection, "reconciliation", None)
    age = _age_seconds(reconciliation_at, now)
    if not reconciliation or not reconciliation.get("ok"):
        return SafetyDecision(False, "RECONCILIATION_REQUIRED", "Broker snapshot is not reconciled.")
    if age is None or age > config.reconciliation_ttl_seconds:
        return SafetyDecision(False, "RECONCILIATION_STALE", "Broker reconciliation snapshot is stale.")
    if str(reconciliation.get("account") or "") != config.account:
        return SafetyDecision(False, "ACCOUNT_MISMATCH", "Broker snapshot account differs from configured account.")
    return SafetyDecision(True, "READY", "Execution bridge is armed and reconciled.")
