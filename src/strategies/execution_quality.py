from __future__ import annotations

from statistics import mean

from src.strategies.market_intelligence import evaluate_market_narrative
from src.strategies.structure import detect_swings


PRIMARY_CONTEXT_TIMEFRAME = {
    "1m": "5m",
    "5m": "15m",
    "15m": "1h",
    "1h": "1h",
}

NARRATIVE_CONTEXT_TIMEFRAME = {
    "1m": "30m",
    "5m": "30m",
    "15m": "1h",
    "1h": "1h",
}

BAR_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}


def _grade(score: int) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 72:
        return "B+"
    if score >= 60:
        return "B"
    return "RESEARCH"


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
    """Grade a setup using structure, sequence, candle quality and market narrative.

    Market Intelligence 1.0 keeps the proven ICT sequence intact, then adds a
    second opinion built from multi-timeframe structure, dealing range,
    equal-liquidity pools, active/inverse FVGs, order blocks/breaker candidates,
    rejection behavior, session context and NQ/ES SMT. The market map does not
    invent trades; it grades candidates the strategy engine already discovered.
    """
    details: dict = {
        "profile": "A_PLUS_CONTEXT_MARKET_INTELLIGENCE_1_0",
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

    narrative_timeframe = NARRATIVE_CONTEXT_TIMEFRAME.get(
        setup.timeframe, context_timeframe
    )
    narrative_history = _history_at_or_before(
        histories.get((setup.symbol, narrative_timeframe), []), setup.created_at
    )
    narrative_bias, narrative_details = _structure_bias(narrative_history)
    details["narrative_timeframe"] = narrative_timeframe
    details["narrative_bias"] = narrative_bias
    details["narrative_details"] = narrative_details

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
        if displacement.body_ratio < 1.90 or displacement.range_ratio < 1.50:
            return (
                False,
                "Order-block fallback needs stronger displacement "
                "(>=1.90x body and >=1.50x range).",
                details,
            )

    market_narrative = evaluate_market_narrative(setup, histories)
    details["market_intelligence"] = market_narrative
    details["market_intelligence_score"] = market_narrative["score"]
    details["market_intelligence_grade"] = market_narrative["grade"]

    if market_narrative.get("opposed_votes", 0) >= 3 and market_narrative.get("aligned_votes", 0) == 0:
        return (
            False,
            "Market Intelligence sees broad higher-timeframe structure opposing this candidate; keep it research-only.",
            details,
        )
    # Sparse historical/test contexts should not be silenced merely because the
    # new map lacks every timeframe. A very weak map can block only when it has
    # enough directional evidence to be meaningful.
    if market_narrative["score"] < 35 and len(market_narrative.get("structural_votes", [])) >= 2:
        return (
            False,
            f"Market Intelligence narrative is too weak at {market_narrative['score']}/100.",
            details,
        )

    trigger_is_smt = str(setup.trigger_type).lower() == "smt"
    components = {
        "local_context": 15,
        "narrative_context": (
            12
            if narrative_bias == setup.direction
            else 6
            if narrative_bias in {"unknown", "neutral"}
            else 0
        ),
        "displacement": 18,
        "fresh_entry": 12 if fvg_age_bars <= 2.0 else 0,
        # SMT earns more weight specifically so an otherwise valid setup can
        # survive an opposing 30m narrative at A grade, while a sweep-only
        # counter-narrative setup remains reduced-risk B+.
        "trigger": 12 if trigger_is_smt else 6,
        "entry_location": 8 if entry_type != "ORDER_BLOCK" else 6,
        "target_room": min(10, max(0, round(float(setup.risk_reward or 0) * 5))),
        "market_intelligence": min(15, round(market_narrative["score"] * 0.15)),
    }
    score = min(100, int(sum(components.values())))
    grade = _grade(score)
    details["score_components"] = components
    details["quality_score"] = score
    details["quality_grade"] = grade
    details["narrative_conflict"] = narrative_bias not in {
        setup.direction,
        "unknown",
        "neutral",
    }

    if score < 72:
        return (
            False,
            f"Chart Intelligence score {score}/100 ({grade}); below B+ execution research floor (72).",
            details,
        )
    return (
        True,
        f"Chart Intelligence score {score}/100 ({grade}) with Market Intelligence {market_narrative['score']}/100; eligible for its grade's risk tier.",
        details,
    )
