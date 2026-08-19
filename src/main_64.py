from __future__ import annotations

import asyncio
from datetime import timezone

from src import main_63 as op63
from src.execution import paper as paper_module
from src.execution.paper import PaperPosition
from src.strategies.execution_quality import BAR_SECONDS


runtime = op63.runtime
continuation = op63.continuation
op61 = op63.op62.op61
op58 = op63.op62.op61.op59.op58


# ---------------------------------------------------------------------------
# Operation 6.4A: strong countertrend setups are no longer hard-vetoed solely
# because the primary higher timeframe has not flipped yet. They must still
# clear a stricter reversal-quality score and are always reduced-risk.
# ---------------------------------------------------------------------------
_previous_quality_gate_64 = op58._adaptive_quality_gate


def _fresh_entry_leg(setup) -> tuple[bool, float]:
    displacement = getattr(setup, "displacement", None)
    entry_fvg = getattr(setup, "entry_fvg", None)
    if displacement is None or entry_fvg is None:
        return False, 999.0
    if getattr(displacement, "direction", None) != setup.direction:
        return False, 999.0
    if getattr(entry_fvg, "direction", None) != setup.direction:
        return False, 999.0
    if entry_fvg.formed_at <= displacement.candle_time:
        return False, 999.0

    bar_seconds = BAR_SECONDS.get(setup.timeframe, 60)
    age_bars = max(
        0.0,
        (setup.created_at - entry_fvg.formed_at).total_seconds() / max(1, bar_seconds),
    )
    return age_bars <= 2.0, age_bars


def _countertrend_score(setup, histories) -> tuple[int, dict]:
    rr = float(setup.risk_reward or 0.0)
    displacement = getattr(setup, "displacement", None)
    body = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
    candle_range = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
    trigger = str(setup.trigger_type or "").lower()
    entry_type = str(setup.metadata.get("entry_type", "FVG_MIDPOINT"))
    fresh, age_bars = _fresh_entry_leg(setup)
    narrative = op61._narrative(setup, histories)

    components = {
        "trigger": 20 if trigger == "smt" else 18 if trigger == "liquidity_sweep" else 0,
        "displacement": 25 if body >= 1.90 and candle_range >= 1.50 else 20 if body >= 1.75 and candle_range >= 1.40 else 0,
        "fresh_entry": 20 if fresh else 0,
        "target_room": 20 if rr >= 2.0 else 15 if rr >= 1.75 else 0,
        "narrative": 10 if narrative.get("strong_support") else 5 if narrative.get("supports_setup") else 0,
        "entry_location": 5 if entry_type != "ORDER_BLOCK" else 3,
    }
    score = int(sum(components.values()))
    details = {
        "operation": 6.4,
        "score": score,
        "components": components,
        "trigger": trigger,
        "rr": rr,
        "displacement_body_ratio": body,
        "displacement_range_ratio": candle_range,
        "entry_fvg_age_bars": round(age_bars, 3),
        "entry_type": entry_type,
        "multi_timeframe_narrative": narrative,
    }
    return score, details


def _is_primary_context_conflict(reason: str) -> bool:
    lower = str(reason or "").lower()
    return "context is" in lower and "while the setup is" in lower


def _adaptive_quality_gate_64(connection, setup, histories=None):
    allowed, reason = _previous_quality_gate_64(connection, setup, histories)
    if allowed:
        return True, reason

    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    if (
        strategy != "ICT_CONFLUENCE"
        or histories is None
        or setup.timeframe not in {"1m", "5m"}
        or not _is_primary_context_conflict(reason)
    ):
        return False, reason

    score, details = _countertrend_score(setup, histories)
    setup.metadata["countertrend_quality_64"] = details

    trigger = details["trigger"]
    rr = details["rr"]
    body = details["displacement_body_ratio"]
    candle_range = details["displacement_range_ratio"]
    age_bars = details["entry_fvg_age_bars"]

    if trigger not in {"smt", "liquidity_sweep"}:
        return False, reason
    if rr < 1.75:
        return False, reason
    if body < 1.75 or candle_range < 1.40:
        return False, reason
    if age_bars > 2.0:
        return False, reason
    if score < 80:
        return False, (
            f"Primary HTF conflict remains blocked: countertrend quality scored "
            f"{score}/100; require 80/100 for reduced-risk reversal execution."
        )

    narrative = details["multi_timeframe_narrative"]
    if narrative.get("strong_support"):
        cap = 0.50
    elif narrative.get("supports_setup"):
        cap = 0.45
    else:
        cap = 0.35

    rescue = (
        f"Primary HTF disagrees, but Operation 6.4 countertrend quality scored "
        f"{score}/100 with {trigger.replace('_', ' ')}, {body:.2f}x body / "
        f"{candle_range:.2f}x range displacement, fresh FVG ({age_bars:.2f} bars), "
        f"and {rr:.2f}R. Reduced-risk reversal tier capped at {cap:.0%}."
    )
    op61._cap_risk(setup, cap, "COUNTERTREND_REVERSAL_64", rescue)
    setup.metadata.setdefault("a_plus_context", {})["quality_grade"] = "A_COUNTERTREND"
    setup.metadata["a_plus_context"]["countertrend_override"] = True

    guard_ok, guard_reason = op61._risk_guards(connection, setup)
    if not guard_ok:
        return False, guard_reason

    post_ok, post_reason = op61._post_loss_risk(connection, setup)
    if not post_ok:
        return False, post_reason

    return True, f"{rescue} {post_reason}"


op58._adaptive_quality_gate = _adaptive_quality_gate_64


# ---------------------------------------------------------------------------
# Operation 6.4B: check the actual live price before a newly accepted setup is
# allowed into the pending-order book. If most of the objective has already
# traded, classify it as MISSED_EXTENDED and arm continuation immediately.
# This preserves the 75% no-chase rule while removing the one-tick
# REGISTERED -> INVALIDATED whiplash seen on fast GC moves.
# ---------------------------------------------------------------------------
_original_register_setup_64 = runtime.paper.register_setup


def _event_time_for(symbol: str, fallback):
    value = runtime.clock.event_time(symbol)
    if value is None:
        return paper_module._aware_utc(fallback)
    return paper_module._aware_utc(value)


def _register_setup_64(setup, *, risk_dollars=None, guard_reason=None):
    state = runtime.market_state.get(setup.symbol, {})
    live_price = state.get("price")
    if live_price is None:
        return _original_register_setup_64(
            setup,
            risk_dollars=risk_dollars,
            guard_reason=guard_reason,
        )

    live_price = float(live_price)
    progress = float(paper_module._preentry_target_progress(setup, live_price))
    stop_broken = (
        live_price <= float(setup.stop_price)
        if setup.direction == "bullish"
        else live_price >= float(setup.stop_price)
    )

    if not stop_broken and progress < float(paper_module._MAX_PREENTRY_TARGET_PROGRESS):
        return _original_register_setup_64(
            setup,
            risk_dollars=risk_dollars,
            guard_reason=guard_reason,
        )

    event_at = _event_time_for(setup.symbol, setup.created_at)
    if stop_broken:
        setup.status = "INVALIDATED"
        result = "INVALIDATED_BEFORE_ENTRY"
        continuation_armed = False
        reason = "Protective stop was already broken before the pending order could be registered."
    else:
        setup.status = "MISSED_EXTENDED"
        result = "MISSED_EXTENDED"
        continuation_armed = bool(
            continuation.arm_from_stale(setup, event_at.isoformat())
        )
        reason = (
            f"Move already traveled {progress * 100.0:.2f}% of the planned objective "
            f"before registration; original entry suppressed instead of chased."
        )

    setup.metadata["pre_registration_viability_64"] = {
        "operation": 6.4,
        "allowed": False,
        "result": result,
        "event_price": live_price,
        "target_progress": round(progress, 6),
        "target_progress_pct": round(progress * 100.0, 2),
        "no_chase_threshold": float(paper_module._MAX_PREENTRY_TARGET_PROGRESS),
        "continuation_armed": continuation_armed,
        "reason": reason,
    }
    setup.metadata["execution_quality_gate"] = {
        **setup.metadata.get("execution_quality_gate", {}),
        "allowed": False,
        "reason": (
            reason
            + (" Continuation engine armed for a fresh pullback/resumption." if continuation_armed else "")
        ),
        "profile": "PRE_ENTRY_VIABILITY_6_4",
    }

    return PaperPosition(
        setup=setup,
        status="INVALIDATED",
        closed_at=event_at,
        exit_price=live_price,
        result=result,
        risk_dollars=risk_dollars,
        result_dollars=0.0 if risk_dollars is not None else None,
        guard_reason=guard_reason,
    )


runtime.paper.register_setup = _register_setup_64


# Persist the clearer setup status after the normal paper/intelligence ledger
# write and leave the old INVALIDATED accounting semantics intact for metrics.
_original_upsert_paper_trade_64 = runtime.upsert_paper_trade


def _upsert_paper_trade_64(connection, position, updated_at):
    _original_upsert_paper_trade_64(connection, position, updated_at)
    viability = position.setup.metadata.get("pre_registration_viability_64")
    if not viability:
        return

    runtime.save_setup(connection, position.setup)
    runtime.console.log(
        f"ENTRY 6.4 {position.setup.symbol} {position.setup.timeframe} "
        f"{position.setup.direction} {viability['result']} "
        f"target_progress={viability['target_progress_pct']:.2f}% "
        f"continuation_armed={viability['continuation_armed']}"
    )


runtime.upsert_paper_trade = _upsert_paper_trade_64


if __name__ == "__main__":
    runtime.console.log(
        "Operation 6.4 active: smart countertrend reversal tier, live pre-entry "
        "viability, MISSED_EXTENDED classification, immediate continuation re-arm, "
        "and the 75% no-chase rule preserved."
    )
    op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
