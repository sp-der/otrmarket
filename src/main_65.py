from __future__ import annotations

import asyncio
from copy import deepcopy

from src import main_64 as op64
from src.strategies.candles import TIMEFRAME_SECONDS
from src.strategies.models import Candle


runtime = op64.runtime
op58 = op64.op58
base = op58.base

# ---------------------------------------------------------------------------
# Operation 6.5A: conservative intrabar execution.
#
# The normal scanner remains candle-close driven and owns the durable strategy
# state. Once that stable state has already confirmed the setup narrative, 6.5
# takes a deep copy of the ICT engine and lets the copy inspect the currently
# forming 1m/5m candle. This can advance a candidate one stage early without
# allowing a half-formed candle to corrupt the durable state machine.
# ---------------------------------------------------------------------------
INTRABAR_TIMEFRAMES = {"1m", "5m"}
INTRABAR_STAGES = {
    "WAIT_DISPLACEMENT",
    "WAIT_ENTRY_FVG",
    "WAIT_QUALIFYING_FVG",
    "WAIT_VALID_RR",
}
INTRABAR_EVAL_INTERVAL_SECONDS = 0.25
INTRABAR_MIN_STABILITY_SECONDS = 0.75
INTRABAR_MIN_CONFIRMATIONS = 3
INTRABAR_MIN_BAR_AGE_SECONDS = 1.0

_last_intrabar_eval: dict[tuple[str, str], object] = {}
_intrabar_candidates: dict[tuple[str, str], dict] = {}
_promoted_bucket: dict[tuple[str, str], object] = {}


def _synthetic_candle(symbol: str, timeframe: str, event_time):
    bucket = runtime.candles.current.get((symbol, timeframe))
    if bucket is None:
        return None

    event_time = runtime.parse_timestamp(event_time.isoformat()) if hasattr(event_time, "isoformat") else event_time
    age_seconds = (event_time - bucket["open_time"]).total_seconds()
    if age_seconds < INTRABAR_MIN_BAR_AGE_SECONDS:
        return None

    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        open_time=bucket["open_time"],
        close_time=event_time,
        open=float(bucket["open"]),
        high=float(bucket["high"]),
        low=float(bucket["low"]),
        close=float(bucket["close"]),
        ticks=int(bucket.get("ticks", 0)),
    )


def _intrabar_histories(symbol: str, timeframe: str, event_time):
    candle = _synthetic_candle(symbol, timeframe, event_time)
    if candle is None:
        return None, None

    histories = runtime.histories_snapshot()
    key = (symbol, timeframe)
    histories[key] = list(histories.get(key, [])) + [candle]
    return histories, candle


def _candidate_fingerprint(setup, bucket_open) -> tuple:
    return (
        setup.symbol,
        setup.timeframe,
        setup.direction,
        bucket_open.isoformat(),
        str(setup.trigger_type or ""),
        round(float(setup.entry_price), 4),
        round(float(setup.stop_price), 4),
        round(float(setup.target_price), 4),
    )


def _stability_ready(key, fingerprint, event_time) -> tuple[bool, int, float]:
    state = _intrabar_candidates.get(key)
    if state is None or state.get("fingerprint") != fingerprint or event_time < state.get("first_seen"):
        state = {
            "fingerprint": fingerprint,
            "first_seen": event_time,
            "last_seen": event_time,
            "confirmations": 1,
        }
        _intrabar_candidates[key] = state
        return False, 1, 0.0

    state["last_seen"] = event_time
    state["confirmations"] = int(state.get("confirmations", 0)) + 1
    stable_seconds = max(0.0, (event_time - state["first_seen"]).total_seconds())
    ready = (
        state["confirmations"] >= INTRABAR_MIN_CONFIRMATIONS
        and stable_seconds >= INTRABAR_MIN_STABILITY_SECONDS
    )
    return ready, state["confirmations"], stable_seconds


def _handle_intrabar_setup_65(connection, setup, histories, confirmations: int, stable_seconds: float):
    strategy = "ICT_CONFLUENCE"
    setup.metadata.setdefault("strategy", strategy)
    setup.metadata.setdefault("checklist_total", 6)
    setup.metadata.setdefault("checklist_score", 6)
    setup.metadata["intrabar_execution_65"] = {
        "operation": 6.5,
        "mode": "STABLE_STATE_COPY",
        "source": "FORMING_CANDLE",
        "confirmations": int(confirmations),
        "stable_seconds": round(float(stable_seconds), 3),
        "timeframe": setup.timeframe,
        "durable_state_untouched": True,
        "no_chase_preserved": True,
    }

    session_decision = op58.evaluate_session_consistency_58(connection, setup)
    setup.metadata["session_consistency"] = session_decision.details
    if not session_decision.allowed:
        setup.metadata["execution_quality_gate"] = {
            "allowed": False,
            "reason": session_decision.reason,
            "profile": "INTRABAR_INTELLIGENCE_6_5",
            "baseline_shadow_profile": base.SHADOW_PROFILE,
        }
        setup.status = "SESSION_BLOCKED"
        runtime.save_setup(connection, setup)
        runtime.console.log(
            f"INTRABAR 6.5 SESSION blocked {setup.symbol} {setup.timeframe}: {session_decision.reason}"
        )
        return setup

    quality_allowed, quality_reason = op58._adaptive_quality_gate(connection, setup, histories)
    setup.metadata["execution_quality_gate"] = {
        "allowed": quality_allowed,
        "reason": quality_reason,
        "profile": "INTRABAR_INTELLIGENCE_6_5",
        "baseline_shadow_profile": base.SHADOW_PROFILE,
    }
    if not quality_allowed:
        setup.status = "QUALITY_BLOCKED"
        runtime.save_setup(connection, setup)
        runtime.console.log(
            f"INTRABAR 6.5 QUALITY blocked {setup.symbol} {setup.timeframe}: {quality_reason}"
        )
        return setup

    decision = runtime.evaluation_guard.decide(connection, setup.created_at)
    applied_risk, risk_multiplier = base._setup_risk(decision, setup)
    setup.metadata["evaluation_guard"] = {
        "status": decision.status,
        "allowed": decision.allowed,
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
        runtime.console.log(
            f"INTRABAR 6.5 PROP GUARD blocked {setup.symbol} {setup.timeframe}: {decision.status} - {decision.reason}"
        )
        return setup

    runtime.save_setup(connection, setup)
    try:
        position = runtime.paper.register_setup(
            setup,
            risk_dollars=applied_risk,
            guard_reason=(
                f"{decision.reason} Operation 6.5 stable intrabar candidate passed. "
                f"Applied {risk_multiplier:.0%} of ${decision.risk_dollars:.2f} cap."
            ),
        )
    except ValueError as exc:
        setup.status = "RISK_REJECTED"
        setup.metadata["geometry_rejection"] = str(exc)
        runtime.save_setup(connection, setup)
        runtime.console.log(
            f"INTRABAR 6.5 RISK GEOMETRY rejected {setup.symbol} {setup.timeframe}: {exc}"
        )
        return setup

    runtime.upsert_paper_trade(connection, position, setup.created_at.isoformat())
    runtime.console.log(
        f"INTRABAR 6.5 REGISTERED {setup.symbol} {setup.timeframe} "
        f"{setup.direction.upper()} {setup.risk_reward:.2f}R risk ${applied_risk:.2f} "
        f"after {confirmations} confirmations / {stable_seconds:.2f}s stability; "
        f"result={position.result or position.status}"
    )
    return setup


def _evaluate_intrabar_65(connection, symbol: str, event_time) -> None:
    if symbol not in runtime.market_state or not runtime.session.strategy_enabled(symbol):
        return

    ict = getattr(runtime.strategy, "ict", None)
    if ict is None:
        return

    for timeframe in INTRABAR_TIMEFRAMES:
        key = (symbol, timeframe)
        context = ict.contexts.get(key)
        if context is None or context.stage not in INTRABAR_STAGES:
            _intrabar_candidates.pop(key, None)
            continue

        previous_eval = _last_intrabar_eval.get(key)
        if previous_eval is not None:
            elapsed = (event_time - previous_eval).total_seconds()
            if elapsed < 0:
                _last_intrabar_eval.pop(key, None)
                _intrabar_candidates.pop(key, None)
                _promoted_bucket.pop(key, None)
            elif elapsed < INTRABAR_EVAL_INTERVAL_SECONDS:
                continue
        _last_intrabar_eval[key] = event_time

        histories, candle = _intrabar_histories(symbol, timeframe, event_time)
        if histories is None or candle is None:
            continue

        bucket_open = candle.open_time
        if _promoted_bucket.get(key) == bucket_open:
            continue

        # Inspect the forming candle with a copy of the durable candle-close
        # state. Any provisional stage mutation dies with the copy.
        probe = deepcopy(ict)
        setup = probe.on_candle(symbol, timeframe, histories)
        if setup is None:
            _intrabar_candidates.pop(key, None)
            continue

        setup.metadata.setdefault("strategy", "ICT_CONFLUENCE")
        setup.metadata.setdefault("checklist_total", 6)
        setup.metadata.setdefault("checklist_score", 6)
        fingerprint = _candidate_fingerprint(setup, bucket_open)
        ready, confirmations, stable_seconds = _stability_ready(
            key,
            fingerprint,
            event_time,
        )
        if not ready:
            continue

        _promoted_bucket[key] = bucket_open
        _intrabar_candidates.pop(key, None)
        _handle_intrabar_setup_65(
            connection,
            setup,
            histories,
            confirmations,
            stable_seconds,
        )


_original_process_price_65 = runtime.process_price


def _process_price_65(connection, symbol, price, bid, ask, timestamp=None):
    event_time = timestamp or runtime.utc_now()
    result = _original_process_price_65(
        connection,
        symbol,
        price,
        bid,
        ask,
        event_time,
    )
    try:
        _evaluate_intrabar_65(connection, symbol, event_time)
    except Exception as exc:
        # Intrabar acceleration is additive. A probe failure must never take the
        # normal candle-close engine or live feed down with it.
        runtime.console.log(f"INTRABAR 6.5 probe error {symbol}: {exc}")
    return result


runtime.process_price = _process_price_65


if __name__ == "__main__":
    runtime.console.log(
        "Operation 6.5 active: stable-state intrabar 1m/5m ICT acceleration, "
        "durable candle-close intelligence preserved, and Operation 6.4 "
        "75% no-chase protection still active."
    )
    op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
