from __future__ import annotations

from statistics import mean

from src.strategies.ict import detect_displacement
from src.strategies.structure import detect_swings


def classify_regime(candles) -> dict:
    """Classify the chart state using only candles available at evaluation time."""
    if len(candles) < 8:
        return {
            "regime": "WARMUP",
            "direction": "neutral",
            "confidence": 0,
            "bars": len(candles),
        }

    window = list(candles[-30:])
    latest = window[-1]
    baseline = window[-12:-1] if len(window) >= 12 else window[:-1]
    avg_range = mean(max(c.range, 1e-12) for c in baseline)
    recent = window[-6:]
    net_move = recent[-1].close - recent[0].open

    swings = detect_swings(window)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    structure_direction = "neutral"
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
            structure_direction = "bullish"
        elif highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
            structure_direction = "bearish"

    previous = window[:-1]
    displacement = detect_displacement(window)
    prior_swings = detect_swings(previous)
    prior_high = next((s for s in reversed(prior_swings) if s.kind == "high"), None)
    prior_low = next((s for s in reversed(prior_swings) if s.kind == "low"), None)

    reversal_direction = None
    if displacement is not None:
        if (
            displacement.direction == "bearish"
            and prior_low is not None
            and latest.close < prior_low.price
            and structure_direction != "bearish"
        ):
            reversal_direction = "bearish"
        elif (
            displacement.direction == "bullish"
            and prior_high is not None
            and latest.close > prior_high.price
            and structure_direction != "bullish"
        ):
            reversal_direction = "bullish"

    if reversal_direction:
        return {
            "regime": "REVERSAL_DEVELOPING",
            "direction": reversal_direction,
            "confidence": 88,
            "structure_direction": structure_direction,
            "avg_range": avg_range,
            "net_move": net_move,
        }

    prior_high_price = max(c.high for c in previous[-8:])
    prior_low_price = min(c.low for c in previous[-8:])
    expansion = latest.range >= avg_range * 1.65
    if expansion and latest.close > prior_high_price:
        return {
            "regime": "EXPANSION_BREAKOUT",
            "direction": "bullish",
            "confidence": 82,
            "structure_direction": structure_direction,
            "avg_range": avg_range,
            "net_move": net_move,
        }
    if expansion and latest.close < prior_low_price:
        return {
            "regime": "EXPANSION_BREAKOUT",
            "direction": "bearish",
            "confidence": 82,
            "structure_direction": structure_direction,
            "avg_range": avg_range,
            "net_move": net_move,
        }

    if structure_direction == "bullish":
        return {
            "regime": "TRENDING_UP",
            "direction": "bullish",
            "confidence": 80,
            "structure_direction": structure_direction,
            "avg_range": avg_range,
            "net_move": net_move,
        }
    if structure_direction == "bearish":
        return {
            "regime": "TRENDING_DOWN",
            "direction": "bearish",
            "confidence": 80,
            "structure_direction": structure_direction,
            "avg_range": avg_range,
            "net_move": net_move,
        }

    if abs(net_move) <= avg_range * 1.10:
        regime = "RANGE_CHOP"
        direction = "neutral"
        confidence = 72
    else:
        regime = "DRIFT"
        direction = "bullish" if net_move > 0 else "bearish"
        confidence = 62

    return {
        "regime": regime,
        "direction": direction,
        "confidence": confidence,
        "structure_direction": structure_direction,
        "avg_range": avg_range,
        "net_move": net_move,
    }
