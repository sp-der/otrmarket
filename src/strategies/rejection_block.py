from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.strategies.ict import detect_displacement, detect_fvg, detect_liquidity_sweep, detect_smt
from src.strategies.models import Candle, Displacement, FairValueGap, StrategySetup
from src.strategies.structure import detect_swings, nearest_target_swing


BIAS_TIMEFRAME = {"1m": "15m", "5m": "15m", "15m": "1h", "1h": "1h"}


@dataclass
class RejectionBlockContext:
    symbol: str
    timeframe: str
    direction: str
    bias_timeframe: str
    bias_reason: str
    liquidity_type: str
    liquidity_level: float
    sweep_time: datetime
    rejection_low: float
    rejection_high: float
    invalidation_price: float
    structure_level: float
    stage: str
    stage_bar_count: int
    smt_checked: bool = False
    smt_present: bool = False
    smt_description: str = ""
    displacement: Displacement | None = None
    entry_fvg: FairValueGap | None = None
    entry_zone_low: float | None = None
    entry_zone_high: float | None = None
    entry_area_type: str | None = None
    mss_time: datetime | None = None


class RejectionBlockEngine:
    """Strict A+ rejection-block state machine. No 10/10 = no setup."""

    strategy_name = "REJECTION_BLOCK_10_10"

    def __init__(
        self,
        *,
        min_rr: float = 3.0,
        displacement_expiry_bars: int = 8,
        structure_expiry_bars: int = 18,
        retracement_expiry_bars: int = 10,
        stop_buffer_fraction: float = 0.0001,
        min_rejection_wick_fraction: float = 0.30,
    ) -> None:
        self.min_rr = min_rr
        self.stop_buffer_fraction = stop_buffer_fraction
        self.min_rejection_wick_fraction = min_rejection_wick_fraction
        self.stage_expiry_bars = {
            "WAIT_SMT": 2,
            "WAIT_DISPLACEMENT": displacement_expiry_bars,
            "WAIT_MSS_BOS": structure_expiry_bars,
            "WAIT_RETRACE": retracement_expiry_bars,
        }
        self.contexts: dict[tuple[str, str], RejectionBlockContext] = {}
        self.diagnostics: dict[tuple[str, str], dict] = {}
        self.last_setup: StrategySetup | None = None
        self.events: list[str] = []

    def clear_symbol(self, symbol: str) -> None:
        for key in [key for key in self.contexts if key[0] == symbol]:
            self.contexts.pop(key, None)
        for key in [key for key in self.diagnostics if key[0] == symbol]:
            self.diagnostics.pop(key, None)
        if self.last_setup and self.last_setup.symbol == symbol:
            self.last_setup = None

    def diagnostic(self, symbol: str, timeframe: str) -> dict | None:
        value = self.diagnostics.get((symbol, timeframe))
        return dict(value) if value else None

    @staticmethod
    def _checklist(**passed: bool) -> dict[str, bool]:
        names = (
            "bias", "liquidity", "sweep", "rejection_block", "smt_checked",
            "displacement", "mss_bos", "retracement", "entry_stop", "target",
        )
        return {name: bool(passed.get(name, False)) for name in names}

    def _set_diag(
        self,
        symbol: str,
        timeframe: str,
        market_time: datetime,
        *,
        stage: str,
        direction: str | None = None,
        checklist: dict[str, bool] | None = None,
        note: str = "",
        setup_id: str | None = None,
        smt_present: bool | None = None,
    ) -> dict:
        checklist = checklist or self._checklist()
        score = sum(int(v) for v in checklist.values())
        item = {
            "symbol": symbol,
            "timeframe": timeframe,
            "market_time": market_time.isoformat(),
            "stage": stage,
            "direction": direction,
            "pd_array": checklist["bias"] and checklist["liquidity"],
            "signal": checklist["sweep"] and checklist["rejection_block"],
            "displacement": checklist["smt_checked"] and checklist["displacement"],
            "entry_fvg": checklist["mss_bos"],
            "retracement": checklist["retracement"],
            "rr": checklist["entry_stop"] and checklist["target"],
            "trigger_type": "rejection_block",
            "note": f"RB {score}/10. {note}".strip(),
            "setup_id": setup_id,
            "strategy_name": self.strategy_name,
            "checklist": checklist,
            "checklist_score": score,
            "checklist_total": 10,
            "smt_present": smt_present,
        }
        self.diagnostics[(symbol, timeframe)] = item
        return item

    def _log(self, text: str, market_time: datetime) -> None:
        self.events.append(f"{market_time.strftime('%H:%M:%S')} {text}")
        self.events = self.events[-24:]

    @staticmethod
    def _bias(symbol: str, timeframe: str, histories) -> tuple[str | None, str, str]:
        bias_tf = BIAS_TIMEFRAME.get(timeframe, timeframe)
        candles = list(histories.get((symbol, bias_tf), []))
        if len(candles) < 8:
            return None, bias_tf, f"Need more {bias_tf} candles for directional bias."
        swings = detect_swings(candles[:-1])
        highs = [s for s in swings if s.kind == "high"]
        lows = [s for s in swings if s.kind == "low"]
        if len(highs) < 2 or len(lows) < 2:
            return None, bias_tf, f"Need two confirmed swing highs/lows on {bias_tf}."
        if highs[-1].price > highs[-2].price and lows[-1].price > lows[-2].price:
            return "bullish", bias_tf, f"{bias_tf} HH/HL structure; draw is liquidity above."
        if highs[-1].price < highs[-2].price and lows[-1].price < lows[-2].price:
            return "bearish", bias_tf, f"{bias_tf} LH/LL structure; draw is liquidity below."
        return None, bias_tf, f"{bias_tf} structure is mixed; no clean directional draw."

    def _clean_rejection(
        self, candle: Candle, direction: str, swept_level: float
    ) -> tuple[bool, float, float, float]:
        if candle.range <= 0:
            return False, 0.0, 0.0, 0.0
        body_low = min(candle.open, candle.close)
        body_high = max(candle.open, candle.close)
        if direction == "bullish":
            wick = (body_low - candle.low) / candle.range
            close_location = (candle.close - candle.low) / candle.range
            clean = (
                candle.low < swept_level < candle.close
                and wick >= self.min_rejection_wick_fraction
                and close_location >= 0.60
            )
            return clean, candle.low, body_low, candle.low
        wick = (candle.high - body_high) / candle.range
        close_location = (candle.high - candle.close) / candle.range
        clean = (
            candle.high > swept_level > candle.close
            and wick >= self.min_rejection_wick_fraction
            and close_location >= 0.60
        )
        return clean, body_high, candle.high, candle.high

    @staticmethod
    def _structure_level(candles: list[Candle], direction: str) -> float | None:
        swings = detect_swings(candles[:-1])
        kind = "high" if direction == "bullish" else "low"
        swing = next((s for s in reversed(swings) if s.kind == kind), None)
        return swing.price if swing else None

    @staticmethod
    def _pair(symbol: str) -> str | None:
        return "ES" if symbol == "NQ" else "NQ" if symbol == "ES" else None

    def _smt_check(
        self, context: RejectionBlockContext, histories
    ) -> tuple[bool, bool, str]:
        pair = self._pair(context.symbol)
        if pair is None:
            return True, False, "Correlation check not required for this market."
        leader = [
            c
            for c in histories.get((context.symbol, context.timeframe), [])
            if c.close_time <= context.sweep_time
        ]
        laggard = [
            c
            for c in histories.get((pair, context.timeframe), [])
            if c.close_time <= context.sweep_time
        ]
        if (
            not leader
            or not laggard
            or leader[-1].close_time != context.sweep_time
            or laggard[-1].close_time != context.sweep_time
        ):
            return False, False, f"Waiting for synchronized {pair} candle to check SMT."
        smt = detect_smt(leader, laggard)
        if smt and smt.direction == context.direction:
            return True, True, smt.description
        return True, False, f"SMT checked versus {pair}; no divergence manufactured."

    @staticmethod
    def _entry_zone(
        context: RejectionBlockContext, fvg: FairValueGap
    ) -> tuple[float, float, str]:
        low = max(context.rejection_low, fvg.lower)
        high = min(context.rejection_high, fvg.upper)
        if low <= high:
            return low, high, "RB_FVG_OVERLAP"
        return fvg.lower, fvg.upper, "DISPLACEMENT_FVG"

    def _base(self, context: RejectionBlockContext) -> dict[str, bool]:
        return self._checklist(
            bias=True,
            liquidity=True,
            sweep=True,
            rejection_block=True,
            smt_checked=context.smt_checked,
            displacement=context.displacement is not None,
            mss_bos=context.mss_time is not None,
        )

    def _expire_if_needed(
        self, key, context, bar_count: int, market_time: datetime
    ) -> bool:
        limit = self.stage_expiry_bars.get(context.stage)
        if limit is None or bar_count - context.stage_bar_count <= limit:
            return False
        self.contexts.pop(key, None)
        self._set_diag(
            context.symbol,
            context.timeframe,
            market_time,
            stage="RB_EXPIRED",
            direction=context.direction,
            checklist=self._base(context),
            smt_present=context.smt_present,
            note=f"{context.stage} expired. A+ sequence must restart from fresh liquidity.",
        )
        return True

    def on_candle(
        self, symbol: str, timeframe: str, histories
    ) -> StrategySetup | None:
        candles = list(histories.get((symbol, timeframe), []))
        if not candles:
            return None
        market_time = candles[-1].close_time
        if len(candles) < 8:
            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage="RB_WARMUP",
                note=f"Need more candles ({len(candles)}/8 minimum).",
            )
            return None

        key = (symbol, timeframe)
        context = self.contexts.get(key)
        if context and self._expire_if_needed(
            key, context, len(candles), market_time
        ):
            return None

        if context is None:
            direction, bias_tf, bias_reason = self._bias(
                symbol, timeframe, histories
            )
            if direction is None:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="RB_WAIT_BIAS",
                    note=bias_reason,
                )
                return None

            sweep = detect_liquidity_sweep(candles)
            if not sweep or sweep.direction != direction:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="RB_WAIT_SWEEP",
                    direction=direction,
                    checklist=self._checklist(bias=True),
                    note=f"{bias_reason} Waiting for matching swing-liquidity sweep.",
                )
                return None

            clean, rb_low, rb_high, invalidation = self._clean_rejection(
                candles[-1], direction, sweep.swept_level
            )
            if not clean:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="RB_WAIT_REJECTION",
                    direction=direction,
                    checklist=self._checklist(
                        bias=True, liquidity=True, sweep=True
                    ),
                    note="Liquidity swept, but the raid did not create a clean rejection block.",
                )
                return None

            structure_level = self._structure_level(candles, direction)
            if structure_level is None:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="RB_WAIT_STRUCTURE",
                    direction=direction,
                    checklist=self._checklist(
                        bias=True,
                        liquidity=True,
                        sweep=True,
                        rejection_block=True,
                    ),
                    note="No confirmed short-term structure level exists for MSS/BOS.",
                )
                return None

            context = RejectionBlockContext(
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                bias_timeframe=bias_tf,
                bias_reason=bias_reason,
                liquidity_type=(
                    "confirmed_swing_low"
                    if direction == "bullish"
                    else "confirmed_swing_high"
                ),
                liquidity_level=sweep.swept_level,
                sweep_time=sweep.candle_time,
                rejection_low=rb_low,
                rejection_high=rb_high,
                invalidation_price=invalidation,
                structure_level=structure_level,
                stage="WAIT_SMT" if self._pair(symbol) else "WAIT_DISPLACEMENT",
                stage_bar_count=len(candles),
            )
            self.contexts[key] = context
            self._log(
                f"{symbol} {timeframe}: liquidity raid + rejection block",
                market_time,
            )

        if not context.smt_checked:
            checked, present, description = self._smt_check(context, histories)
            if not checked:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="RB_WAIT_SMT",
                    direction=context.direction,
                    checklist=self._base(context),
                    note=description,
                )
                return None
            context.smt_checked = True
            context.smt_present = present
            context.smt_description = description
            context.stage = "WAIT_DISPLACEMENT"
            context.stage_bar_count = len(candles)

        if context.stage == "WAIT_DISPLACEMENT":
            if len(candles) <= context.stage_bar_count:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="RB_WAIT_DISPLACEMENT",
                    direction=context.direction,
                    checklist=self._base(context),
                    smt_present=context.smt_present,
                    note="Raid/rejection confirmed. Waiting for later aggressive displacement that creates an FVG.",
                )
                return None

            displacement = detect_displacement(candles)
            fvg = detect_fvg(candles)
            if (
                not displacement
                or displacement.direction != context.direction
                or not fvg
                or fvg.direction != context.direction
            ):
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="RB_WAIT_DISPLACEMENT",
                    direction=context.direction,
                    checklist=self._base(context),
                    smt_present=context.smt_present,
                    note="Weak/choppy reaction is not enough. Waiting for strong displacement + FVG.",
                )
                return None

            context.displacement = displacement
            context.entry_fvg = fvg
            (
                context.entry_zone_low,
                context.entry_zone_high,
                context.entry_area_type,
            ) = self._entry_zone(context, fvg)

            latest = candles[-1]
            broke = (
                latest.close > context.structure_level
                if context.direction == "bullish"
                else latest.close < context.structure_level
            )
            if broke:
                context.mss_time = latest.close_time
                context.stage = "WAIT_RETRACE"
                context.stage_bar_count = len(candles)
                stage = "RB_WAIT_RETRACE"
                note = (
                    "Displacement broke structure. Do not chase; waiting for a later "
                    "retracement into the planned RB/FVG area."
                )
            else:
                context.stage = "WAIT_MSS_BOS"
                context.stage_bar_count = len(candles)
                stage = "RB_WAIT_MSS_BOS"
                note = (
                    f"Displacement/FVG confirmed. Waiting for close beyond "
                    f"{context.structure_level:.2f} for MSS/BOS."
                )
            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage=stage,
                direction=context.direction,
                checklist=self._base(context),
                smt_present=context.smt_present,
                note=note,
            )
            return None

        if context.stage == "WAIT_MSS_BOS":
            latest = candles[-1]
            broke = (
                latest.close > context.structure_level
                if context.direction == "bullish"
                else latest.close < context.structure_level
            )
            if not broke:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="RB_WAIT_MSS_BOS",
                    direction=context.direction,
                    checklist=self._base(context),
                    smt_present=context.smt_present,
                    note=(
                        f"Waiting for close beyond {context.structure_level:.2f}; "
                        "no predicted reversal."
                    ),
                )
                return None
            context.mss_time = latest.close_time
            context.stage = "WAIT_RETRACE"
            context.stage_bar_count = len(candles)
            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage="RB_WAIT_RETRACE",
                direction=context.direction,
                checklist=self._base(context),
                smt_present=context.smt_present,
                note=(
                    "MSS/BOS confirmed. Waiting for a later retracement; "
                    "missed entry = let it go."
                ),
            )
            return None

        if context.stage != "WAIT_RETRACE":
            return None
        if len(candles) <= context.stage_bar_count:
            return None

        latest = candles[-1]
        invalid = (
            latest.low <= context.invalidation_price
            if context.direction == "bullish"
            else latest.high >= context.invalidation_price
        )
        if invalid:
            self.contexts.pop(key, None)
            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage="RB_INVALIDATED",
                direction=context.direction,
                checklist=self._base(context),
                smt_present=context.smt_present,
                note="Rejection-block invalidation breached before entry. NO TRADE.",
            )
            return None

        zone_low = float(context.entry_zone_low)
        zone_high = float(context.entry_zone_high)
        touched = latest.high >= zone_low and latest.low <= zone_high
        close_inside = zone_low <= latest.close <= zone_high
        if not (touched and close_inside):
            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage="RB_WAIT_RETRACE",
                direction=context.direction,
                checklist=self._base(context),
                smt_present=context.smt_present,
                note=(
                    f"Waiting for retracement and close inside "
                    f"{context.entry_area_type} {zone_low:.2f}-{zone_high:.2f}."
                ),
            )
            return None

        setup = self._build_setup(candles, context, latest.close, market_time)
        if setup is None:
            self.contexts.pop(key, None)
            checklist = self._base(context)
            checklist.update(
                retracement=True,
                entry_stop=True,
                target=False,
            )
            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage="RB_REJECTED_9_OF_10",
                direction=context.direction,
                checklist=checklist,
                smt_present=context.smt_present,
                note=(
                    "Entry/stop were clear, but clean opposing liquidity did not "
                    "justify 3R. 9/10 = NO TRADE."
                ),
            )
            return None

        self.last_setup = setup
        self.contexts.pop(key, None)
        checklist = self._checklist(
            bias=True,
            liquidity=True,
            sweep=True,
            rejection_block=True,
            smt_checked=True,
            displacement=True,
            mss_bos=True,
            retracement=True,
            entry_stop=True,
            target=True,
        )
        self._set_diag(
            symbol,
            timeframe,
            market_time,
            stage="RB_SETUP_READY",
            direction=context.direction,
            checklist=checklist,
            smt_present=context.smt_present,
            note=(
                "10/10 A+ rejection block confirmed. Entry, stop and target "
                "locked before execution."
            ),
            setup_id=setup.setup_id,
        )
        self._log(
            f"{symbol} {timeframe}: 10/10 REJECTION BLOCK READY",
            market_time,
        )
        return setup

    def _build_setup(
        self,
        candles: list[Candle],
        context: RejectionBlockContext,
        raw_entry: float,
        market_time: datetime,
    ) -> StrategySetup | None:
        raw_stop = (
            context.invalidation_price * (1 - self.stop_buffer_fraction)
            if context.direction == "bullish"
            else context.invalidation_price * (1 + self.stop_buffer_fraction)
        )
        target_swing = nearest_target_swing(
            candles[:-1], context.direction, raw_entry
        )
        if target_swing is None:
            return None

        risk = abs(raw_entry - raw_stop)
        if risk <= 0:
            return None
        three_r = (
            raw_entry + risk * self.min_rr
            if context.direction == "bullish"
            else raw_entry - risk * self.min_rr
        )
        if context.direction == "bullish" and target_swing.price < three_r:
            return None
        if context.direction == "bearish" and target_swing.price > three_r:
            return None

        entry, stop, target = normalize_trade_prices(
            context.symbol,
            context.direction,
            raw_entry,
            raw_stop,
            three_r,
        )
        geometry = validate_trade_geometry(
            context.symbol,
            context.direction,
            entry,
            stop,
            target,
        )
        if (
            not geometry.valid
            or float(geometry.risk_reward or 0.0) < self.min_rr
        ):
            return None

        fvg = context.entry_fvg
        assert fvg is not None and context.displacement is not None
        checklist = {name: True for name in self._checklist()}
        return StrategySetup(
            setup_id=uuid4().hex[:12],
            symbol=context.symbol,
            timeframe=context.timeframe,
            direction=context.direction,
            created_at=market_time,
            # StrategySetup predates multi-strategy support and requires a FVG
            # PD-array field. The strategy identity is explicit in metadata.
            pd_array=fvg,
            trigger_type="rejection_block",
            trigger_details={
                "liquidity_type": context.liquidity_type,
                "liquidity_level": context.liquidity_level,
                "sweep_time": context.sweep_time.isoformat(),
                "rejection_block": {
                    "low": context.rejection_low,
                    "high": context.rejection_high,
                    "invalidation": context.invalidation_price,
                },
                "smt_checked": context.smt_checked,
                "smt_present": context.smt_present,
                "smt_description": context.smt_description,
                "mss_bos_level": context.structure_level,
                "entry_area_type": context.entry_area_type,
                "entry_zone": [
                    context.entry_zone_low,
                    context.entry_zone_high,
                ],
            },
            displacement=context.displacement,
            entry_fvg=fvg,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            risk_reward=float(geometry.risk_reward or 0.0),
            metadata={
                "strategy": self.strategy_name,
                "checklist_score": 10,
                "checklist_total": 10,
                "checklist": checklist,
                "bias_timeframe": context.bias_timeframe,
                "bias_reason": context.bias_reason,
                "capital_plan": "$250 risk / $750 objective",
                "target_rule": (
                    "Opposing confirmed liquidity must offer >=3R; modeled exit "
                    "is fixed at 3R."
                ),
                "entry_rule": (
                    "After MSS/BOS, a later candle must retrace and close inside "
                    "the planned RB/FVG area."
                ),
                "rejection_rule": (
                    "Sweep candle must close back through liquidity with >=30% "
                    "rejection wick and favorable 40% close location."
                ),
                "no_chase": True,
            },
        )
