from __future__ import annotations

import asyncio
from datetime import timezone

from src import main_59 as op59
from src.strategies import reversal as reversal_module
from src.strategies.execution_quality import _structure_bias

runtime = op59.runtime

# Operation 6.1: let the existing no-chase reversal engine work on 1m too.
reversal_module.BAR_SECONDS["1m"] = 60
reversal_module.FRESHNESS_BARS["1m"] = 8
runtime.strategy.reversal.min_rr = 1.25

ACTIVE_FUTURES = {"NQ", "ES", "GC"}
FAILED_THESIS_MEMORY: dict[tuple[str, str], dict] = {}


def _cap_risk(setup, cap: float, tier: str, reason: str) -> None:
    try:
        current = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        current = 1.0
    setup.metadata["risk_multiplier"] = min(current, cap)
    setup.metadata["execution_tier"] = tier
    setup.metadata["tier_reason"] = reason


def _bias(histories, symbol: str, timeframe: str, created_at) -> str:
    candles = [c for c in histories.get((symbol, timeframe), []) if c.close_time <= created_at]
    return _structure_bias(candles)[0]


def _narrative(setup, histories) -> dict:
    if setup.timeframe == "1m":
        primary_tf, intermediate_tf, narrative_tf = "5m", "15m", "30m"
    elif setup.timeframe == "5m":
        primary_tf, intermediate_tf, narrative_tf = "15m", "30m", "1h"
    else:
        primary_tf, intermediate_tf, narrative_tf = "30m", "1h", "1h"
    primary = _bias(histories, setup.symbol, primary_tf, setup.created_at)
    intermediate = _bias(histories, setup.symbol, intermediate_tf, setup.created_at)
    narrative = _bias(histories, setup.symbol, narrative_tf, setup.created_at)
    supporters = sum(x == setup.direction for x in (intermediate, narrative))
    return {
        "primary_timeframe": primary_tf,
        "primary": primary,
        "intermediate_timeframe": intermediate_tf,
        "intermediate": intermediate,
        "narrative_timeframe": narrative_tf,
        "narrative": narrative,
        "supports_setup": supporters >= 1,
        "strong_support": supporters == 2,
    }


def _risk_guards(connection, setup) -> tuple[bool, str]:
    ok, reason = op59.op58.base._active_risk_gate(setup)
    if not ok:
        return False, reason
    ok, reason = op59.op58.base._global_loss_cooldown(connection, setup)
    if not ok:
        return False, reason
    ok, reason = op59.op58.base._same_symbol_cooldown(connection, setup)
    if not ok:
        return False, reason
    return True, "Independent risk/cooldown guards cleared."


def _post_loss_risk(connection, setup) -> tuple[bool, str]:
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    stats = op59._futures_day_stats(
        connection,
        created_at.astimezone(timezone.utc),
        op59.SessionConsistencyConfig.from_env().timezone,
    )
    setup.metadata["futures_day_stats"] = stats
    if stats["losses"] < 1:
        return True, "No realized futures loss today; normal setup risk applies."

    rr = float(setup.risk_reward or 0.0)
    displacement = getattr(setup, "displacement", None)
    body = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
    candle_range = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
    trigger = str(setup.trigger_type or "").lower()

    if rr >= 3.0:
        cap = 0.80 if body >= 1.65 and candle_range >= 1.35 else 0.65
    elif rr >= 2.0:
        cap = 0.65 if body >= 1.65 and candle_range >= 1.35 else 0.50
    elif rr >= 1.5:
        cap = 0.50 if body >= 1.75 and candle_range >= 1.40 else 0.40
    elif rr >= 1.25 and (trigger == "smt" or (body >= 1.90 and candle_range >= 1.50)):
        cap = 0.35
    else:
        return False, (
            "Post-loss adaptive risk rejected a low-room/weak follow-up: "
            f"{rr:.2f}R, {body:.2f}x body, {candle_range:.2f}x range."
        )

    reason = f"Post-loss setup remains eligible at reduced risk ({rr:.2f}R, cap {cap:.0%})."
    _cap_risk(setup, cap, "POST_LOSS_ADAPTIVE", reason)
    setup.metadata["post_loss_quality"] = {
        "allowed": True,
        "adaptive_risk": True,
        "risk_cap": cap,
        "source": "OPERATION_6_1",
    }
    return True, reason


def _failed_thesis_for(setup) -> dict | None:
    memory = FAILED_THESIS_MEMORY.get((setup.symbol, setup.timeframe))
    if not memory or memory["direction"] == setup.direction:
        return None
    age = (setup.created_at - memory["created_at"]).total_seconds() / 60.0
    if age < 0 or age > 35:
        FAILED_THESIS_MEMORY.pop((setup.symbol, setup.timeframe), None)
        return None
    return {**memory, "age_minutes": round(age, 2)}


def _salvage_ict(connection, setup, histories, reason: str) -> tuple[bool, str]:
    rr = float(setup.risk_reward or 0.0)
    displacement = getattr(setup, "displacement", None)
    body = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
    candle_range = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
    trigger = str(setup.trigger_type or "").lower()
    mtf = _narrative(setup, histories)
    setup.metadata["multi_timeframe_narrative_61"] = mtf
    lower_reason = reason.lower()

    # A 5m counter-move can be a retracement rather than a true narrative flip.
    if (
        setup.timeframe == "1m"
        and "context is" in lower_reason
        and "while the setup is" in lower_reason
        and mtf["supports_setup"]
        and rr >= 1.25
        and body >= 1.65
        and candle_range >= 1.35
        and trigger in {"smt", "liquidity_sweep"}
    ):
        cap = 0.60 if mtf["strong_support"] else 0.50
        rescue = (
            f"{mtf['primary_timeframe']} disagrees, but {mtf['intermediate_timeframe']}/"
            f"{mtf['narrative_timeframe']} preserve the {setup.direction} narrative; "
            f"treat primary move as retracement and cap risk at {cap:.0%}."
        )
        _cap_risk(setup, cap, "MTF_RETRACEMENT_REDUCED", rescue)
        ok, guard_reason = _risk_guards(connection, setup)
        return ok, rescue if ok else guard_reason

    # Keep the failed 1.21R NQ long blocked. Sweep-only rescue starts at 1.35R.
    if (
        setup.timeframe == "1m"
        and trigger == "liquidity_sweep"
        and "1m non-smt setup needs" in lower_reason
        and rr >= 1.35
        and body >= 1.90
        and candle_range >= 1.50
    ):
        context_ok, context_reason, details = op59.op58.evaluate_ict_context(setup, histories)
        setup.metadata["a_plus_context"] = details
        if context_ok:
            b_ok, b_reason = op59.op58.base._b_plus_execution_gate(connection, setup)
            if not b_ok:
                return False, b_reason
            cap = 0.45 if rr < 1.50 else 0.60
            rescue = f"Strong 1m sweep continuation rescued at {cap:.0%} risk ({rr:.2f}R)."
            _cap_risk(setup, cap, "ONE_MINUTE_SWEEP_B_PLUS", rescue)
            ok, guard_reason = _risk_guards(connection, setup)
            return ok, rescue if ok else guard_reason
        if "context is" in context_reason.lower() and mtf["supports_setup"] and rr >= 1.50:
            rescue = "Strong sweep has tactical context conflict but aligned 15m/30m narrative."
            _cap_risk(setup, 0.40, "ONE_MINUTE_SWEEP_MTF_REDUCED", rescue)
            ok, guard_reason = _risk_guards(connection, setup)
            return ok, rescue if ok else guard_reason

    # B+ becomes a reduced-risk tier instead of universally needing 2.50R.
    if (
        "b+ setup offers only" in lower_reason
        and rr >= 1.50
        and body >= 1.65
        and candle_range >= 1.35
    ):
        b_ok, b_reason = op59.op58.base._b_plus_execution_gate(connection, setup)
        if not b_ok:
            return False, b_reason
        cap = 0.50 if rr >= 2.0 else 0.35
        rescue = f"B+ chart structure accepted at {cap:.0%} risk with {rr:.2f}R."
        _cap_risk(setup, cap, "B_PLUS_ADAPTIVE_61", rescue)
        ok, guard_reason = _risk_guards(connection, setup)
        return ok, rescue if ok else guard_reason

    return False, reason


def _adaptive_quality_gate_61(connection, setup, histories=None):
    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    rr = float(setup.risk_reward or 0.0)
    allowed, reason = op59._original_adaptive_quality_gate(connection, setup, histories)

    if not allowed and strategy == "ICT_CONFLUENCE" and histories is not None:
        allowed, reason = _salvage_ict(connection, setup, histories, reason)

    if not allowed and strategy == "MSS_REVERSAL" and setup.timeframe == "1m":
        failed = _failed_thesis_for(setup)
        displacement = getattr(setup, "displacement", None)
        body = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
        candle_range = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
        if failed and rr >= 1.25 and body >= 1.65 and candle_range >= 1.35:
            cap = 0.45 if rr >= 1.50 else 0.30
            rescue = (
                f"Failed {failed['direction']} thesis flipped into confirmed {setup.direction} "
                f"1m MSS reversal; first-pullback entry eligible at {cap:.0%} risk."
            )
            setup.metadata["failed_thesis_reversal"] = failed
            _cap_risk(setup, cap, "FAILED_THESIS_REVERSAL", rescue)
            guard_ok, guard_reason = _risk_guards(connection, setup)
            allowed, reason = (True, rescue) if guard_ok else (False, guard_reason)

    if not allowed:
        return False, reason

    post_ok, post_reason = _post_loss_risk(connection, setup)
    if not post_ok:
        return False, post_reason
    tier = setup.metadata.get("execution_tier", setup.metadata.get("session_tier", "CORE"))
    return True, f"Operation 6.1 adaptive quality passed ({tier}). {post_reason}"


def _ensure_counterfactual_table(connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS counterfactual_setups (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            target_price REAL NOT NULL,
            created_at TEXT NOT NULL,
            blocked_status TEXT NOT NULL,
            blocked_reason TEXT,
            outcome TEXT NOT NULL DEFAULT 'OPEN',
            resolved_at TEXT,
            max_favorable_r REAL NOT NULL DEFAULT 0,
            max_adverse_r REAL NOT NULL DEFAULT 0,
            last_checked TEXT
        )
        """
    )
    connection.commit()


def _track_blocked(connection, setup) -> None:
    if setup.symbol not in ACTIVE_FUTURES or str(setup.status).upper() not in {"QUALITY_BLOCKED", "SESSION_BLOCKED"}:
        return
    reason = setup.metadata.get("execution_quality_gate", {}).get("reason") or "Blocked candidate"
    connection.execute(
        """
        INSERT OR IGNORE INTO counterfactual_setups (
            setup_id, symbol, timeframe, direction, entry_price, stop_price,
            target_price, created_at, blocked_status, blocked_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            setup.setup_id, setup.symbol, setup.timeframe, setup.direction,
            float(setup.entry_price), float(setup.stop_price), float(setup.target_price),
            setup.created_at.isoformat(), str(setup.status), str(reason),
        ),
    )
    connection.commit()


def _update_counterfactuals(connection, symbol: str, timeframe: str, histories) -> None:
    candles = histories.get((symbol, timeframe), [])
    if not candles:
        return
    latest = candles[-1]
    rows = connection.execute(
        """
        SELECT setup_id, direction, entry_price, stop_price, target_price,
               max_favorable_r, max_adverse_r
        FROM counterfactual_setups
        WHERE symbol = ? AND timeframe = ? AND outcome = 'OPEN'
        """,
        (symbol, timeframe),
    ).fetchall()
    for setup_id, direction, entry, stop, target, max_fav, max_adv in rows:
        entry, stop, target = float(entry), float(stop), float(target)
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        if direction == "bullish":
            fav = max(0.0, (latest.high - entry) / risk)
            adv = max(0.0, (entry - latest.low) / risk)
            target_hit, stop_hit = latest.high >= target, latest.low <= stop
        else:
            fav = max(0.0, (entry - latest.low) / risk)
            adv = max(0.0, (latest.high - entry) / risk)
            target_hit, stop_hit = latest.low <= target, latest.high >= stop
        outcome = "AMBIGUOUS_SAME_BAR" if target_hit and stop_hit else "WOULD_WIN" if target_hit else "WOULD_LOSE" if stop_hit else "OPEN"
        connection.execute(
            """
            UPDATE counterfactual_setups
            SET outcome = ?, resolved_at = CASE WHEN ? = 'OPEN' THEN resolved_at ELSE ? END,
                max_favorable_r = ?, max_adverse_r = ?, last_checked = ?
            WHERE setup_id = ?
            """,
            (
                outcome, outcome, latest.close_time.isoformat(),
                max(float(max_fav or 0.0), fav), max(float(max_adv or 0.0), adv),
                latest.close_time.isoformat(), setup_id,
            ),
        )
    if rows:
        connection.commit()


def _remember_failed_thesis(setup) -> None:
    if (
        setup.symbol not in {"NQ", "ES"}
        or setup.timeframe != "1m"
        or str(setup.status).upper() != "QUALITY_BLOCKED"
        or str(setup.metadata.get("strategy", "ICT_CONFLUENCE")) != "ICT_CONFLUENCE"
        or float(setup.risk_reward or 0.0) < 1.0
    ):
        return
    FAILED_THESIS_MEMORY[(setup.symbol, setup.timeframe)] = {
        "setup_id": setup.setup_id,
        "direction": setup.direction,
        "created_at": setup.created_at,
        "entry_price": float(setup.entry_price),
        "stop_price": float(setup.stop_price),
        "target_price": float(setup.target_price),
        "blocked_reason": setup.metadata.get("execution_quality_gate", {}).get("reason"),
    }


def evaluate_strategy_61(connection, symbol: str, timeframe: str):
    _ensure_counterfactual_table(connection)
    histories = runtime.histories_snapshot()
    _update_counterfactuals(connection, symbol, timeframe, histories)
    handled = op59.op58.evaluate_strategy_58(connection, symbol, timeframe) or []
    for setup in handled:
        _track_blocked(connection, setup)
        _remember_failed_thesis(setup)
        if str(setup.metadata.get("strategy", "")) == "MSS_REVERSAL" and str(setup.status).upper() not in {"QUALITY_BLOCKED", "SESSION_BLOCKED"}:
            FAILED_THESIS_MEMORY.pop((setup.symbol, setup.timeframe), None)
    return handled


op59.op58._adaptive_quality_gate = _adaptive_quality_gate_61
runtime.evaluate_strategy = evaluate_strategy_61


if __name__ == "__main__":
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
