from __future__ import annotations

import asyncio
from types import MethodType

from src import main_62 as op62
from src.execution import paper as paper_module


runtime = op62.runtime
continuation = op62.continuation


# ---------------------------------------------------------------------------
# Operation 6.3A: pending-entry lifetimes now match the intended execution
# cadence instead of falling back to the old 6/4-bar limits.
# ---------------------------------------------------------------------------
PENDING_BARS_63 = {
    "1m": 15,
    "5m": 8,
    "15m": 4,
    "1h": 2,
}
paper_module._PENDING_BARS.update(PENDING_BARS_63)


# ---------------------------------------------------------------------------
# Operation 6.3B: normalize the Operation 5.8 handoff to a collection.
# Operation 6.1 iterates the return value, while 5.8 historically returned a
# single StrategySetup. That mismatch produced "StrategySetup is not iterable"
# inside the NinjaTrader collector after otherwise valid setup registration.
# ---------------------------------------------------------------------------
def _normalize_handled(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


_original_evaluate_strategy_58 = op62.op61.op59.op58.evaluate_strategy_58


def _evaluate_strategy_58_collection(connection, symbol: str, timeframe: str):
    return _normalize_handled(
        _original_evaluate_strategy_58(connection, symbol, timeframe)
    )


op62.op61.op59.op58.evaluate_strategy_58 = _evaluate_strategy_58_collection


# ---------------------------------------------------------------------------
# Operation 6.3C: attach entry-life diagnostics before an order is invalidated.
# The 75% no-chase threshold is deliberately unchanged.
# ---------------------------------------------------------------------------
_original_invalidate_pending = paper_module._invalidate_pending


def _invalidate_pending_63(position, *, timestamp, price: float, result: str) -> None:
    setup = position.setup
    created_at = paper_module._aware_utc(setup.created_at)
    event_at = paper_module._aware_utc(timestamp)
    bar_seconds = paper_module._BAR_SECONDS.get(setup.timeframe, 60)
    expiry_bars = paper_module._PENDING_BARS.get(setup.timeframe, 4)
    age_seconds = max(0.0, (event_at - created_at).total_seconds())
    age_bars = age_seconds / max(1, bar_seconds)
    target_progress = paper_module._preentry_target_progress(setup, float(price))

    setup.metadata["entry_lifecycle_63"] = {
        "result": result,
        "event_price": float(price),
        "age_seconds": round(age_seconds, 3),
        "age_bars": round(age_bars, 3),
        "expiry_limit_bars": int(expiry_bars),
        "target_progress": round(float(target_progress), 6),
        "target_progress_pct": round(float(target_progress) * 100.0, 2),
        "stale_threshold": float(paper_module._MAX_PREENTRY_TARGET_PROGRESS),
        "operation": 6.3,
    }
    _original_invalidate_pending(
        position,
        timestamp=event_at,
        price=float(price),
        result=result,
    )


paper_module._invalidate_pending = _invalidate_pending_63


# Persist the lifecycle payload after the normal 6.2 persistence/continuation
# arm logic has run, and emit a compact audit line for Railway tracing.
_original_upsert_paper_trade_63 = runtime.upsert_paper_trade


def _upsert_paper_trade_63(connection, position, updated_at):
    _original_upsert_paper_trade_63(connection, position, updated_at)
    lifecycle = position.setup.metadata.get("entry_lifecycle_63")
    if not lifecycle:
        return

    position.setup.status = position.status
    runtime.save_setup(connection, position.setup)
    runtime.console.log(
        f"ENTRY LIFE 6.3 {position.setup.symbol} {position.setup.timeframe} "
        f"{position.setup.direction} {lifecycle['result']} "
        f"age={lifecycle['age_bars']:.2f}/{lifecycle['expiry_limit_bars']} bars "
        f"target_progress={lifecycle['target_progress_pct']:.2f}%"
    )


runtime.upsert_paper_trade = _upsert_paper_trade_63


# ---------------------------------------------------------------------------
# Operation 6.3D: continuation breadcrumbs. The existing strategy diagnostic
# row still carries the live state; this additionally records each state change
# in Railway logs so stale-thesis follow-through can be audited end-to-end.
# ---------------------------------------------------------------------------
_original_continuation_diag = continuation._diag
_continuation_last_stage: dict[tuple[str, str], str] = {}


def _continuation_diag_63(
    self,
    symbol,
    timeframe,
    market_time,
    stage,
    direction=None,
    pullback=False,
    displacement=False,
    fvg=False,
    rr=False,
    note="",
    setup_id=None,
):
    item = _original_continuation_diag(
        symbol,
        timeframe,
        market_time,
        stage,
        direction,
        pullback,
        displacement,
        fvg,
        rr,
        note,
        setup_id,
    )
    key = (symbol, timeframe)
    previous = _continuation_last_stage.get(key)
    if previous != stage:
        _continuation_last_stage[key] = stage
        runtime.console.log(
            f"CONTINUATION 6.3 {symbol} {timeframe} {direction or '-'} "
            f"{previous or 'ARMED'} -> {stage}: {note}"
        )
    return item


continuation._diag = MethodType(_continuation_diag_63, continuation)


if __name__ == "__main__":
    runtime.console.log(
        "Operation 6.3 active: normalized setup handoff, extended pending-entry "
        "windows, 75% no-chase preserved, lifecycle + continuation tracing enabled."
    )
    op62._restore_progress()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
