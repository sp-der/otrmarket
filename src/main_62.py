from __future__ import annotations

import asyncio
import json
from types import MethodType

from src import main_61 as op61
from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.runtime.recovery import restore_active_paper_positions, restore_recent_stale_watches
from src.strategies import reversal as reversal_module
from src.strategies.continuation import ContinuationRearmEngine
from src.strategies.models import StrategySetup


runtime = op61.runtime
continuation = ContinuationRearmEngine(min_rr=1.50)


# ---------------------------------------------------------------------------
# Operation 6.2A: stale first-entry -> continuation re-arm.
# ---------------------------------------------------------------------------
_original_on_candle_all = runtime.strategy.on_candle_all
_original_diagnostic = runtime.strategy.diagnostic
_original_clear_symbol = runtime.strategy.clear_symbol


def _diag_progress(item):
    if not item:
        return -1.0
    if item.get("checklist_total"):
        return float(item.get("checklist_score", 0)) / max(
            1.0, float(item.get("checklist_total", 6))
        )
    return sum(
        int(bool(item.get(key)))
        for key in ("pd_array", "signal", "displacement", "entry_fvg", "retracement", "rr")
    ) / 6.0


def _on_candle_all_62(self, symbol, timeframe, histories):
    candidates = list(_original_on_candle_all(symbol, timeframe, histories))
    if not candidates:
        rearm = continuation.on_candle(symbol, timeframe, histories)
        if rearm:
            candidates.append(rearm)
            self.last_setup = rearm
    return candidates


def _diagnostic_62(self, symbol, timeframe):
    base = _original_diagnostic(symbol, timeframe)
    rearm = continuation.diagnostic(symbol, timeframe)
    if not rearm:
        return base
    if not base or _diag_progress(rearm) > _diag_progress(base):
        return rearm
    return base


def _clear_symbol_62(self, symbol):
    _original_clear_symbol(symbol)
    continuation.clear_symbol(symbol)


runtime.strategy.on_candle_all = MethodType(_on_candle_all_62, runtime.strategy)
runtime.strategy.diagnostic = MethodType(_diagnostic_62, runtime.strategy)
runtime.strategy.clear_symbol = MethodType(_clear_symbol_62, runtime.strategy)


# Persist a stale entry first, then convert only its thesis into a continuation
# watch. The invalid order itself stays dead so the bot never chases it.
_original_upsert_paper_trade = runtime.upsert_paper_trade


def _upsert_paper_trade_62(connection, position, updated_at):
    _original_upsert_paper_trade(connection, position, updated_at)
    if str(position.result or "") == "STALE_MOVE_BEFORE_ENTRY":
        if continuation.arm_from_stale(position.setup, updated_at):
            runtime.console.log(
                f"CONTINUATION 6.2 armed {position.setup.symbol} "
                f"{position.setup.timeframe} {position.setup.direction} after stale first entry"
            )


runtime.upsert_paper_trade = _upsert_paper_trade_62


# ---------------------------------------------------------------------------
# Operation 6.2B: continuation quality tier.
# ---------------------------------------------------------------------------
def _continuation_trades_today(connection, setup) -> int:
    candidate_day = op61.op59.op58.base._trading_day(setup.created_at)
    rows = connection.execute(
        """
        SELECT p.status, s.created_at, s.payload_json
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id = p.setup_id
        WHERE p.status IN ('PENDING', 'OPEN', 'CLOSED')
        """
    ).fetchall()
    count = 0
    for status, created_at, payload_json in rows:
        if op61.op59.op58.base._trading_day(created_at) != candidate_day:
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if payload.get("metadata", {}).get("strategy") == "TREND_CONTINUATION_REARM":
            count += 1
    return count


def _adaptive_quality_gate_62(connection, setup, histories=None):
    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    if strategy != "TREND_CONTINUATION_REARM":
        return op61._adaptive_quality_gate_61(connection, setup, histories)

    rr = float(setup.risk_reward or 0.0)
    displacement = getattr(setup, "displacement", None)
    body = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
    candle_range = float(getattr(displacement, "range_ratio", 0.0) or 0.0)

    if rr < 1.50:
        return False, f"Continuation re-arm offers only {rr:.2f}R; require 1.50R."
    if body < 1.50 or candle_range < 1.30:
        return False, (
            "Continuation re-arm needs confirmed resumption displacement "
            f"(>=1.50x body / >=1.30x range); got {body:.2f}x/{candle_range:.2f}x."
        )
    if histories is None:
        return False, "Continuation re-arm requires multi-timeframe context."

    mtf = op61._narrative(setup, histories)
    setup.metadata["multi_timeframe_narrative_62"] = mtf
    primary_support = mtf.get("primary") == setup.direction
    if not (primary_support or mtf.get("supports_setup")):
        return False, (
            f"Continuation thesis lacks higher-timeframe support: "
            f"{mtf['primary_timeframe']}={mtf['primary']}, "
            f"{mtf['intermediate_timeframe']}={mtf['intermediate']}, "
            f"{mtf['narrative_timeframe']}={mtf['narrative']}."
        )

    if _continuation_trades_today(connection, setup) >= 2:
        return False, "Daily continuation re-arm execution limit reached (2/2)."

    cap = 0.65 if rr >= 2.0 and mtf.get("strong_support") else 0.50
    reason = (
        f"Stale thesis renewed by pullback + displacement + fresh pre-armed FVG "
        f"at {rr:.2f}R; continuation risk capped at {cap:.0%}."
    )
    op61._cap_risk(setup, cap, "CONTINUATION_REARM_62", reason)

    guard_ok, guard_reason = op61._risk_guards(connection, setup)
    if not guard_ok:
        return False, guard_reason

    post_ok, post_reason = op61._post_loss_risk(connection, setup)
    if not post_ok:
        return False, post_reason
    return True, f"Operation 6.2 continuation quality passed. {reason} {post_reason}"


op61.op59.op58._adaptive_quality_gate = _adaptive_quality_gate_62


# ---------------------------------------------------------------------------
# Operation 6.2C: pre-arm MSS reversal limits before the pullback touches.
# ---------------------------------------------------------------------------
def _prearmed_reversal_build_setup_62(self, symbol, timeframe, candles, context, fvg):
    latest = candles[-1]
    candidates = [
        ("FVG_MIDPOINT", fvg.midpoint),
        ("OTE_70_5", self._ote(context, 0.705)),
        ("OTE_79", self._ote(context, 0.79)),
    ]

    for entry_type, raw_entry in candidates:
        if context.direction == "bearish" and raw_entry <= latest.close:
            continue
        if context.direction == "bullish" and raw_entry >= latest.close:
            continue

        raw_stop = context.stop_anchor * (
            1.0001 if context.direction == "bearish" else 0.9999
        )
        raw_target = self._target(candles, context.direction, raw_entry)
        if raw_target is None:
            continue
        entry, stop, target = normalize_trade_prices(
            symbol, context.direction, raw_entry, raw_stop, raw_target
        )
        geometry = validate_trade_geometry(
            symbol, context.direction, entry, stop, target
        )
        if not geometry.valid:
            continue
        rr = float(geometry.risk_reward or 0.0)
        if rr < self.min_rr:
            continue

        setup = StrategySetup(
            setup_id=__import__("uuid").uuid4().hex[:12],
            symbol=symbol,
            timeframe=timeframe,
            direction=context.direction,
            created_at=latest.close_time,
            pd_array=fvg,
            trigger_type=context.trigger_type,
            trigger_details=context.trigger_details,
            displacement=context.displacement,
            entry_fvg=fvg,
            entry_price=float(entry),
            stop_price=float(stop),
            target_price=float(target),
            risk_reward=rr,
            metadata={
                "strategy": "MSS_REVERSAL",
                "operation": 6.2,
                "entry_type": entry_type,
                "entry_priority": ["FVG_MIDPOINT", "OTE_70_5", "OTE_79"],
                "mss_break_level": context.break_level,
                "regime_before": context.regime_before,
                "no_chase": True,
                "prearmed_limit": True,
                "freshness_bars": reversal_module.FRESHNESS_BARS[timeframe],
                "risk_multiplier": 0.75,
            },
        )
        return (
            setup,
            f"Pre-armed first-pullback reversal via {entry_type} at {rr:.2f}R; "
            "limit is waiting before price touches it.",
        )

    return (
        None,
        "Fresh reversal FVG exists, but no untraded pullback limit clears "
        f"{self.min_rr:.2f}R; waiting instead of chasing.",
    )


reversal_module.ReversalEngine._build_setup = _prearmed_reversal_build_setup_62


def _restore_progress_62():
    connection = runtime.get_connection()
    try:
        restored, errors = restore_active_paper_positions(connection, runtime.paper)
        stale_watches = restore_recent_stale_watches(connection, continuation)
        runtime.console.log(
            f"Operation 6.2 restart recovery: restored {restored} active/pending "
            f"paper position(s), {stale_watches} continuation watch(es); "
            "trade history left untouched."
        )
        for error in errors[:3]:
            runtime.console.log(f"Operation 6.2 recovery warning: {error}")
    finally:
        connection.close()


if __name__ == "__main__":
    _restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\nStopped.")
