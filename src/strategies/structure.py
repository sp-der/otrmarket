from __future__ import annotations

from src.strategies.models import Candle, SwingPoint


def detect_swings(candles: list[Candle], left: int = 2, right: int = 2) -> list[SwingPoint]:
    if len(candles) < left + right + 1:
        return []

    swings: list[SwingPoint] = []

    for i in range(left, len(candles) - right):
        center = candles[i]
        left_slice = candles[i - left : i]
        right_slice = candles[i + 1 : i + right + 1]

        if all(center.high > c.high for c in left_slice + right_slice):
            swings.append(
                SwingPoint(
                    symbol=center.symbol,
                    timeframe=center.timeframe,
                    kind="high",
                    price=center.high,
                    time=center.close_time,
                    index=i,
                )
            )

        if all(center.low < c.low for c in left_slice + right_slice):
            swings.append(
                SwingPoint(
                    symbol=center.symbol,
                    timeframe=center.timeframe,
                    kind="low",
                    price=center.low,
                    time=center.close_time,
                    index=i,
                )
            )

    return swings


def latest_swing(candles: list[Candle], kind: str, before_index: int | None = None) -> SwingPoint | None:
    swings = detect_swings(candles)
    if before_index is not None:
        swings = [s for s in swings if s.index < before_index]
    for swing in reversed(swings):
        if swing.kind == kind:
            return swing
    return None


def nearest_target_swing(candles: list[Candle], direction: str, entry_price: float) -> SwingPoint | None:
    swings = detect_swings(candles)
    if direction == "bullish":
        candidates = [s for s in swings if s.kind == "high" and s.price > entry_price]
    else:
        candidates = [s for s in swings if s.kind == "low" and s.price < entry_price]

    if not candidates:
        return None
    return candidates[-1]
