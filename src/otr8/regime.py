from __future__ import annotations

from statistics import mean

from src.strategies.market_intelligence import structure_snapshot
from src.strategies.regime import classify_regime

from .models import RegimeSnapshot80


class GoldRegimeEngine80:
    """Deterministic Gold regime classifier using only information available now.

    The classifier is intentionally simple and auditable. It combines the legacy
    regime detector with directional efficiency, candle overlap/alternation and
    15m/1h/4h structure. It is context for ranking, never an execution bypass.
    """

    CONTEXT_ORDER = ("4h", "1h", "15m")

    @staticmethod
    def _history(histories, symbol: str, timeframe: str, market_time=None):
        candles = list(histories.get((symbol, timeframe), []))
        if market_time is not None:
            candles = [c for c in candles if c.close_time <= market_time]
        return candles[-80:]

    @staticmethod
    def _efficiency(candles) -> float:
        window = list(candles[-12:])
        if len(window) < 2:
            return 0.0
        net = abs(float(window[-1].close) - float(window[0].open))
        path = sum(abs(float(c.close) - float(c.open)) for c in window)
        return min(1.0, net / max(path, 1e-12))

    @staticmethod
    def _overlap(candles) -> float:
        window = list(candles[-12:])
        if len(window) < 2:
            return 0.0
        overlaps = 0
        for first, second in zip(window, window[1:]):
            if min(float(first.high), float(second.high)) >= max(float(first.low), float(second.low)):
                overlaps += 1
        return overlaps / max(1, len(window) - 1)

    @staticmethod
    def _alternation(candles) -> float:
        window = [c for c in candles[-12:] if getattr(c, "direction", None) in {"bullish", "bearish"}]
        if len(window) < 2:
            return 0.0
        changes = sum(first.direction != second.direction for first, second in zip(window, window[1:]))
        return changes / max(1, len(window) - 1)

    @classmethod
    def _htf_direction(cls, histories, symbol: str, market_time=None) -> tuple[str, dict]:
        votes = []
        details = {}
        weights = {"4h": 3, "1h": 2, "15m": 1}
        for timeframe in cls.CONTEXT_ORDER:
            candles = cls._history(histories, symbol, timeframe, market_time)
            snapshot = structure_snapshot(candles) if candles else {"direction": "unknown"}
            direction = str(snapshot.get("direction") or "unknown")
            details[timeframe] = snapshot
            if direction in {"bullish", "bearish"}:
                votes.extend([direction] * weights[timeframe])
        bullish = votes.count("bullish")
        bearish = votes.count("bearish")
        if bullish > bearish:
            return "bullish", details
        if bearish > bullish:
            return "bearish", details
        return "neutral", details

    def classify(self, histories, symbol: str, timeframe: str, market_time=None) -> RegimeSnapshot80:
        candles = self._history(histories, symbol, timeframe, market_time)
        if market_time is None and candles:
            market_time = candles[-1].close_time
        legacy = classify_regime(candles)
        legacy_name = str(legacy.get("regime") or "WARMUP")
        legacy_direction = str(legacy.get("direction") or "neutral")
        htf_direction, htf_details = self._htf_direction(histories, symbol, market_time)

        if len(candles) < 8:
            return RegimeSnapshot80(
                symbol=symbol,
                timeframe=timeframe,
                regime="WARMUP",
                direction="neutral",
                confidence=0,
                directional_efficiency=0.0,
                range_expansion=0.0,
                overlap_ratio=0.0,
                alternation_ratio=0.0,
                higher_timeframe_direction=htf_direction,
                legacy_regime=legacy_name,
                details={"htf": htf_details, "bars": len(candles)},
            )

        baseline = candles[-20:-1] or candles[:-1]
        avg_range = mean(max(float(c.range), 1e-12) for c in baseline)
        latest_range = max(float(candles[-1].range), 0.0)
        expansion = latest_range / max(avg_range, 1e-12)
        efficiency = self._efficiency(candles)
        overlap = self._overlap(candles)
        alternation = self._alternation(candles)

        execution_structure = structure_snapshot(candles)
        execution_direction = str(execution_structure.get("direction") or legacy_direction)
        directional = execution_direction if execution_direction in {"bullish", "bearish"} else legacy_direction

        if expansion >= 2.10:
            regime = "VOLATILITY_EXPANSION"
            direction = directional if directional in {"bullish", "bearish"} else htf_direction
            confidence = min(95, int(76 + min(19, (expansion - 2.10) * 12)))
        elif legacy_name == "REVERSAL_DEVELOPING":
            regime = "REVERSAL_DEVELOPING"
            direction = legacy_direction
            confidence = int(legacy.get("confidence") or 82)
        elif (
            legacy_name == "EXPANSION_BREAKOUT"
            or (efficiency >= 0.48 and directional in {"bullish", "bearish"})
        ) and (htf_direction in {"neutral", directional}):
            regime = "TREND_EXPANSION"
            direction = directional
            confidence = 86 if htf_direction == directional else 78
        elif htf_direction in {"bullish", "bearish"} and execution_direction not in {htf_direction, "unknown"}:
            regime = "TREND_PULLBACK"
            direction = htf_direction
            confidence = 80 if efficiency >= 0.25 else 72
        elif overlap >= 0.68 and alternation >= 0.55:
            regime = "CHOP"
            direction = "neutral"
            confidence = 84
        elif efficiency <= 0.28 and overlap >= 0.48:
            regime = "RANGE"
            direction = "neutral"
            confidence = 76
        elif directional in {"bullish", "bearish"}:
            regime = "DRIFT"
            direction = directional
            confidence = 64
        else:
            regime = "RANGE"
            direction = "neutral"
            confidence = 62

        return RegimeSnapshot80(
            symbol=symbol,
            timeframe=timeframe,
            regime=regime,
            direction=direction,
            confidence=confidence,
            directional_efficiency=round(efficiency, 4),
            range_expansion=round(expansion, 4),
            overlap_ratio=round(overlap, 4),
            alternation_ratio=round(alternation, 4),
            higher_timeframe_direction=htf_direction,
            legacy_regime=legacy_name,
            details={
                "legacy": legacy,
                "execution_structure": execution_structure,
                "htf": htf_details,
                "avg_range": avg_range,
            },
        )
