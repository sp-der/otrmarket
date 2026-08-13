from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from src.strategies.ict import (
    detect_displacement,
    detect_fvg,
    detect_liquidity_sweep,
    detect_smt,
    find_pd_array_touch,
    fvg_in_retracement_zone,
)
from src.strategies.models import FairValueGap, StrategySetup
from src.strategies.structure import detect_swings, nearest_target_swing


@dataclass
class PendingContext:
    symbol: str
    timeframe: str
    direction: str
    pd_array: FairValueGap
    stage: str
    started_bar_count: int
    trigger_type: str | None = None
    trigger_details: dict | None = None
    swept_level: float | None = None
    displacement: object | None = None


class ConfluenceEngine:
    """State machine for the user's PD Array -> Signal -> Displacement -> FVG setup."""

    def __init__(
        self,
        min_rr: float = 1.25,
        context_expiry_bars: int = 16,
        stop_buffer_fraction: float = 0.0001,
    ):
        self.min_rr = min_rr
        self.context_expiry_bars = context_expiry_bars
        self.stop_buffer_fraction = stop_buffer_fraction
        self.contexts: dict[tuple[str, str], PendingContext] = {}
        self.last_setup: StrategySetup | None = None
        self.events: list[str] = []

    def _log(self, message: str) -> None:
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.events.append(f"{now} {message}")
        self.events = self.events[-8:]

    def on_candle(self, symbol: str, timeframe: str, histories: dict[tuple[str, str], list]):
        candles = histories.get((symbol, timeframe), [])
        if len(candles) < 6:
            return None

        key = (symbol, timeframe)
        context = self.contexts.get(key)

        if context and len(candles) - context.started_bar_count > self.context_expiry_bars:
            self._log(f"{symbol} {timeframe}: context expired at {context.stage}")
            self.contexts.pop(key, None)
            context = None

        if context is None:
            pd_array = find_pd_array_touch(candles)
            if pd_array:
                context = PendingContext(
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=pd_array.direction,
                    pd_array=pd_array,
                    stage="WAIT_SIGNAL",
                    started_bar_count=len(candles),
                )
                self.contexts[key] = context
                self._log(
                    f"{symbol} {timeframe}: {pd_array.direction} PD-array FVG touched"
                )
                # Keep evaluating the same candle. A liquidity sweep can be the
                # exact candle that taps the PD array, so returning here would
                # miss valid setups.

        if context.stage == "WAIT_SIGNAL":
            sweep = detect_liquidity_sweep(candles)
            smt = self._detect_pair_smt(symbol, timeframe, histories)

            if sweep and sweep.direction == context.direction:
                context.trigger_type = "liquidity_sweep"
                context.trigger_details = {
                    "swept_level": sweep.swept_level,
                    "swing_time": sweep.swing_time.isoformat(),
                }
                context.swept_level = sweep.swept_level
                context.stage = "WAIT_DISPLACEMENT"
                self._log(f"{symbol} {timeframe}: liquidity sweep confirmed")

            elif smt and smt.direction == context.direction:
                context.trigger_type = "smt"
                context.trigger_details = {
                    "leader": smt.leader,
                    "laggard": smt.laggard,
                    "description": smt.description,
                }
                context.stage = "WAIT_DISPLACEMENT"
                self._log(f"{symbol} {timeframe}: SMT confirmed")

            return None

        if context.stage == "WAIT_DISPLACEMENT":
            displacement = detect_displacement(candles)
            if displacement and displacement.direction == context.direction:
                context.displacement = displacement
                context.stage = "WAIT_ENTRY_FVG"
                self._log(f"{symbol} {timeframe}: displacement confirmed")
            return None

        if context.stage == "WAIT_ENTRY_FVG":
            fvg = detect_fvg(candles)
            if not fvg or fvg.direction != context.direction:
                return None

            if not fvg_in_retracement_zone(fvg, context.displacement):
                self._log(f"{symbol} {timeframe}: entry FVG outside 50-79% zone")
                return None

            setup = self._build_setup(candles, context, fvg)
            if setup:
                self.last_setup = setup
                self.contexts.pop(key, None)
                self._log(
                    f"{symbol} {timeframe}: SETUP {setup.direction.upper()} RR {setup.risk_reward:.2f}"
                )
                return setup

        return None

    @staticmethod
    def _detect_pair_smt(symbol: str, timeframe: str, histories):
        pair = None
        if symbol == "QQQ":
            pair = "SPY"
        elif symbol == "SPY":
            pair = "QQQ"
        if pair is None:
            return None

        leader = histories.get((symbol, timeframe), [])
        laggard = histories.get((pair, timeframe), [])
        return detect_smt(leader, laggard)

    def _build_setup(self, candles, context: PendingContext, entry_fvg: FairValueGap):
        entry = entry_fvg.midpoint
        swings = detect_swings(candles)

        if context.direction == "bullish":
            low_swings = [s for s in swings if s.kind == "low" and s.price < entry]
            if not low_swings:
                return None
            stop_anchor = context.swept_level or low_swings[-1].price
            stop = stop_anchor * (1 - self.stop_buffer_fraction)
        else:
            high_swings = [s for s in swings if s.kind == "high" and s.price > entry]
            if not high_swings:
                return None
            stop_anchor = context.swept_level or high_swings[-1].price
            stop = stop_anchor * (1 + self.stop_buffer_fraction)

        target_swing = nearest_target_swing(candles, context.direction, entry)
        if not target_swing:
            return None
        target = target_swing.price

        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0 or reward <= 0:
            return None
        rr = reward / risk
        if rr < self.min_rr:
            self._log(f"{context.symbol} {context.timeframe}: rejected low RR {rr:.2f}")
            return None

        return StrategySetup(
            setup_id=uuid4().hex[:12],
            symbol=context.symbol,
            timeframe=context.timeframe,
            direction=context.direction,
            created_at=datetime.now(timezone.utc),
            pd_array=context.pd_array,
            trigger_type=context.trigger_type,
            trigger_details=context.trigger_details or {},
            displacement=context.displacement,
            entry_fvg=entry_fvg,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            risk_reward=rr,
            metadata={
                "entry_rule": "FVG midpoint",
                "discount_rule": "50-79% displacement retracement",
                "pd_array_type": "FVG_ONLY_OPERATION_1",
            },
        )
