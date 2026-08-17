from __future__ import annotations

import asyncio
import os
from datetime import time, timezone

from src import main_multi as base
from src.risk import session_consistency as session_rules
from src.risk.session_consistency import SessionConsistencyConfig, SessionConsistencyDecision
from src.storage.learning import observe_market_opportunity
from src.strategies.adaptive_manager import AdaptiveStrategyEngine
from src.strategies.execution_quality import evaluate_ict_context
from src.strategies.reversal import evaluate_reversal_context

runtime = base.runtime
runtime.strategy = AdaptiveStrategyEngine()

BTC_SYMBOLS = {"BTC", "BTC-USD"}
FUTURES_DAILY_CLOSE = time(17, 0)
FUTURES_DAILY_REOPEN = time(18, 0)


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return time(hour, minute)
    except Exception:
        return fallback


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _futures_market_state(local) -> tuple[bool, str]:
    """Return whether the normal CME Globex session is available in ET.

    Supported OTR futures use the standard Sunday-Friday Globex rhythm: reopen
    Sunday at 18:00 ET, daily maintenance from 17:00-18:00 ET Monday-Thursday,
    and weekly close Friday at 17:00 ET through Sunday reopen.
    """
    weekday = local.weekday()
    clock = local.time().replace(tzinfo=None)

    if weekday == 5:
        return False, "WEEKLY_CLOSED"
    if weekday == 6:
        return (clock >= FUTURES_DAILY_REOPEN, "SUNDAY_GLOBEX" if clock >= FUTURES_DAILY_REOPEN else "WEEKLY_CLOSED")
    if weekday == 4 and clock >= FUTURES_DAILY_CLOSE:
        return False, "WEEKLY_CLOSED"
    if FUTURES_DAILY_CLOSE <= clock < FUTURES_DAILY_REOPEN:
        return False, "DAILY_MAINTENANCE"
    return True, "GLOBEX_OPEN"


def _session_tier_58(symbol: str, local, config: SessionConsistencyConfig) -> tuple[str, float]:
    clock = local.time().replace(tzinfo=None)
    core_start = _parse_hhmm(config.session_start, time(9, 30))
    core_end = _parse_hhmm(config.session_end, time(13, 0))

    if symbol in BTC_SYMBOLS:
        if core_start <= clock < core_end:
            return "BTC_CORE", _env_float("OTR_CORE_RISK_MULTIPLIER", 1.00)
        return "BTC_OFFHOURS", _env_float("OTR_BTC_OFFHOURS_RISK_MULTIPLIER", 0.50)

    if local.weekday() == 6 and clock >= FUTURES_DAILY_REOPEN:
        return "SUNDAY_GLOBEX", _env_float("OTR_SUNDAY_GLOBEX_RISK_MULTIPLIER", 0.50)
    if clock >= FUTURES_DAILY_REOPEN or clock < time(2, 0):
        return "ASIA", _env_float("OTR_ASIA_RISK_MULTIPLIER", 0.50)
    if time(2, 0) <= clock < time(8, 30):
        return "LONDON", _env_float("OTR_LONDON_RISK_MULTIPLIER", 0.65)
    if time(8, 30) <= clock < core_start:
        return "PREMARKET", _env_float("OTR_PREMARKET_RISK_MULTIPLIER", 0.75)
    if core_start <= clock < core_end:
        return "NY_CORE", _env_float("OTR_CORE_RISK_MULTIPLIER", 1.00)
    if core_end <= clock < time(16, 0):
        return "NY_AFTERNOON", _env_float("OTR_AFTERNOON_RISK_MULTIPLIER", 0.75)
    return "LATE_GLOBEX", _env_float("OTR_LATE_RISK_MULTIPLIER", 0.50)


def evaluate_session_consistency_58(connection, setup, config: SessionConsistencyConfig | None = None):
    """Allow quality setups across all live sessions, with session-aware risk.

    Operation 5.8 no longer treats a cash-session clock, a small calibration
    trade count, or a modest realized win as a reason to stop scanning for live
    opportunities. Actual market closure/maintenance, timeframe eligibility,
    post-loss quality, strategy quality, exposure/cooldowns, and the evaluation
    guard remain hard protections.
    """
    config = config or SessionConsistencyConfig.from_env()
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    local = created_at.astimezone(config.timezone)
    symbol = str(getattr(setup, "symbol", "") or "").upper()

    details = {
        "profile": "ALL_SESSION_INTELLIGENCE_5_8",
        "timezone": config.timezone_name,
        "local_day": local.strftime("%A"),
        "local_time": local.strftime("%H:%M"),
        "symbol": symbol,
        "execution_timeframe": config.execution_timeframe,
        "candidate_timeframe": setup.timeframe,
        "multi_timeframe_execution": config.all_timeframes_enabled,
        "supported_execution_timeframes": sorted(session_rules.SUPPORTED_EXECUTION_TIMEFRAMES),
        "core_session_start": config.session_start,
        "core_session_end": config.session_end,
        "daily_maintenance": "17:00-18:00 America/New_York",
        "weekly_close": "Friday 17:00-Sunday 18:00 America/New_York",
        "calibration_trade_cap_is_hard_block": False,
        "base_win_lock_is_hard_block": False,
        "prop_guard_controls_final_risk": True,
    }

    if setup.timeframe not in session_rules.SUPPORTED_EXECUTION_TIMEFRAMES:
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

    if symbol in BTC_SYMBOLS:
        market_state = "24_7"
    elif symbol in session_rules.FUTURES_SYMBOLS:
        market_open, market_state = _futures_market_state(local)
        if not market_open:
            details["market_session_mode"] = market_state
            if market_state == "DAILY_MAINTENANCE":
                reason = "CME daily maintenance is active from 17:00-18:00 America/New_York; no new futures risk."
            elif local.weekday() == 6:
                reason = "Futures weekly session has not reopened yet; Sunday execution begins at 18:00 America/New_York."
            else:
                reason = "Futures weekly session is closed; new risk resumes Sunday at 18:00 America/New_York."
            return SessionConsistencyDecision(False, reason, details)
    else:
        details["market_session_mode"] = "UNSUPPORTED_MARKET"
        return SessionConsistencyDecision(False, f"{symbol or 'Unknown symbol'} is not enabled for autonomous all-session execution.", details)

    stats = session_rules._day_stats(connection, created_at, config.timezone)
    details["day_stats"] = stats

    # Keep the stronger second-chance standard after a realized loss, but do not
    # shut the bot down merely because it already traded or banked a modest win.
    if stats["losses"] >= 1:
        second_ok, second_reason, second_details = session_rules._second_chance_quality(setup, config)
        details["second_chance"] = second_details
        if not second_ok:
            return SessionConsistencyDecision(False, second_reason, details)

    tier, multiplier = _session_tier_58(symbol, local, config)
    try:
        current = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        current = 1.0
    setup.metadata["risk_multiplier"] = min(current, multiplier)
    setup.metadata["session_tier"] = tier
    details.update(
        market_session_mode=market_state,
        session_tier=tier,
        session_risk_multiplier=multiplier,
    )

    execution_scope = "multi-timeframe" if config.all_timeframes_enabled else config.execution_timeframe
    return SessionConsistencyDecision(
        True,
        (
            f"Operation 5.8 all-session {tier.replace('_', ' ').lower()} is open at {multiplier:.0%} max risk; "
            f"{execution_scope} candidate proceeds to quality and evaluation guards."
        ),
        details,
    )


def _apply_b_plus_tier(connection, setup, rr):
    if rr < 2.5:
        return False, f"B+ setup offers only {rr:.2f}R; require 2.50R for reduced-risk execution."
    ok, reason = base._b_plus_execution_gate(connection, setup)
    if not ok:
        return False, reason
    try:
        current = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        current = 1.0
    setup.metadata["risk_multiplier"] = min(current, 0.40)
    setup.metadata["execution_tier"] = "B_PLUS_REDUCED"
    setup.metadata["tier_reason"] = reason
    return True, reason


def _adaptive_quality_gate(connection, setup, histories=None):
    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    rr = float(setup.risk_reward or 0.0)

    if strategy == "REJECTION_BLOCK_10_10":
        score = int(setup.metadata.get("checklist_score", 0) or 0)
        total = int(setup.metadata.get("checklist_total", 10) or 10)
        if score < total or total < 10:
            return False, f"Rejection-block checklist is only {score}/{total}; require 10/10."
        if rr < 3.0:
            return False, f"Rejection-block setup offers only {rr:.2f}R; require 3.00R."

    elif strategy == "MSS_REVERSAL":
        if rr < 1.5:
            return False, f"Reversal offers only {rr:.2f}R; require 1.50R."
        if histories is not None:
            ok, reason, details = evaluate_reversal_context(setup, histories)
            setup.metadata["a_plus_context"] = details
            if not ok:
                return False, reason
            if details.get("quality_grade") == "B+":
                ok, tier_reason = _apply_b_plus_tier(connection, setup, rr)
                if not ok:
                    return False, tier_reason
            else:
                conflict = bool(details.get("narrative_conflict"))
                cap = 0.60 if conflict else 0.75
                try:
                    current = float(setup.metadata.get("risk_multiplier", 1.0))
                except (TypeError, ValueError):
                    current = 1.0
                setup.metadata["risk_multiplier"] = min(current, cap)
                setup.metadata["execution_tier"] = "REVERSAL_COUNTERTREND_REDUCED" if conflict else "REVERSAL_REDUCED"

    else:
        if rr < 1.0:
            return False, f"ICT setup offers only {rr:.2f}R; structural minimum is 1.00R."

        # SMT is no longer the sole 1m NQ/ES confirmation path. A sweep-based
        # alternative must be materially stronger and starts at reduced risk.
        if setup.timeframe == "1m" and setup.symbol in {"NQ", "ES"} and str(setup.trigger_type or "").lower() != "smt":
            displacement = getattr(setup, "displacement", None)
            body = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
            candle_range = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
            if str(setup.trigger_type or "").lower() != "liquidity_sweep":
                return False, "1m NQ/ES needs SMT or a confirmed liquidity sweep."
            if body < 1.90 or candle_range < 1.50 or rr < 1.50:
                return False, (
                    "1m non-SMT setup needs sweep + >=1.90x body + >=1.50x range "
                    f"displacement + >=1.50R; got {body:.2f}x/{candle_range:.2f}x/{rr:.2f}R."
                )
            try:
                current = float(setup.metadata.get("risk_multiplier", 1.0))
            except (TypeError, ValueError):
                current = 1.0
            setup.metadata["risk_multiplier"] = min(current, 0.70)
            setup.metadata["execution_tier"] = "ONE_MINUTE_SWEEP_REDUCED"

        if histories is not None:
            ok, reason, details = evaluate_ict_context(setup, histories)
            setup.metadata["a_plus_context"] = details
            if not ok:
                return False, reason
            if details.get("quality_grade") == "B+":
                ok, tier_reason = _apply_b_plus_tier(connection, setup, rr)
                if not ok:
                    return False, tier_reason

    for gate in (base._active_risk_gate,):
        ok, reason = gate(setup)
        if not ok:
            return False, reason
    ok, reason = base._global_loss_cooldown(connection, setup)
    if not ok:
        return False, reason
    ok, reason = base._same_symbol_cooldown(connection, setup)
    if not ok:
        return False, reason

    grade = setup.metadata.get("a_plus_context", {}).get("quality_grade", "A/A+")
    tier = setup.metadata.get("execution_tier", setup.metadata.get("session_tier", "CORE"))
    return True, f"{grade} quality passed Operation 5.8 adaptive gate ({tier})."


def _observe(connection, symbol, timeframe, histories):
    try:
        lesson = observe_market_opportunity(connection, symbol, timeframe, histories)
    except Exception as exc:
        runtime.console.log(f"LEARNING 5.8 observer error {symbol} {timeframe}: {exc}")
        return
    if lesson:
        runtime.console.log(f"LEARNING 5.8 captured: {lesson['summary']}")


def evaluate_strategy_58(connection, symbol: str, timeframe: str):
    histories = runtime.histories_snapshot()
    if not runtime.session.strategy_enabled(symbol):
        _observe(connection, symbol, timeframe, histories)
        return None

    setups = runtime.strategy.on_candle_all(symbol, timeframe, histories)
    runtime.save_diagnostic(connection, runtime.strategy.diagnostic(symbol, timeframe))
    handled = []

    for setup in setups:
        strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
        if strategy != "MSS_REVERSAL":
            base._register_48_shadow(connection, setup)

        session_decision = evaluate_session_consistency_58(connection, setup)
        setup.metadata["session_consistency"] = session_decision.details
        if not session_decision.allowed:
            setup.metadata["execution_quality_gate"] = {
                "allowed": False, "reason": session_decision.reason,
                "profile": "ADAPTIVE_INTELLIGENCE_5_8",
                "baseline_shadow_profile": base.SHADOW_PROFILE,
            }
            setup.status = "SESSION_BLOCKED"
            runtime.save_setup(connection, setup)
            runtime.console.log(f"SESSION 5.8 blocked {setup.symbol} {setup.timeframe} [{strategy}]: {session_decision.reason}")
            handled.append(setup)
            continue

        quality_allowed, quality_reason = _adaptive_quality_gate(connection, setup, histories)
        setup.metadata["execution_quality_gate"] = {
            "allowed": quality_allowed, "reason": quality_reason,
            "profile": "ADAPTIVE_INTELLIGENCE_5_8",
            "baseline_shadow_profile": base.SHADOW_PROFILE,
        }
        if not quality_allowed:
            setup.status = "QUALITY_BLOCKED"
            runtime.save_setup(connection, setup)
            runtime.console.log(f"QUALITY 5.8 blocked {setup.symbol} {setup.timeframe} [{strategy}]: {quality_reason}")
            handled.append(setup)
            continue

        decision = runtime.evaluation_guard.decide(connection, setup.created_at)
        applied_risk, risk_multiplier = base._setup_risk(decision, setup)
        setup.metadata["evaluation_guard"] = {
            "status": decision.status, "allowed": decision.allowed,
            "risk_cap_dollars": decision.risk_dollars,
            "risk_multiplier": risk_multiplier,
            "risk_dollars": applied_risk if decision.allowed else 0.0,
            "reason": decision.reason,
            "profile": decision.snapshot.get("profile"),
            "phase": decision.snapshot.get("phase"),
        }
        if not decision.allowed:
            setup.status = "GUARD_BLOCKED"
            runtime.save_setup(connection, setup)
            runtime.console.log(f"PROP GUARD blocked {setup.symbol} {setup.timeframe} [{strategy}]: {decision.status} - {decision.reason}")
            handled.append(setup)
            continue

        runtime.save_setup(connection, setup)
        try:
            position = runtime.paper.register_setup(
                setup,
                risk_dollars=applied_risk,
                guard_reason=(
                    f"{decision.reason} Operation 5.8 adaptive gate passed. "
                    f"Applied {risk_multiplier:.0%} of ${decision.risk_dollars:.2f} cap."
                ),
            )
        except ValueError as exc:
            setup.status = "RISK_REJECTED"
            setup.metadata["geometry_rejection"] = str(exc)
            runtime.save_setup(connection, setup)
            runtime.console.log(f"RISK GEOMETRY rejected {setup.symbol} {setup.timeframe} [{strategy}]: {exc}")
            handled.append(setup)
            continue

        runtime.upsert_paper_trade(connection, position, setup.created_at.isoformat())
        context = setup.metadata.get("a_plus_context", {})
        htf = context.get("context_timeframe")
        bias = context.get("higher_timeframe_bias")
        context_text = f" HTF {htf}:{bias}" if htf and bias else ""
        session = setup.metadata.get("session_consistency", {})
        session_text = f" {session.get('session_tier')} {session.get('local_time')} {session.get('timezone')}" if session else ""
        runtime.console.log(
            f"SETUP 5.8 REGISTERED {setup.symbol} {setup.timeframe} [{strategy}] "
            f"{setup.direction.upper()} {setup.risk_reward:.2f}R risk ${applied_risk:.2f} "
            f"({risk_multiplier:.0%} tier){context_text}{session_text}"
        )
        handled.append(setup)

    _observe(connection, symbol, timeframe, histories)
    return handled[-1] if handled else None


runtime.evaluate_strategy = evaluate_strategy_58

if __name__ == "__main__":
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
