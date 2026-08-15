from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP


TICK_SIZES: dict[str, float] = {
    "NQ": 0.25,
    "ES": 0.25,
    "GC": 0.10,
    "BTC-USD": 0.01,
}


@dataclass(frozen=True)
class PriceGeometry:
    valid: bool
    direction: str
    entry: float
    stop: float
    target: float
    risk: float
    reward: float
    risk_reward: float | None
    reason: str = ""


def tick_size_for(symbol: str) -> float:
    return TICK_SIZES.get(symbol, 0.01)


def _round_to_tick(value: float, tick: float, rounding) -> float:
    if tick <= 0:
        return float(value)
    q = Decimal(str(value)) / Decimal(str(tick))
    rounded = q.quantize(Decimal("1"), rounding=rounding) * Decimal(str(tick))
    return float(rounded)


def normalize_trade_prices(symbol: str, direction: str, entry: float, stop: float, target: float) -> tuple[float, float, float]:
    """Round planned futures prices to executable tick increments.

    Entry uses nearest tick. Stops/targets round away from the entry so rounding
    never accidentally turns valid geometry into zero/negative risk.
    """
    tick = tick_size_for(symbol)
    entry_tick = _round_to_tick(entry, tick, ROUND_HALF_UP)
    if direction == "bullish":
        stop_tick = _round_to_tick(stop, tick, ROUND_FLOOR)
        target_tick = _round_to_tick(target, tick, ROUND_CEILING)
    else:
        stop_tick = _round_to_tick(stop, tick, ROUND_CEILING)
        target_tick = _round_to_tick(target, tick, ROUND_FLOOR)
    return entry_tick, stop_tick, target_tick


def validate_trade_geometry(symbol: str, direction: str, entry: float, stop: float, target: float) -> PriceGeometry:
    risk = (entry - stop) if direction == "bullish" else (stop - entry)
    reward = (target - entry) if direction == "bullish" else (entry - target)

    if direction not in {"bullish", "bearish"}:
        return PriceGeometry(False, direction, entry, stop, target, risk, reward, None, f"Unknown direction: {direction}")

    if direction == "bullish" and not (stop < entry < target):
        return PriceGeometry(
            False, direction, entry, stop, target, risk, reward, None,
            f"Invalid bullish geometry: require stop < entry < target ({stop:.2f} < {entry:.2f} < {target:.2f}).",
        )
    if direction == "bearish" and not (target < entry < stop):
        return PriceGeometry(
            False, direction, entry, stop, target, risk, reward, None,
            f"Invalid bearish geometry: require target < entry < stop ({target:.2f} < {entry:.2f} < {stop:.2f}).",
        )
    if risk <= 0:
        return PriceGeometry(False, direction, entry, stop, target, risk, reward, None, "Risk distance must be positive.")
    if reward <= 0:
        return PriceGeometry(False, direction, entry, stop, target, risk, reward, None, "Reward distance must be positive.")

    tick = tick_size_for(symbol)
    for label, value in (("entry", entry), ("stop", stop), ("target", target)):
        units = value / tick
        if abs(units - round(units)) > 1e-7:
            return PriceGeometry(False, direction, entry, stop, target, risk, reward, None, f"{label.title()} {value:.4f} is not aligned to {tick:g} tick size.")

    rr = reward / risk
    return PriceGeometry(True, direction, entry, stop, target, risk, reward, rr, "")
