from __future__ import annotations

from datetime import datetime, timezone
import os

from src.research.execution.contracts import micro_spec
from src.risk.geometry import validate_trade_geometry

from .config import ExecutionConfig
from .models import ExecutionIntent, ExecutionMode, SafetyDecision, iso, utc_now
from .store import daily_execution_ledger, get_state, latest_market_data_at, latest_market_event_at, recent_market_event_max


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
    replay_block = _replay_clock_block(config)
    if replay_block is not None:
        return replay_block

    if intent.expires_at <= now:
        return SafetyDecision(False, "EXPIRED", "Execution command expired before dispatch.")

    integrity = _instrument_integrity(intent, config)
    if integrity is not None:
        return integrity

    freshness, ledger_reference = _market_data_freshness(connection, intent, config, now)
    if freshness is not None:
        return freshness

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
        now=ledger_reference,
        tz_name=config.session_tz,
        roll_hour=config.trading_day_roll_hour,
        commission_per_contract_rt=config.commission_per_contract_rt,
        replay_clock=config.replay_clock,
    )
    if ledger["active_commands"] >= config.max_active_commands:
        return SafetyDecision(
            False,
            "EXPOSURE_LIMIT",
            f"{ledger['active_commands']} broker command(s) already active; limit is {config.max_active_commands}.",
            {"ledger": ledger},
        )
    # Optional, disabled by default (0 = unlimited qualified trades per day).
    if config.max_trades_per_day > 0 and ledger["trades"] >= config.max_trades_per_day:
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


def _replay_clock_block(config: ExecutionConfig) -> SafetyDecision | None:
    """Historical replay chronology is a simulation-only policy."""
    if config.replay_clock and config.mode != ExecutionMode.SIM_BRIDGE:
        return SafetyDecision(
            False,
            "REPLAY_CLOCK_FORBIDDEN",
            "OTR_EXECUTION_DATA_CLOCK=REPLAY is only valid with OTR_EXECUTION_MODE=SIM_BRIDGE; LIVE always uses the wall clock.",
        )
    return None


def _signal_event_at(intent: ExecutionIntent) -> datetime | None:
    raw = (intent.metadata or {}).get("signal_event_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _market_data_freshness(connection, intent: ExecutionIntent, config: ExecutionConfig, now: datetime) -> tuple[SafetyDecision | None, datetime]:
    """Explicit data-clock policy. Returns (block, ledger reference time).

    Both clocks require the bridge to be delivering quotes right now (server
    receipt time) and the stream not to be running backwards. They differ in
    what "current" means for exchange/event timestamps:

    * WALL: the newest exchange timestamp must be within max_data_age of the
      real wall clock, and a stamped signal must be within max_signal_lag of
      it. A restart backlog or historical data is refused.
    * REPLAY: the replay stream's own newest event time is the reference.
      The signal's event time must sit within max_signal_lag behind it, so a
      backlog being re-processed relative to the stream is still refused,
      while September replay data in October is not.
    """
    symbol = intent.root_symbol
    data_at = latest_market_data_at(connection, symbol)
    data_age = _age_seconds(data_at, now)
    if data_age is None or data_age > config.max_data_age_seconds:
        return SafetyDecision(
            False,
            "MARKET_DATA_STALE",
            f"No {symbol} quote received in the last {config.max_data_age_seconds}s (bridge stalled or disconnected); new entries are blocked.",
            {"age_seconds": data_age, "data_clock": config.data_clock},
        ), now

    head = latest_market_event_at(connection, symbol)
    if head is None:
        return SafetyDecision(False, "MARKET_DATA_NOT_CURRENT", f"Newest {symbol} quote has no usable event timestamp.", {"data_clock": config.data_clock}), now

    recent_max = recent_market_event_max(connection, symbol)
    # Event-time tolerance: in accelerated Market Replay, several charts
    # mapping to one root can interleave by many event-seconds per flush, so
    # REPLAY uses the (event-time) signal-lag tolerance instead of 10s.
    reorder_tolerance = config.max_signal_lag_seconds if config.replay_clock else config.max_data_age_seconds
    if recent_max is not None and (recent_max - head).total_seconds() > reorder_tolerance:
        return SafetyDecision(
            False,
            "MARKET_DATA_REORDERED",
            f"Newest {symbol} event time is behind recently received quotes (out-of-order feed or replay rewind).",
            {"head": iso(head), "recent_max": iso(recent_max), "data_clock": config.data_clock},
        ), now

    signal_at = _signal_event_at(intent)
    if config.replay_clock:
        if signal_at is None:
            return SafetyDecision(False, "REPLAY_SIGNAL_TIME_REQUIRED", "REPLAY clock requires the setup's registration event time.", {"data_clock": "REPLAY"}), head
        lag = (head - signal_at).total_seconds()
        if lag > config.max_signal_lag_seconds or lag < -config.max_data_age_seconds:
            return SafetyDecision(
                False,
                "REPLAY_SIGNAL_BACKLOG",
                f"Signal event time is {lag:.0f}s from the replay stream head; allowed lag is {config.max_signal_lag_seconds}s.",
                {"head": iso(head), "signal_event_at": iso(signal_at), "data_clock": "REPLAY"},
            ), head
        return None, head

    if abs((now - head).total_seconds()) > config.max_data_age_seconds:
        return SafetyDecision(
            False,
            "MARKET_DATA_NOT_CURRENT",
            (
                f"Newest {symbol} exchange timestamp is not within {config.max_data_age_seconds}s of the wall clock "
                "(restart backlog, historical replay without OTR_EXECUTION_DATA_CLOCK=REPLAY, or bridge clock fault)."
            ),
            {"event_at": iso(head), "data_clock": "WALL"},
        ), now
    if signal_at is not None and (now - signal_at).total_seconds() > config.max_signal_lag_seconds:
        return SafetyDecision(
            False,
            "SIGNAL_BACKLOG",
            f"Signal event time is more than {config.max_signal_lag_seconds}s old relative to the wall clock.",
            {"signal_event_at": iso(signal_at), "data_clock": "WALL"},
        ), now
    return None, now


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
    replay_block = _replay_clock_block(config)
    if replay_block is not None:
        return replay_block

    reconciliation, reconciliation_at = get_state(connection, "reconciliation", None)
    age = _age_seconds(reconciliation_at, now)
    if not reconciliation or not reconciliation.get("ok"):
        return SafetyDecision(False, "RECONCILIATION_REQUIRED", "Broker snapshot is not reconciled.")
    if age is None or age > config.reconciliation_ttl_seconds:
        return SafetyDecision(False, "RECONCILIATION_STALE", "Broker reconciliation snapshot is stale.")
    if str(reconciliation.get("account") or "") != config.account:
        return SafetyDecision(False, "ACCOUNT_MISMATCH", "Broker snapshot account differs from configured account.")
    return SafetyDecision(True, "READY", "Execution bridge is armed and reconciled.")
