from __future__ import annotations

from datetime import datetime, timezone
import os

from src.research.execution.contracts import micro_spec
from src.risk.geometry import validate_trade_geometry

from .config import ExecutionConfig
from .models import ExecutionIntent, ExecutionMode, SafetyDecision, iso, utc_now
from .store import daily_execution_ledger, get_state, latest_market_data_at, latest_market_event_at


def _age_seconds(timestamp: datetime | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max(0.0, (now - timestamp.astimezone(timezone.utc)).total_seconds())


VERIFY_TRADING_MODES = {"VERIFY", "VERIFICATION", "TEST"}


def _verify_mode_block() -> SafetyDecision | None:
    """VERIFY bypasses every account governor (daily loss, MLL, concurrency).

    That is acceptable for paper research only; broker transmission must
    never run while it is active.
    """
    if os.getenv("OTR_TRADING_MODE", "").strip().upper() in VERIFY_TRADING_MODES:
        return SafetyDecision(
            False,
            "VERIFY_MODE_FORBIDDEN",
            "OTR_TRADING_MODE=VERIFY disables account governors; broker execution is refused.",
        )
    return None


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
    verify_block = _verify_mode_block()
    if verify_block is not None:
        return verify_block

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

    integrity = _instrument_integrity(intent, config)
    if integrity is not None:
        return integrity

    data_at = latest_market_data_at(connection, intent.root_symbol)
    data_age = _age_seconds(data_at, now)
    if data_age is None or data_age > config.max_data_age_seconds:
        return SafetyDecision(
            False,
            "MARKET_DATA_STALE",
            f"Newest {intent.root_symbol} quote is older than {config.max_data_age_seconds}s; new entries are blocked.",
            {"age_seconds": data_age},
        )
    event_at = latest_market_event_at(connection, intent.root_symbol)
    if event_at is None or abs((now - event_at).total_seconds()) > config.max_data_age_seconds:
        return SafetyDecision(
            False,
            "MARKET_DATA_NOT_CURRENT",
            (
                f"Newest {intent.root_symbol} exchange timestamp is not within {config.max_data_age_seconds}s of the "
                "wall clock (restart backlog, replay data or bridge clock fault)."
            ),
            {"event_at": iso(event_at) if event_at else None},
        )

    reconciliation, reconciliation_at = get_state(connection, "reconciliation", None)
    recon_age = _age_seconds(reconciliation_at, now)
    if not reconciliation or not bool(reconciliation.get("ok")):
        return SafetyDecision(False, "RECONCILIATION_REQUIRED", "No clean broker reconciliation snapshot is available.", {"reconciliation": reconciliation})
    if recon_age is None or recon_age > config.reconciliation_ttl_seconds:
        return SafetyDecision(False, "RECONCILIATION_STALE", f"Broker reconciliation is older than {config.reconciliation_ttl_seconds}s.", {"age_seconds": recon_age})
    if str(reconciliation.get("account") or "") != config.account:
        return SafetyDecision(False, "ACCOUNT_MISMATCH", "Reconciled broker account does not match OTR execution account.")

    ledger = daily_execution_ledger(
        connection,
        account=config.account,
        now=now,
        tz_name=config.session_tz,
        roll_hour=config.trading_day_roll_hour,
        commission_per_contract_rt=config.commission_per_contract_rt,
    )
    if ledger["active_commands"] >= config.max_active_commands:
        return SafetyDecision(
            False,
            "EXPOSURE_LIMIT",
            f"{ledger['active_commands']} broker command(s) already active; limit is {config.max_active_commands}.",
            {"ledger": ledger},
        )
    if ledger["trades"] >= config.max_trades_per_day:
        return SafetyDecision(
            False,
            "DAILY_TRADE_LIMIT",
            f"{ledger['trades']} broker trade(s) this trading day; limit is {config.max_trades_per_day}.",
            {"ledger": ledger},
        )
    worst_case = ledger["loss_used"] + ledger["open_risk"] + float(intent.risk_dollars)
    if worst_case > config.max_daily_loss_dollars + 1e-9:
        return SafetyDecision(
            False,
            "DAILY_LOSS_LIMIT",
            (
                f"Daily loss used ${ledger['loss_used']:.2f} + open risk ${ledger['open_risk']:.2f} + new risk "
                f"${float(intent.risk_dollars):.2f} would exceed the ${config.max_daily_loss_dollars:.2f} daily limit."
            ),
            {"ledger": ledger},
        )

    return SafetyDecision(True, "APPROVED", "Execution intent passed Operation 7.2 fail-closed safety interlocks.", {"mode": config.mode.value, "account": config.account, "quantity": intent.quantity, "risk_dollars": intent.risk_dollars, "ledger": ledger})


def _instrument_integrity(intent: ExecutionIntent, config: ExecutionConfig) -> SafetyDecision | None:
    """Refuse symbols outside the allowlist and any contract/multiplier drift."""
    root = str(intent.root_symbol or "").upper()
    if root not in config.allowed_symbols:
        return SafetyDecision(
            False,
            "SYMBOL_NOT_ALLOWED",
            f"{root or 'unknown'} is not an authorised execution market (allowed: {', '.join(config.allowed_symbols) or 'none'}).",
        )
    try:
        spec = micro_spec(root)
    except ValueError as exc:
        return SafetyDecision(False, "CONTRACT_INVALID", str(exc))
    contract = str(intent.execution_contract or "").strip().upper()
    parts = contract.split()
    if len(parts) != 2 or parts[0] != spec.instrument:
        return SafetyDecision(
            False,
            "CONTRACT_UNRESOLVED",
            f"Execution contract {contract or 'missing'!r} must name the {spec.instrument} expiry (e.g. '{spec.instrument} 12-26').",
        )
    expected_per_contract = abs(float(intent.entry_price) - float(intent.stop_price)) * float(spec.point_value)
    if expected_per_contract <= 0 or abs(expected_per_contract - float(intent.per_contract_risk)) > 0.01:
        return SafetyDecision(
            False,
            "CONTRACT_MULTIPLIER_MISMATCH",
            f"Per-contract risk ${float(intent.per_contract_risk):.2f} does not match the canonical {spec.instrument} "
            f"multiplier (${expected_per_contract:.2f}).",
        )
    if abs(float(intent.per_contract_risk) * int(intent.quantity) - float(intent.risk_dollars)) > 0.01:
        return SafetyDecision(False, "RISK_ARITHMETIC_MISMATCH", "Intent risk does not equal quantity x per-contract risk.")
    return None


def bridge_dispatch_ready(connection, config: ExecutionConfig, *, now: datetime | None = None) -> SafetyDecision:
    now = now or utc_now()
    kill_switch, _ = get_state(connection, "kill_switch", False)
    if bool(kill_switch) or os.getenv("OTR_EXECUTION_KILL_SWITCH", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return SafetyDecision(False, "KILL_SWITCH", "OTR execution kill switch is active.")
    if config.mode == ExecutionMode.PAPER:
        return SafetyDecision(False, "PAPER_ONLY", "Execution bridge is disabled in PAPER mode.")
    if not config.armed:
        return SafetyDecision(False, "NOT_ARMED", "Execution bridge is not armed.")
    verify_block = _verify_mode_block()
    if verify_block is not None:
        return verify_block
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
