from __future__ import annotations

from src.strategies.regime import classify_regime


CONTEXT_TIMEFRAMES = ("5m", "15m", "30m")
REVERSAL_CATALYSTS = {"liquidity_sweep", "smt"}


def _causal_regime(symbol: str, timeframe: str, created_at, histories) -> dict:
    candles = [
        candle
        for candle in histories.get((symbol, timeframe), [])
        if candle.close_time <= created_at
    ]
    return classify_regime(candles)


def assess_one_minute_reversal_context(setup, histories) -> tuple[bool, str, dict]:
    """Fail closed on 1m MSS reversals that lack larger-chart confirmation.

    The 1m chart is an execution timeframe, not permission to call a new trend by
    itself. A reversal therefore needs a real liquidity catalyst, 5m alignment,
    no 15m/30m opposition, and at least one 15m/30m confirming direction.
    """
    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    if strategy != "MSS_REVERSAL" or str(setup.timeframe) != "1m":
        return True, "Operation 7.2 reversal context guard not applicable.", {
            "operation": "7.2R",
            "applicable": False,
        }

    direction = str(setup.direction)
    trigger = str(setup.trigger_type or "").lower()
    regimes = {
        timeframe: _causal_regime(setup.symbol, timeframe, setup.created_at, histories)
        for timeframe in CONTEXT_TIMEFRAMES
    }
    directions = {
        timeframe: str(regime.get("direction") or "neutral")
        for timeframe, regime in regimes.items()
    }
    details = {
        "operation": "7.2R",
        "applicable": True,
        "strategy": strategy,
        "direction": direction,
        "trigger": trigger,
        "context_directions": directions,
        "context_regimes": {
            timeframe: regime.get("regime")
            for timeframe, regime in regimes.items()
        },
        "required": {
            "reversal_catalyst": sorted(REVERSAL_CATALYSTS),
            "five_minute_alignment": True,
            "fifteen_minute_may_oppose": False,
            "thirty_minute_may_oppose": False,
            "15m_or_30m_confirmation": True,
        },
    }

    if trigger not in REVERSAL_CATALYSTS:
        return False, (
            "Operation 7.2R blocked 1m MSS reversal: market-structure shift alone is not enough; "
            "require a liquidity sweep or NQ/ES SMT catalyst."
        ), details

    if directions["5m"] != direction:
        return False, (
            f"Operation 7.2R blocked 1m {direction} reversal: immediate 5m context is "
            f"{directions['5m']}, not {direction}."
        ), details

    for timeframe in ("15m", "30m"):
        context_direction = directions[timeframe]
        if context_direction not in {"neutral", direction}:
            return False, (
                f"Operation 7.2R blocked 1m {direction} reversal: {timeframe} context remains "
                f"{context_direction}; 1m cannot overrule the larger move."
            ), details

    if direction not in {directions["15m"], directions["30m"]}:
        return False, (
            f"Operation 7.2R blocked 1m {direction} reversal: 5m aligned, but neither 15m nor 30m "
            "has confirmed the new direction yet."
        ), details

    return True, (
        f"Operation 7.2R 1m reversal confirmed: {trigger.replace('_', ' ')} catalyst, "
        f"5m {direction}, 15m {directions['15m']}, 30m {directions['30m']}."
    ), details
