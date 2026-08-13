from __future__ import annotations

from statistics import mean

from src.strategies.models import (
    Candle,
    Displacement,
    FairValueGap,
    LiquiditySweep,
    SMTDivergence,
)
from src.strategies.structure import detect_swings


def detect_fvg(candles: list[Candle]) -> FairValueGap | None:
    if len(candles) < 3:
        return None

    c1, _, c3 = candles[-3:]

    if c1.high < c3.low:
        return FairValueGap(
            symbol=c3.symbol,
            timeframe=c3.timeframe,
            direction="bullish",
            lower=c1.high,
            upper=c3.low,
            formed_at=c3.close_time,
            candle1_time=c1.close_time,
            candle3_time=c3.close_time,
        )

    if c1.low > c3.high:
        return FairValueGap(
            symbol=c3.symbol,
            timeframe=c3.timeframe,
            direction="bearish",
            lower=c3.high,
            upper=c1.low,
            formed_at=c3.close_time,
            candle1_time=c1.close_time,
            candle3_time=c3.close_time,
        )

    return None


def find_recent_fvgs(candles: list[Candle], lookback: int = 100) -> list[FairValueGap]:
    start = max(0, len(candles) - lookback)
    results: list[FairValueGap] = []
    for idx in range(start + 2, len(candles)):
        fvg = detect_fvg(candles[: idx + 1])
        if fvg:
            results.append(fvg)
    return results


def find_pd_array_touch(candles: list[Candle], max_age_bars: int = 80) -> FairValueGap | None:
    if len(candles) < 4:
        return None

    latest = candles[-1]
    earlier = candles[:-1]
    fvgs = find_recent_fvgs(earlier, lookback=max_age_bars + 3)

    for fvg in reversed(fvgs):
        if fvg.overlaps_range(latest.low, latest.high):
            return fvg

    return None


def detect_liquidity_sweep(candles: list[Candle], swing_left: int = 2, swing_right: int = 2) -> LiquiditySweep | None:
    if len(candles) < swing_left + swing_right + 2:
        return None

    latest = candles[-1]
    swings = detect_swings(candles[:-1], left=swing_left, right=swing_right)

    recent_low = next((s for s in reversed(swings) if s.kind == "low"), None)
    recent_high = next((s for s in reversed(swings) if s.kind == "high"), None)

    if recent_low and latest.low < recent_low.price and latest.close > recent_low.price:
        return LiquiditySweep(
            symbol=latest.symbol,
            timeframe=latest.timeframe,
            direction="bullish",
            swept_level=recent_low.price,
            candle_time=latest.close_time,
            swing_time=recent_low.time,
        )

    if recent_high and latest.high > recent_high.price and latest.close < recent_high.price:
        return LiquiditySweep(
            symbol=latest.symbol,
            timeframe=latest.timeframe,
            direction="bearish",
            swept_level=recent_high.price,
            candle_time=latest.close_time,
            swing_time=recent_high.time,
        )

    return None


def detect_displacement(
    candles: list[Candle],
    lookback: int = 20,
    body_multiplier: float = 1.5,
    range_multiplier: float = 1.3,
    close_extreme_fraction: float = 0.20,
) -> Displacement | None:
    if len(candles) < lookback + 1:
        return None

    latest = candles[-1]
    previous = candles[-lookback - 1 : -1]

    avg_body = mean(max(c.body, 1e-12) for c in previous)
    avg_range = mean(max(c.range, 1e-12) for c in previous)

    body_ratio = latest.body / avg_body
    range_ratio = latest.range / avg_range

    if body_ratio < body_multiplier or range_ratio < range_multiplier or latest.range <= 0:
        return None

    if latest.close > latest.open:
        close_position = (latest.high - latest.close) / latest.range
        if close_position > close_extreme_fraction:
            return None
        direction = "bullish"
    elif latest.close < latest.open:
        close_position = (latest.close - latest.low) / latest.range
        if close_position > close_extreme_fraction:
            return None
        direction = "bearish"
    else:
        return None

    return Displacement(
        symbol=latest.symbol,
        timeframe=latest.timeframe,
        direction=direction,
        candle_time=latest.close_time,
        low=latest.low,
        high=latest.high,
        body_ratio=body_ratio,
        range_ratio=range_ratio,
    )


def detect_smt(
    leader: list[Candle],
    laggard: list[Candle],
    lookback: int = 8,
) -> SMTDivergence | None:
    if len(leader) < lookback + 1 or len(laggard) < lookback + 1:
        return None

    a = leader[-1]
    b = laggard[-1]

    # Require roughly synchronized candles.
    if abs((a.close_time - b.close_time).total_seconds()) > 1:
        return None

    a_prev = leader[-lookback - 1 : -1]
    b_prev = laggard[-lookback - 1 : -1]

    a_prev_high = max(c.high for c in a_prev)
    a_prev_low = min(c.low for c in a_prev)
    b_prev_high = max(c.high for c in b_prev)
    b_prev_low = min(c.low for c in b_prev)

    if a.high > a_prev_high and b.high <= b_prev_high:
        return SMTDivergence(
            timeframe=a.timeframe,
            direction="bearish",
            leader=a.symbol,
            laggard=b.symbol,
            event_time=a.close_time,
            leader_level=a.high,
            laggard_level=b.high,
            description=f"{a.symbol} made a relative higher high while {b.symbol} did not.",
        )

    if a.low < a_prev_low and b.low >= b_prev_low:
        return SMTDivergence(
            timeframe=a.timeframe,
            direction="bullish",
            leader=a.symbol,
            laggard=b.symbol,
            event_time=a.close_time,
            leader_level=a.low,
            laggard_level=b.low,
            description=f"{a.symbol} made a relative lower low while {b.symbol} did not.",
        )

    return None


def fvg_in_retracement_zone(
    fvg: FairValueGap,
    displacement: Displacement,
    min_retrace: float = 0.50,
    max_retrace: float = 0.79,
) -> bool:
    move_range = displacement.high - displacement.low
    if move_range <= 0:
        return False

    if displacement.direction == "bullish":
        zone_low = displacement.high - (max_retrace * move_range)
        zone_high = displacement.high - (min_retrace * move_range)
    else:
        zone_low = displacement.low + (min_retrace * move_range)
        zone_high = displacement.low + (max_retrace * move_range)

    return fvg.upper >= zone_low and fvg.lower <= zone_high
