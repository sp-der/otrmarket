from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.strategies.confluence import ConfluenceEngine, PendingContext
from src.strategies.models import Candle, FairValueGap, StrategySetup
from src.strategies.structure import detect_swings, nearest_target_swing


class FlexibleConfluenceEngine(ConfluenceEngine):
    """ICT confluence with flexible entries and adaptive setup lifetimes.

    Operation 6.0 keeps the original PD array -> signal -> displacement ->
    qualifying FVG -> 50-79% retracement sequence, but no longer gives every
    stage the same short lifespan. One-minute continuation ideas get more time
    to mature after displacement, while five-minute structure can persist long
    enough to provide a higher-timeframe narrative for a fresh one-minute
    trigger. Late entries are still allowed, but they are automatically sized
    more conservatively and prefer the cleanest available geometry.
    """

    _ONE_MINUTE_LIMITS = {
        "WAIT_SIGNAL": 24,
        "WAIT_DISPLACEMENT": 15,
        "WAIT_ENTRY_FVG": 25,
        "WAIT_QUALIFYING_FVG": 25,
        "WAIT_VALID_RR": 30,
    }
    _FIVE_MINUTE_LIMITS = {
        "WAIT_SIGNAL": 18,
        "WAIT_DISPLACEMENT": 15,
        "WAIT_ENTRY_FVG": 18,
        "WAIT_QUALIFYING_FVG": 18,
        "WAIT_VALID_RR": 18,
    }

    def __init__(self, *args, min_rr: float = 1.0, **kwargs) -> None:
        super().__init__(*args, min_rr=min_rr, **kwargs)

    def _adaptive_stage_limit(self, context: PendingContext) -> int:
        if context.timeframe == "1m":
            return self._ONE_MINUTE_LIMITS.get(
                context.stage,
                self.stage_expiry_bars.get(context.stage, 16),
            )
        if context.timeframe == "5m":
            return self._FIVE_MINUTE_LIMITS.get(
                context.stage,
                self.stage_expiry_bars.get(context.stage, 16),
            )
        return self.stage_expiry_bars.get(context.stage, 16)

    def _stage_expired(
        self,
        context: PendingContext,
        current_bar_count: int,
    ) -> tuple[bool, int, int]:
        """Use timeframe/stage-aware lifetimes instead of one flat timer."""
        limit = self._adaptive_stage_limit(context)
        elapsed = max(0, current_bar_count - context.stage_bar_count)
        return elapsed > limit, elapsed, limit

    @staticmethod
    def _lifetime_profile(
        context: PendingContext,
        current_bar_count: int,
    ) -> tuple[str, float, int]:
        """Return setup-age tier, max risk multiplier and age in bars.

        The post-displacement timer is intentionally preserved across FVG and
        R:R retries, so age measures how long price has taken to offer a usable
        entry instead of resetting every time a candidate appears.
        """
        age = max(0, current_bar_count - context.stage_bar_count)
        if context.timeframe == "1m":
            if age <= 15:
                return "NORMAL", 1.00, age
            if age <= 25:
                return "MATURE", 0.75, age
            return "LATE", 0.50, age
        if context.timeframe == "5m":
            if age <= 12:
                return "NORMAL", 1.00, age
            return "MATURE", 0.75, age
        return "NORMAL", 1.00, age

    @staticmethod
    def _structural_invalidation_reason(
        context: PendingContext,
        latest: Candle,
    ) -> str | None:
        """Kill an old idea when confirmed displacement is fully negated.

        Time alone should not erase a valid narrative, but a close through the
        far side of the displacement candle is a concrete structural reason to
        stop carrying the setup forward and wait for a fresh sequence.
        """
        displacement = context.displacement
        if displacement is None:
            return None
        if context.direction == "bullish" and latest.close < displacement.low:
            return (
                "Bullish displacement was structurally negated by a close "
                f"below {displacement.low:.2f}."
            )
        if context.direction == "bearish" and latest.close > displacement.high:
            return (
                "Bearish displacement was structurally negated by a close "
                f"above {displacement.high:.2f}."
            )
        return None

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
        """Conservative sizing; never exceeds the evaluation guard's cap."""
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

        lifetime_tier, lifetime_risk_cap, setup_age_bars = self._lifetime_profile(
            context,
            len(candles),
        )
        valid_attempts = [attempt for attempt in attempts if attempt["valid"]]

        # During the normal window preserve entry priority. Once a setup has
        # matured, prefer the strongest available geometry instead of blindly
        # accepting the first valid candidate. This mimics zooming out and
        # waiting for the cleaner entry while still respecting the same thesis.
        if lifetime_tier == "NORMAL":
            chosen = valid_attempts[0] if valid_attempts else None
        else:
            chosen = max(
                valid_attempts,
                key=lambda attempt: float(attempt.get("risk_reward") or 0.0),
                default=None,
            )

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
        multiplier = min(multiplier, lifetime_risk_cap)
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
                "lifetime_profile": "ADAPTIVE_6_0",
                "lifetime_tier": lifetime_tier,
                "lifetime_risk_cap": lifetime_risk_cap,
                "setup_age_bars": setup_age_bars,
                "operation": 6.0,
            },
        )

    def on_candle(self, symbol: str, timeframe: str, histories):
        candles = histories.get((symbol, timeframe), [])
        context = self.contexts.get((symbol, timeframe))
        if context is not None and candles:
            invalidation = self._structural_invalidation_reason(context, candles[-1])
            if invalidation:
                self.contexts.pop((symbol, timeframe), None)
                self._log(
                    f"{symbol} {timeframe}: structurally invalidated - {invalidation}",
                    candles[-1].close_time,
                )
                self._set_diag(
                    symbol,
                    timeframe,
                    candles[-1].close_time,
                    stage="INVALIDATED",
                    direction=context.direction,
                    pd_array=True,
                    signal=context.trigger_type is not None,
                    displacement=context.displacement is not None,
                    entry_fvg=context.entry_fvg_seen,
                    retracement=context.retracement_seen,
                    trigger_type=context.trigger_type,
                    note=invalidation + " Waiting for a fresh sequence.",
                )
                return None

        setup = super().on_candle(symbol, timeframe, histories)
        if setup:
            diag = self.diagnostics.get((symbol, timeframe))
            if diag is not None:
                entry_type = setup.metadata.get("entry_type", "FVG_MIDPOINT")
                multiplier = float(setup.metadata.get("risk_multiplier", 1.0))
                lifetime_tier = setup.metadata.get("lifetime_tier", "NORMAL")
                age = int(setup.metadata.get("setup_age_bars", 0) or 0)
                diag["entry_type"] = entry_type
                diag["risk_multiplier"] = multiplier
                diag["note"] = (
                    f"Valid A+ setup via {entry_type.replace('_', ' ')} at "
                    f"{setup.risk_reward:.2f}R; {lifetime_tier.lower()} entry age "
                    f"{age} bars, max risk tier {multiplier:.0%}."
                )
        return setup
