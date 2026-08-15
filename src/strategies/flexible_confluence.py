from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.strategies.confluence import ConfluenceEngine, PendingContext
from src.strategies.models import Candle, FairValueGap, StrategySetup
from src.strategies.structure import detect_swings, nearest_target_swing


class FlexibleConfluenceEngine(ConfluenceEngine):
    """Preserve the existing six-stage ICT state machine and widen only entry choice.

    A setup still has to pass the original PD array -> signal -> displacement ->
    qualifying FVG -> 50-79% retracement sequence. Once it reaches the final
    geometry check, OTR evaluates the original FVG midpoint first, then a 79%
    OTE entry, then a same-leg order-block mean-threshold entry. The first
    structurally valid candidate offering at least ``min_rr`` is used.
    """

    def __init__(self, *args, min_rr: float = 1.0, **kwargs) -> None:
        super().__init__(*args, min_rr=min_rr, **kwargs)

    @staticmethod
    def _retracement_zone(context: PendingContext) -> tuple[float, float] | None:
        displacement = context.displacement
        if displacement is None:
            return None
        move_range = displacement.high - displacement.low
        if move_range <= 0:
            return None
        if context.direction == "bullish":
            return (
                displacement.high - (0.79 * move_range),
                displacement.high - (0.50 * move_range),
            )
        return (
            displacement.low + (0.50 * move_range),
            displacement.low + (0.79 * move_range),
        )

    @staticmethod
    def _ote_79_price(context: PendingContext) -> float | None:
        displacement = context.displacement
        if displacement is None:
            return None
        move_range = displacement.high - displacement.low
        if move_range <= 0:
            return None
        if context.direction == "bullish":
            return displacement.high - (0.79 * move_range)
        return displacement.low + (0.79 * move_range)

    @classmethod
    def _order_block_candidate(
        cls,
        candles: list[Candle],
        context: PendingContext,
        lookback: int = 8,
    ) -> tuple[float, dict] | None:
        """Return a same-leg order-block mean threshold inside the 50-79% zone.

        The block is deliberately not a new strategy trigger. It is only the
        most recent opposing candle immediately before the already-confirmed
        displacement, and its body must overlap that displacement's OTE zone.
        """
        displacement = context.displacement
        zone = cls._retracement_zone(context)
        if displacement is None or zone is None:
            return None

        displacement_index = next(
            (
                idx
                for idx in range(len(candles) - 1, -1, -1)
                if candles[idx].close_time == displacement.candle_time
            ),
            None,
        )
        if displacement_index is None or displacement_index <= 0:
            return None

        zone_low, zone_high = zone
        start = max(0, displacement_index - lookback)
        for candle in reversed(candles[start:displacement_index]):
            opposing = (
                candle.close < candle.open
                if context.direction == "bullish"
                else candle.close > candle.open
            )
            if not opposing:
                continue

            body_low = min(candle.open, candle.close)
            body_high = max(candle.open, candle.close)
            overlap_low = max(body_low, zone_low)
            overlap_high = min(body_high, zone_high)
            if overlap_low > overlap_high:
                continue

            # Mean threshold of the portion of the order-block body that sits
            # inside the same displacement OTE zone.
            entry = (overlap_low + overlap_high) / 2.0
            return entry, {
                "candle_time": candle.close_time.isoformat(),
                "body_low": body_low,
                "body_high": body_high,
                "zone_overlap_low": overlap_low,
                "zone_overlap_high": overlap_high,
            }
        return None

    @staticmethod
    def _risk_tier(rr: float) -> tuple[str, float]:
        """Conservative replay sizing; never exceeds the guard's risk cap."""
        if rr >= 3.0:
            return "RR_3_PLUS", 1.00
        if rr >= 2.0:
            return "RR_2_TO_3", 0.80
        if rr >= 1.5:
            return "RR_1_5_TO_2", 0.65
        return "RR_1_TO_1_5", 0.50

    def _entry_candidates(
        self,
        candles: list[Candle],
        context: PendingContext,
        entry_fvg: FairValueGap,
    ) -> list[tuple[str, float, dict]]:
        candidates: list[tuple[str, float, dict]] = [
            (
                "FVG_MIDPOINT",
                entry_fvg.midpoint,
                {
                    "fvg_lower": entry_fvg.lower,
                    "fvg_upper": entry_fvg.upper,
                },
            )
        ]

        ote = self._ote_79_price(context)
        if ote is not None:
            candidates.append(("OTE_79", ote, {"retracement": 0.79}))

        order_block = self._order_block_candidate(candles, context)
        if order_block is not None:
            price, details = order_block
            candidates.append(("ORDER_BLOCK", price, details))

        return candidates

    def _evaluate_entry_candidate(
        self,
        candles: list[Candle],
        context: PendingContext,
        entry_type: str,
        raw_entry: float,
        details: dict,
    ) -> dict:
        result = {
            "entry_type": entry_type,
            "raw_entry": raw_entry,
            "valid": False,
            "details": details,
        }
        swings = detect_swings(candles)

        if context.direction == "bullish":
            low_swings = [s for s in swings if s.kind == "low" and s.price < raw_entry]
            if not low_swings:
                result["reason"] = "No valid swing low exists below entry for the stop."
                return result
            if context.swept_level is not None and context.swept_level < raw_entry:
                stop_anchor = context.swept_level
            else:
                stop_anchor = low_swings[-1].price
            raw_stop = stop_anchor * (1 - self.stop_buffer_fraction)
        else:
            high_swings = [s for s in swings if s.kind == "high" and s.price > raw_entry]
            if not high_swings:
                result["reason"] = "No valid swing high exists above entry for the stop."
                return result
            if context.swept_level is not None and context.swept_level > raw_entry:
                stop_anchor = context.swept_level
            else:
                stop_anchor = high_swings[-1].price
            raw_stop = stop_anchor * (1 + self.stop_buffer_fraction)

        target_swing = nearest_target_swing(candles, context.direction, raw_entry)
        if not target_swing:
            result["reason"] = "No valid opposing swing target exists beyond entry."
            return result
        raw_target = target_swing.price

        entry, stop, target = normalize_trade_prices(
            context.symbol,
            context.direction,
            raw_entry,
            raw_stop,
            raw_target,
        )
        geometry = validate_trade_geometry(
            context.symbol,
            context.direction,
            entry,
            stop,
            target,
        )
        result.update(
            {
                "entry": entry,
                "stop": stop,
                "target": target,
            }
        )
        if not geometry.valid:
            result["reason"] = geometry.reason
            return result

        rr = float(geometry.risk_reward or 0.0)
        result["risk_reward"] = rr
        if rr < self.min_rr:
            result["reason"] = (
                f"Risk/reward {rr:.2f}R is below minimum {self.min_rr:.2f}R."
            )
            return result

        result["valid"] = True
        result["reason"] = "Valid structural entry."
        return result

    def _build_setup(
        self,
        candles,
        context: PendingContext,
        entry_fvg: FairValueGap,
        market_time: datetime,
    ):
        key = (context.symbol, context.timeframe)
        self.risk_rejections.pop(key, None)

        attempts = [
            self._evaluate_entry_candidate(
                candles,
                context,
                entry_type,
                raw_entry,
                details,
            )
            for entry_type, raw_entry, details in self._entry_candidates(
                candles, context, entry_fvg
            )
        ]

        # Preserve the original behavior first. Alternate entries are fallbacks,
        # not replacements: FVG midpoint -> 79% OTE -> associated order block.
        chosen = next((attempt for attempt in attempts if attempt["valid"]), None)
        if chosen is None:
            summaries = []
            for attempt in attempts:
                rr = attempt.get("risk_reward")
                rr_text = f"{rr:.2f}R" if rr is not None else "no geometry"
                summaries.append(
                    f"{attempt['entry_type']} {rr_text}: {attempt.get('reason', 'rejected')}"
                )
            return self._reject_setup(
                context,
                "No A+ entry candidate cleared the final risk check. "
                + " | ".join(summaries),
                market_time,
            )

        rr = float(chosen["risk_reward"])
        tier, multiplier = self._risk_tier(rr)
        entry_type = str(chosen["entry_type"])
        entry_rule = {
            "FVG_MIDPOINT": "qualifying FVG midpoint rounded to exchange tick",
            "OTE_79": "79% OTE of confirmed displacement rounded to exchange tick",
            "ORDER_BLOCK": "same-leg order-block mean threshold inside 50-79% zone",
        }.get(entry_type, entry_type)

        serializable_attempts = []
        for attempt in attempts:
            serializable_attempts.append(
                {
                    "entry_type": attempt["entry_type"],
                    "entry": attempt.get("entry"),
                    "stop": attempt.get("stop"),
                    "target": attempt.get("target"),
                    "risk_reward": attempt.get("risk_reward"),
                    "valid": bool(attempt.get("valid")),
                    "reason": attempt.get("reason"),
                    "details": attempt.get("details", {}),
                }
            )

        return StrategySetup(
            setup_id=uuid4().hex[:12],
            symbol=context.symbol,
            timeframe=context.timeframe,
            direction=context.direction,
            created_at=market_time,
            pd_array=context.pd_array,
            trigger_type=context.trigger_type,
            trigger_details=context.trigger_details or {},
            displacement=context.displacement,
            entry_fvg=entry_fvg,
            entry_price=float(chosen["entry"]),
            stop_price=float(chosen["stop"]),
            target_price=float(chosen["target"]),
            risk_reward=rr,
            metadata={
                "entry_rule": entry_rule,
                "entry_type": entry_type,
                "entry_priority": ["FVG_MIDPOINT", "OTE_79", "ORDER_BLOCK"],
                "entry_candidates": serializable_attempts,
                "discount_rule": "50-79% displacement retracement",
                "pd_array_type": "ACTIVE_FVG",
                "geometry_guard": "long stop<entry<target; short target<entry<stop",
                "target_rule": "nearest valid opposing structural swing",
                "min_rr": self.min_rr,
                "setup_quality": "A_PLUS_STRUCTURE",
                "risk_tier": tier,
                "risk_multiplier": multiplier,
                "operation": 4.8,
            },
        )

    def on_candle(self, symbol: str, timeframe: str, histories):
        setup = super().on_candle(symbol, timeframe, histories)
        if setup:
            diag = self.diagnostics.get((symbol, timeframe))
            if diag is not None:
                entry_type = setup.metadata.get("entry_type", "FVG_MIDPOINT")
                multiplier = float(setup.metadata.get("risk_multiplier", 1.0))
                diag["entry_type"] = entry_type
                diag["risk_multiplier"] = multiplier
                diag["note"] = (
                    f"Valid A+ setup via {entry_type.replace('_', ' ')} at "
                    f"{setup.risk_reward:.2f}R; replay risk tier {multiplier:.0%}."
                )
        return setup
