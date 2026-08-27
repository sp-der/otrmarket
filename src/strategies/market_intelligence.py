from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from zoneinfo import ZoneInfo

from src.strategies.ict import detect_smt, find_active_fvgs, find_recent_fvgs
from src.strategies.structure import detect_swings


NY = ZoneInfo("America/New_York")
PAIR = {"NQ": "ES", "ES": "NQ"}
CONTEXT_TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h")


def _history(histories, symbol: str, timeframe: str, market_time: datetime | None):
    candles = list(histories.get((symbol, timeframe), []))
    if market_time is not None:
        candles = [c for c in candles if c.close_time <= market_time]
    return candles[-240:]


def _average_range(candles) -> float:
    window = list(candles[-20:])
    if not window:
        return 0.0
    return mean(max(float(c.range), 1e-12) for c in window)


def structure_snapshot(candles) -> dict:
    candles = list(candles[-80:])
    if len(candles) < 6:
        return {"direction": "unknown", "source": "warmup", "bars": len(candles)}

    swings = detect_swings(candles)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1].price > highs[-2].price
        hl = lows[-1].price > lows[-2].price
        lh = highs[-1].price < highs[-2].price
        ll = lows[-1].price < lows[-2].price
        if hh and hl:
            direction = "bullish"
        elif lh and ll:
            direction = "bearish"
        else:
            direction = "mixed"
        return {
            "direction": direction,
            "source": "swing_structure",
            "last_high": highs[-1].price,
            "prior_high": highs[-2].price,
            "last_low": lows[-1].price,
            "prior_low": lows[-2].price,
        }

    recent = candles[-6:]
    first = mean(c.close for c in recent[:3])
    second = mean(c.close for c in recent[3:])
    threshold = _average_range(recent) * 0.15
    delta = second - first
    if delta > threshold:
        direction = "bullish"
    elif delta < -threshold:
        direction = "bearish"
    else:
        direction = "neutral"
    return {
        "direction": direction,
        "source": "close_momentum",
        "delta": delta,
        "threshold": threshold,
    }


def dealing_range_snapshot(candles) -> dict:
    candles = list(candles[-100:])
    swings = detect_swings(candles)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    if not highs or not lows or not candles:
        return {"available": False}

    high = highs[-1].price
    low = lows[-1].price
    if high <= low:
        high = max(high, max(c.high for c in candles[-30:]))
        low = min(low, min(c.low for c in candles[-30:]))
    width = high - low
    if width <= 0:
        return {"available": False}

    price = float(candles[-1].close)
    position = (price - low) / width
    if position < 0.45:
        zone = "discount"
    elif position > 0.55:
        zone = "premium"
    else:
        zone = "equilibrium"
    return {
        "available": True,
        "low": low,
        "high": high,
        "midpoint": (low + high) / 2.0,
        "position": round(position, 4),
        "zone": zone,
    }


def detect_equal_liquidity(candles) -> dict:
    candles = list(candles[-120:])
    swings = detect_swings(candles)
    tolerance = max(_average_range(candles) * 0.12, 1e-9)
    result = {"tolerance": tolerance, "equal_highs": [], "equal_lows": []}

    for kind, key in (("high", "equal_highs"), ("low", "equal_lows")):
        points = [s for s in swings if s.kind == kind][-10:]
        used: set[int] = set()
        clusters = []
        for i, first in enumerate(points):
            if i in used:
                continue
            cluster = [first]
            for j in range(i + 1, len(points)):
                if j in used:
                    continue
                if abs(points[j].price - first.price) <= tolerance:
                    cluster.append(points[j])
                    used.add(j)
            if len(cluster) >= 2:
                used.add(i)
                clusters.append(
                    {
                        "price": round(mean(p.price for p in cluster), 6),
                        "touches": len(cluster),
                        "first_time": cluster[0].time.isoformat(),
                        "last_time": cluster[-1].time.isoformat(),
                    }
                )
        result[key] = clusters[-4:]
    return result


def rejection_snapshot(candles) -> dict:
    if not candles:
        return {"signal": None}
    candle = candles[-1]
    if candle.range <= 0:
        return {"signal": None}
    body_low = min(candle.open, candle.close)
    body_high = max(candle.open, candle.close)
    lower = max(0.0, body_low - candle.low) / candle.range
    upper = max(0.0, candle.high - body_high) / candle.range
    close_position = (candle.close - candle.low) / candle.range
    signal = None
    if lower >= 0.35 and close_position >= 0.60:
        signal = "bullish"
    elif upper >= 0.35 and close_position <= 0.40:
        signal = "bearish"
    return {
        "signal": signal,
        "lower_wick_fraction": round(lower, 4),
        "upper_wick_fraction": round(upper, 4),
        "close_position": round(close_position, 4),
    }


def _fvg_dict(fvg, current_price: float | None = None) -> dict:
    item = {
        "direction": fvg.direction,
        "lower": fvg.lower,
        "upper": fvg.upper,
        "midpoint": fvg.midpoint,
        "formed_at": fvg.formed_at.isoformat(),
    }
    if current_price is not None:
        if current_price < fvg.lower:
            distance = fvg.lower - current_price
        elif current_price > fvg.upper:
            distance = current_price - fvg.upper
        else:
            distance = 0.0
        item["distance"] = distance
    return item


def fvg_snapshot(candles) -> dict:
    candles = list(candles[-140:])
    if len(candles) < 3:
        return {"active": [], "inverse": []}
    current_price = float(candles[-1].close)
    recent = find_recent_fvgs(candles, lookback=100)
    active = find_active_fvgs(candles, lookback=100)

    inverse = []
    by_time = {c.close_time: i for i, c in enumerate(candles)}
    for fvg in recent[-20:]:
        formed_index = by_time.get(fvg.formed_at)
        if formed_index is None:
            continue
        later = candles[formed_index + 1 :]
        if fvg.direction == "bullish":
            broken_index = next((i for i, c in enumerate(later) if c.close < fvg.lower), None)
            inverse_direction = "bearish"
        else:
            broken_index = next((i for i, c in enumerate(later) if c.close > fvg.upper), None)
            inverse_direction = "bullish"
        if broken_index is None:
            continue
        after_break = later[broken_index + 1 :]
        retested = any(c.high >= fvg.lower and c.low <= fvg.upper for c in after_break)
        inverse.append(
            {
                "direction": inverse_direction,
                "lower": fvg.lower,
                "upper": fvg.upper,
                "source_direction": fvg.direction,
                "formed_at": fvg.formed_at.isoformat(),
                "retested": retested,
            }
        )

    active_items = sorted(
        (_fvg_dict(fvg, current_price) for fvg in active[-12:]),
        key=lambda item: item.get("distance", 0.0),
    )
    return {"active": active_items[:6], "inverse": inverse[-6:]}


def order_block_snapshot(candles) -> dict:
    candles = list(candles[-100:])
    if len(candles) < 22:
        return {"active": [], "breaker_candidates": []}

    blocks = []
    for idx in range(20, len(candles)):
        current = candles[idx]
        previous = candles[max(0, idx - 20) : idx]
        avg_body = mean(max(c.body, 1e-12) for c in previous)
        avg_range = mean(max(c.range, 1e-12) for c in previous)
        body_ratio = current.body / avg_body
        range_ratio = current.range / avg_range
        if body_ratio < 1.65 or range_ratio < 1.35:
            continue
        direction = "bullish" if current.close > current.open else "bearish" if current.close < current.open else None
        if direction is None:
            continue
        opposing = None
        for candidate in reversed(candles[max(0, idx - 8) : idx]):
            if direction == "bullish" and candidate.close < candidate.open:
                opposing = candidate
                break
            if direction == "bearish" and candidate.close > candidate.open:
                opposing = candidate
                break
        if opposing is None:
            continue
        body_low = min(opposing.open, opposing.close)
        body_high = max(opposing.open, opposing.close)
        later = candles[idx + 1 :]
        if direction == "bullish":
            invalidated = any(c.close < opposing.low for c in later)
        else:
            invalidated = any(c.close > opposing.high for c in later)
        retested = any(c.high >= body_low and c.low <= body_high for c in later)
        blocks.append(
            {
                "direction": direction,
                "body_low": body_low,
                "body_high": body_high,
                "candle_low": opposing.low,
                "candle_high": opposing.high,
                "formed_before": current.close_time.isoformat(),
                "retested": retested,
                "invalidated": invalidated,
                "body_ratio": round(body_ratio, 3),
                "range_ratio": round(range_ratio, 3),
            }
        )

    deduped = []
    seen = set()
    for block in reversed(blocks):
        key = (block["direction"], round(block["body_low"], 6), round(block["body_high"], 6))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(block)
    deduped.reverse()

    active = [b for b in deduped if not b["invalidated"]][-6:]
    breakers = []
    for block in deduped:
        if not block["invalidated"]:
            continue
        breakers.append(
            {
                **block,
                "direction": "bearish" if block["direction"] == "bullish" else "bullish",
                "source_order_block_direction": block["direction"],
            }
        )
    return {"active": active, "breaker_candidates": breakers[-6:]}


def session_liquidity_snapshot(candles, market_time: datetime | None = None) -> dict:
    candles = list(candles[-600:])
    if not candles:
        return {}
    market_time = market_time or candles[-1].close_time
    if market_time.tzinfo is None:
        market_time = market_time.replace(tzinfo=timezone.utc)
    local_now = market_time.astimezone(NY)

    dated = {}
    for candle in candles:
        value = candle.close_time
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        day = value.astimezone(NY).date()
        if day >= local_now.date():
            continue
        dated.setdefault(day, []).append(candle)
    previous_days = sorted(dated)
    previous = dated[previous_days[-1]] if previous_days else []

    result = {
        "local_time": local_now.strftime("%Y-%m-%d %H:%M"),
        "previous_day_high": max((c.high for c in previous), default=None),
        "previous_day_low": min((c.low for c in previous), default=None),
    }

    clock = local_now.time().replace(tzinfo=None)
    if clock.hour >= 18:
        session = "ASIA"
    elif clock.hour < 2:
        session = "TOKYO"
    elif clock.hour < 8:
        session = "LONDON"
    elif clock.hour < 12:
        session = "NEW_YORK_AM"
    elif clock.hour < 16 or (clock.hour == 16 and clock.minute < 30):
        session = "NEW_YORK_PM"
    else:
        session = "MAINTENANCE"
    result["session"] = session
    return result


def build_market_map(symbol: str, execution_timeframe: str, histories, market_time: datetime | None = None) -> dict:
    if market_time is None:
        base = histories.get((symbol, execution_timeframe), [])
        market_time = base[-1].close_time if base else None

    timeframes = {}
    for timeframe in CONTEXT_TIMEFRAMES:
        candles = _history(histories, symbol, timeframe, market_time)
        if not candles:
            continue
        snapshot = {
            "bars": len(candles),
            "last_close": candles[-1].close,
            "structure": structure_snapshot(candles),
            "dealing_range": dealing_range_snapshot(candles),
        }
        if timeframe in {execution_timeframe, "5m", "15m"}:
            snapshot["equal_liquidity"] = detect_equal_liquidity(candles)
            snapshot["rejection"] = rejection_snapshot(candles)
            snapshot["fvgs"] = fvg_snapshot(candles)
            snapshot["order_blocks"] = order_block_snapshot(candles)
        timeframes[timeframe] = snapshot

    pair_smt = None
    pair = PAIR.get(symbol)
    if pair:
        own = _history(histories, symbol, execution_timeframe, market_time)
        other = _history(histories, pair, execution_timeframe, market_time)
        smt = detect_smt(own, other) if own and other else None
        if smt:
            pair_smt = {
                "direction": smt.direction,
                "leader": smt.leader,
                "laggard": smt.laggard,
                "event_time": smt.event_time.isoformat(),
                "description": smt.description,
            }

    session_source = _history(histories, symbol, "1m", market_time)
    if not session_source:
        session_source = _history(histories, symbol, execution_timeframe, market_time)

    return {
        "profile": "MARKET_INTELLIGENCE_1_0",
        "symbol": symbol,
        "execution_timeframe": execution_timeframe,
        "market_time": market_time.isoformat() if market_time else None,
        "timeframes": timeframes,
        "pair_smt": pair_smt,
        "session_liquidity": session_liquidity_snapshot(session_source, market_time),
    }


def evaluate_market_narrative(setup, histories) -> dict:
    market_map = build_market_map(setup.symbol, setup.timeframe, histories, setup.created_at)
    direction = str(setup.direction)
    components = {}

    structural_votes = []
    for timeframe in ("5m", "15m", "30m", "1h"):
        structure = market_map.get("timeframes", {}).get(timeframe, {}).get("structure", {})
        vote = structure.get("direction")
        if vote in {"bullish", "bearish"}:
            structural_votes.append(vote)
    aligned = sum(vote == direction for vote in structural_votes)
    opposed = sum(vote != direction for vote in structural_votes)
    if structural_votes:
        components["multi_timeframe_structure"] = round(25 * aligned / len(structural_votes))
    else:
        components["multi_timeframe_structure"] = 10

    execution = market_map.get("timeframes", {}).get(setup.timeframe, {})
    dealing = execution.get("dealing_range", {})
    zone = dealing.get("zone")
    favorable = (direction == "bullish" and zone == "discount") or (direction == "bearish" and zone == "premium")
    if favorable:
        components["dealing_range_location"] = 15
    elif zone == "equilibrium":
        components["dealing_range_location"] = 10
    elif zone in {"discount", "premium"}:
        components["dealing_range_location"] = 4
    else:
        components["dealing_range_location"] = 7

    equal = execution.get("equal_liquidity", {})
    target_key = "equal_highs" if direction == "bullish" else "equal_lows"
    components["liquidity_draw"] = 15 if equal.get(target_key) else 8

    fvg_state = execution.get("fvgs", {})
    ob_state = execution.get("order_blocks", {})
    same_fvg = any(item.get("direction") == direction for item in fvg_state.get("active", []))
    same_ob = any(item.get("direction") == direction for item in ob_state.get("active", []))
    inverse = any(item.get("direction") == direction and item.get("retested") for item in fvg_state.get("inverse", []))
    components["pd_array_confluence"] = 15 if sum((same_fvg, same_ob, inverse)) >= 2 else 11 if any((same_fvg, same_ob, inverse)) else 5

    smt = market_map.get("pair_smt")
    if setup.symbol in PAIR:
        components["correlation"] = 10 if smt and smt.get("direction") == direction else 5
    else:
        components["correlation"] = 7

    rejection = execution.get("rejection", {}).get("signal")
    components["rejection_behavior"] = 10 if rejection == direction else 5 if rejection is None else 1

    session = market_map.get("session_liquidity", {}).get("session")
    components["session_context"] = 10 if session not in {None, "MAINTENANCE"} else 0

    score = max(0, min(100, int(sum(components.values()))))
    if score >= 90:
        grade = "A+"
    elif score >= 80:
        grade = "A"
    elif score >= 70:
        grade = "B+"
    elif score >= 60:
        grade = "B"
    else:
        grade = "RESEARCH"

    return {
        "profile": "MARKET_NARRATIVE_1_0",
        "score": score,
        "grade": grade,
        "components": components,
        "structural_votes": structural_votes,
        "aligned_votes": aligned,
        "opposed_votes": opposed,
        "market_map": market_map,
    }
