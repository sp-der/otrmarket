from __future__ import annotations

from statistics import mean

from src.strategies.structure import detect_swings


PRIMARY_CONTEXT_TIMEFRAME = {
    "1m": "5m",
    "5m": "15m",
    "15m": "1h",
    "1h": "1h",
}

BAR_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}


def _history_at_or_before(candles, market_time):
    return [candle for candle in candles if candle.close_time <= market_time]


def _structure_bias(candles) -> tuple[str, dict]:
    """Classify higher-timeframe direction from structure, then momentum fallback."""
    if len(candles) < 6:
        return "unknown", {"source": "warmup", "bars": len(candles)}

    window = list(candles[-30:])
    swings = detect_swings(window)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    if len(highs) >= 2 and len(lows) >= 2:
        higher_high = highs[-1].price > highs[-2].price
        higher_low = lows[-1].price > lows[-2].price
        lower_high = highs[-1].price < highs[-2].price
        lower_low = lows[-1].price < lows[-2].price
        if higher_high and higher_low:
            return "bullish", {
                "source": "swing_structure",
                "last_high": highs[-1].price,
                "prior_high": highs[-2].price,
                "last_low": lows[-1].price,
                "prior_low": lows[-2].price,
            }
        if lower_high and lower_low:
            return "bearish", {
                "source": "swing_structure",
                "last_high": highs[-1].price,
                "prior_high": highs[-2].price,
                "last_low": lows[-1].price,
                "prior_low": lows[-2].price,
            }

    recent = window[-6:]
    prior_mean = mean(c.close for c in recent[:3])
    recent_mean = mean(c.close for c in recent[3:])
    avg_range = mean(max(c.range, 1e-12) for c in recent)
    delta = recent_mean - prior_mean
    threshold = avg_range * 0.15

    if delta > threshold:
        bias = "bullish"
    elif delta < -threshold:
        bias = "bearish"
    else:
        bias = "neutral"

    return bias, {
        "source": "close_momentum",
        "delta": delta,
        "threshold": threshold,
        "prior_mean": prior_mean,
        "recent_mean": recent_mean,
    }


def evaluate_ict_context(setup, histories) -> tuple[bool, str, dict]:
    """Require context, sequence, displacement quality, and a fresh entry leg.

    This is deliberately separate from risk/reward. R:R controls sizing after a
    setup is accepted; it no longer acts as a proxy for setup quality.
    """
    details: dict = {
        "profile": "A_PLUS_CONTEXT_5_0",
        "direction": setup.direction,
        "execution_timeframe": setup.timeframe,
    }

    context_timeframe = PRIMARY_CONTEXT_TIMEFRAME.get(setup.timeframe, setup.timeframe)
    details["context_timeframe"] = context_timeframe
    context_history = _history_at_or_before(
        histories.get((setup.symbol, context_timeframe), []),
        setup.created_at,
    )
    bias, bias_details = _structure_bias(context_history)
    details["higher_timeframe_bias"] = bias
    details["higher_timeframe_details"] = bias_details

    if bias == "unknown":
        return (
            False,
            f"{context_timeframe} context is not warmed up enough to grade this setup.",
            details,
        )
    if bias == "neutral":
        return (
            False,
            f"{context_timeframe} context is neutral/choppy; wait for directional structure.",
            details,
        )
    if bias != setup.direction:
        return (
            False,
            f"{context_timeframe} context is {bias} while the setup is {setup.direction}.",
            details,
        )

    displacement = getattr(setup, "displacement", None)
    entry_fvg = getattr(setup, "entry_fvg", None)
    if displacement is None or entry_fvg is None:
        return False, "Missing displacement or entry FVG context.", details

    details["displacement_body_ratio"] = float(displacement.body_ratio)
    details["displacement_range_ratio"] = float(displacement.range_ratio)
    if displacement.direction != setup.direction:
        return False, "Displacement direction does not match the setup direction.", details
    if entry_fvg.direction != setup.direction:
        return False, "Entry FVG direction does not match the setup direction.", details
    if entry_fvg.formed_at <= displacement.candle_time:
        return False, "Entry FVG did not form after the confirmed displacement.", details

    # The detector already requires 1.50x body / 1.30x range. Execution asks for
    # a slightly stronger impulse so borderline displacement stays research-only.
    if displacement.body_ratio < 1.65 or displacement.range_ratio < 1.35:
        return (
            False,
            "Displacement is valid but not A+ strength "
            f"({displacement.body_ratio:.2f}x body / {displacement.range_ratio:.2f}x range).",
            details,
        )

    bar_seconds = BAR_SECONDS.get(setup.timeframe, 60)
    fvg_age_bars = max(
        0.0,
        (setup.created_at - entry_fvg.formed_at).total_seconds() / bar_seconds,
    )
    details["entry_fvg_age_bars"] = round(fvg_age_bars, 2)
    if fvg_age_bars > 2.0:
        return (
            False,
            f"Entry FVG is stale at {fvg_age_bars:.1f} bars old; require a fresh displacement leg.",
            details,
        )

    entry_type = str(setup.metadata.get("entry_type", "FVG_MIDPOINT"))
    details["entry_type"] = entry_type
    if entry_type == "ORDER_BLOCK":
        # Fallback blocks need a visibly stronger impulse because the entry is
        # farther removed from the actual imbalance than the preferred FVG/OTE.
        if displacement.body_ratio < 1.90 or displacement.range_ratio < 1.50:
            return (
                False,
                "Order-block fallback needs stronger displacement "
                "(>=1.90x body and >=1.50x range).",
                details,
            )

    details["quality_score"] = "5/5"
    return True, "Higher-timeframe context and A+ ICT sequence confirmed.", details
