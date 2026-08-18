from __future__ import annotations

import asyncio
import json
from datetime import time, timezone

from src import main_58 as op58
from src.risk.session_consistency import SessionConsistencyConfig, SessionConsistencyDecision

runtime = op58.runtime

ACTIVE_FUTURES = {"NQ", "ES", "GC"}


def _parse_db_time(value):
    return op58.base._parse_time(value)


def _futures_day_stats(connection, reference_time, tz) -> dict:
    """Return same-day stats for the active futures book only.

    BTC history is intentionally preserved in SQLite, but while crypto is
    disabled it must not trigger futures second-chance, cooldown, or B+ rules.
    """
    current_day = reference_time.astimezone(tz).date()
    rows = connection.execute(
        """
        SELECT symbol, status, result, opened_at, closed_at, result_dollars
        FROM paper_trades
        WHERE symbol IN ('NQ', 'ES', 'GC')
        ORDER BY COALESCE(closed_at, opened_at) ASC
        """
    ).fetchall()

    trades = losses = wins = 0
    realized = 0.0
    for row in rows:
        symbol, status, result, opened_value, closed_value, result_dollars = row
        opened_at = _parse_db_time(opened_value)
        if opened_at and opened_at.astimezone(tz).date() == current_day:
            trades += 1

        closed_at = _parse_db_time(closed_value)
        if not closed_at or closed_at.astimezone(tz).date() != current_day:
            continue
        if str(status or "").upper() != "CLOSED":
            continue
        marker = str(result or "").upper()
        losses += int(marker == "LOSS")
        wins += int(marker == "WIN")
        realized += float(result_dollars or 0.0)

    return {
        "trades": trades,
        "losses": losses,
        "wins": wins,
        "realized_pnl": realized,
        "symbols": sorted(ACTIVE_FUTURES),
    }


def evaluate_session_consistency_59(connection, setup, config: SessionConsistencyConfig | None = None):
    """Keep SESSION_BLOCKED strictly about market/session eligibility.

    Post-loss standards are execution quality, not a market-hours decision.
    They are enforced later by ``_adaptive_quality_gate_59`` so the dashboard
    tells the trader the real reason a completed candidate did not execute.
    """
    config = config or SessionConsistencyConfig.from_env()
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    local = created_at.astimezone(config.timezone)
    symbol = str(getattr(setup, "symbol", "") or "").upper()

    details = {
        "profile": "ALL_SESSION_INTELLIGENCE_5_9",
        "timezone": config.timezone_name,
        "local_day": local.strftime("%A"),
        "local_time": local.strftime("%H:%M"),
        "symbol": symbol,
        "execution_timeframe": config.execution_timeframe,
        "candidate_timeframe": setup.timeframe,
        "multi_timeframe_execution": config.all_timeframes_enabled,
        "supported_execution_timeframes": sorted(op58.session_rules.SUPPORTED_EXECUTION_TIMEFRAMES),
        "core_session_start": config.session_start,
        "core_session_end": config.session_end,
        "daily_maintenance": "17:00-18:00 America/New_York",
        "weekly_close": "Friday 17:00-Sunday 18:00 America/New_York",
        "post_loss_quality_is_session_block": False,
        "prop_guard_controls_final_risk": True,
    }

    if symbol in op58.BTC_SYMBOLS:
        details["market_session_mode"] = "DISABLED"
        return SessionConsistencyDecision(False, "BTC execution is disabled for the current futures test.", details)

    if setup.timeframe not in op58.session_rules.SUPPORTED_EXECUTION_TIMEFRAMES:
        return SessionConsistencyDecision(
            False,
            f"{setup.timeframe} is not a supported autonomous execution timeframe.",
            details,
        )

    if not config.all_timeframes_enabled and setup.timeframe != config.execution_timeframe:
        return SessionConsistencyDecision(
            False,
            f"Execution profile is set to {config.execution_timeframe}; {setup.timeframe} remains scanner/shadow only.",
            details,
        )

    if symbol not in op58.session_rules.FUTURES_SYMBOLS:
        details["market_session_mode"] = "UNSUPPORTED_MARKET"
        return SessionConsistencyDecision(False, f"{symbol or 'Unknown symbol'} is not enabled for autonomous futures execution.", details)

    market_open, market_state = op58._futures_market_state(local)
    details["market_session_mode"] = market_state
    if not market_open:
        if market_state == "DAILY_MAINTENANCE":
            reason = "CME daily maintenance is active from 17:00-18:00 America/New_York; no new futures risk."
        elif local.weekday() == 6:
            reason = "Futures weekly session has not reopened yet; Sunday execution begins at 18:00 America/New_York."
        else:
            reason = "Futures weekly session is closed; new risk resumes Sunday at 18:00 America/New_York."
        return SessionConsistencyDecision(False, reason, details)

    tier, multiplier = op58._session_tier_58(symbol, local, config)
    try:
        current = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        current = 1.0
    setup.metadata["risk_multiplier"] = min(current, multiplier)
    setup.metadata["session_tier"] = tier
    details.update(
        session_tier=tier,
        session_risk_multiplier=multiplier,
        futures_day_stats=_futures_day_stats(connection, created_at, config.timezone),
    )

    execution_scope = "multi-timeframe" if config.all_timeframes_enabled else config.execution_timeframe
    return SessionConsistencyDecision(
        True,
        (
            f"Operation 5.9 {tier.replace('_', ' ').lower()} futures session is open at {multiplier:.0%} max risk; "
            f"{execution_scope} candidate proceeds to quality and evaluation guards."
        ),
        details,
    )


def _futures_global_loss_cooldown(connection, setup) -> tuple[bool, str]:
    row = connection.execute(
        """
        SELECT symbol, closed_at
        FROM paper_trades
        WHERE symbol IN ('NQ', 'ES', 'GC')
          AND status = 'CLOSED' AND result = 'LOSS' AND closed_at IS NOT NULL
        ORDER BY closed_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return True, "No futures-wide loss reset applies."

    loss_symbol, closed_value = row
    closed_at = _parse_db_time(closed_value)
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    if closed_at is None or created_at <= closed_at:
        return True, "No futures-wide loss reset applies."

    elapsed = (created_at - closed_at).total_seconds() / 60.0
    if elapsed < 30:
        return False, (
            f"Futures post-loss reset active after {loss_symbol}: "
            f"{30 - elapsed:.0f} market minutes remain before new futures risk."
        )
    return True, "Futures-wide post-loss reset cleared."


def _futures_b_plus_execution_gate(connection, setup) -> tuple[bool, str]:
    candidate_day = op58.base._trading_day(setup.created_at)
    rows = connection.execute(
        """
        SELECT p.symbol, p.result, p.closed_at, s.created_at, s.payload_json
        FROM paper_trades p
        LEFT JOIN strategy_setups s ON s.setup_id = p.setup_id
        WHERE p.symbol IN ('NQ', 'ES', 'GC')
        """
    ).fetchall()

    b_plus_count = 0
    for symbol, result, closed_at, created_at, payload_json in rows:
        if str(result or "").upper() == "LOSS" and op58.base._trading_day(closed_at) == candidate_day:
            return False, "B+ futures tier disabled after a realized futures loss on this trading day."
        if op58.base._trading_day(created_at) != candidate_day or not payload_json:
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        grade = payload.get("metadata", {}).get("a_plus_context", {}).get("quality_grade")
        if grade == "B+":
            b_plus_count += 1

    if b_plus_count >= 2:
        return False, "Daily B+ futures limit reached (2/2 reduced-risk trades)."
    return True, f"B+ futures reduced-risk slot available ({b_plus_count}/2 used)."


# Patch the inherited quality helpers so BTC history cannot contaminate futures
# cooldowns or B+ eligibility while crypto execution is disabled.
op58.base._global_loss_cooldown = _futures_global_loss_cooldown
op58.base._b_plus_execution_gate = _futures_b_plus_execution_gate

_original_adaptive_quality_gate = op58._adaptive_quality_gate


def _adaptive_quality_gate_59(connection, setup, histories=None):
    allowed, reason = _original_adaptive_quality_gate(connection, setup, histories)
    if not allowed:
        return False, reason

    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    stats = _futures_day_stats(connection, created_at, SessionConsistencyConfig.from_env().timezone)
    setup.metadata["futures_day_stats"] = stats

    if stats["losses"] >= 1:
        config = SessionConsistencyConfig.from_env()
        second_ok, second_reason, second_details = op58.session_rules._second_chance_quality(setup, config)
        setup.metadata["post_loss_quality"] = {
            **second_details,
            "allowed": second_ok,
            "reason": second_reason,
            "source": "FUTURES_ONLY_5_9",
        }
        if not second_ok:
            return False, second_reason

    return True, reason


# The existing Operation 5.8 execution loop looks these names up dynamically,
# so replacing them upgrades classification without duplicating order logic.
op58.evaluate_session_consistency_58 = evaluate_session_consistency_59
op58._adaptive_quality_gate = _adaptive_quality_gate_59
runtime.evaluate_strategy = op58.evaluate_strategy_58

# The futures evaluation account should ignore preserved BTC paper history too.
def _futures_evaluation_rows(connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    if "risk_dollars" not in columns:
        return []
    connection.row_factory = __import__("sqlite3").Row
    return connection.execute(
        """
        SELECT setup_id, status, opened_at, closed_at, result,
               result_r, risk_dollars, result_dollars, updated_at
        FROM paper_trades
        WHERE symbol IN ('NQ', 'ES', 'GC')
          AND risk_dollars IS NOT NULL AND risk_dollars > 0
        ORDER BY COALESCE(closed_at, opened_at, updated_at) ASC
        """
    ).fetchall()

runtime.evaluation_guard._rows = _futures_evaluation_rows


if __name__ == "__main__":
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
