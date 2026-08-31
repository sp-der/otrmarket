from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from src.risk import operating_mode
from src.risk.eval_history72 import excluded_setup_ids


SCALP_RISK_CAP_DOLLARS = 125.0
SCALP_MAX_PER_SESSION = 3
SCALP_MAX_PER_DAY = 6
SCALP_SYMBOL_LOSS_LIMIT = 2


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def scalp_risk_cap() -> float:
    return max(25.0, _env_float("OTR_SCALP_RISK_CAP", SCALP_RISK_CAP_DOLLARS))


def _strategy_from_payload(payload_json: str | None) -> str:
    if not payload_json:
        return ""
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    return str((payload.get("metadata") or {}).get("strategy", "")).upper()


def evaluate_scalp_operating_mode72(connection, setup):
    """Separate small-risk scalp quota from the two primary eval trade slots."""
    config = operating_mode.OperatingModeConfig.from_env()
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    candidate_session = operating_mode._session_bucket(created_at)
    local_day = created_at.astimezone(operating_mode.NY).date()

    max_session = max(1, _env_int("OTR_SCALP_MAX_SESSION", SCALP_MAX_PER_SESSION))
    max_day = max(max_session, _env_int("OTR_SCALP_MAX_DAY", SCALP_MAX_PER_DAY))
    loss_limit = max(1, _env_int("OTR_SCALP_SYMBOL_LOSS_LIMIT", SCALP_SYMBOL_LOSS_LIMIT))

    details = {
        "profile": "MOMENTUM_SCALP_OPERATING_MODE_7_2",
        "mode": config.mode,
        "quality_grade": "A",
        "candidate_session": candidate_session,
        "max_scalps_per_session": max_session,
        "max_scalps_per_day": max_day,
        "symbol_loss_limit_per_session": loss_limit,
        "risk_cap_dollars": scalp_risk_cap(),
        "forced_trade": False,
        "primary_eval_slots_consumed": False,
    }

    if config.mode != "EVAL":
        return False, "Momentum scalp lane remains EVAL/PAPER-only until funded execution is separately certified.", details
    if candidate_session is None:
        return False, "Momentum scalp lane only opens inside a named futures session bucket.", details

    # Preserve the normal eval day-profit lock across both primary and scalp lanes.
    all_stats = operating_mode._day_and_session_stats(connection, created_at)
    details["realized_today"] = all_stats["realized_today"]
    if all_stats["realized_today"] >= config.eval_day_profit_objective:
        return False, "Evaluation day objective reached; scalp lane is locked to protect the modeled pass.", details

    excluded = excluded_setup_ids(connection)
    rows = connection.execute(
        """
        SELECT p.setup_id, p.symbol, p.status, p.result, p.opened_at, p.closed_at,
               s.payload_json
        FROM paper_trades p
        LEFT JOIN strategy_setups s ON s.setup_id = p.setup_id
        ORDER BY COALESCE(p.opened_at, p.closed_at) ASC
        """
    ).fetchall()

    day_count = 0
    session_count = 0
    symbol_session_losses = 0
    for setup_id, symbol, status, result, opened_value, closed_value, payload_json in rows:
        if str(setup_id) in excluded or _strategy_from_payload(payload_json) != "MOMENTUM_SCALP":
            continue
        opened = operating_mode._parse_dt(opened_value)
        if opened and opened.astimezone(operating_mode.NY).date() == local_day:
            day_count += 1
            opened_session = operating_mode._session_bucket(opened)
            if opened_session == candidate_session:
                session_count += 1
                if symbol == setup.symbol and str(result or "").upper() == "LOSS":
                    symbol_session_losses += 1

    details.update(
        scalp_trades_today=day_count,
        scalp_trades_session=session_count,
        symbol_scalp_losses_session=symbol_session_losses,
    )

    if symbol_session_losses >= loss_limit:
        return False, (
            f"{setup.symbol} momentum scalp lane shut off after {symbol_session_losses} scalp losses "
            f"in the {candidate_session['name']} session."
        ), details
    if session_count >= max_session:
        return False, (
            f"Momentum scalp session limit reached ({session_count}/{max_session}) "
            f"for {candidate_session['name']}."
        ), details
    if day_count >= max_day:
        return False, f"Momentum scalp daily limit reached ({day_count}/{max_day}).", details

    setup.metadata["profit_objective_dollars"] = round(scalp_risk_cap() * 1.5, 2)
    return True, (
        f"Momentum scalp slot available: {session_count}/{max_session} this {candidate_session['name']} session, "
        f"{day_count}/{max_day} today; <=${scalp_risk_cap():.0f} risk and two-loss symbol kill switch remain active."
    ), details
