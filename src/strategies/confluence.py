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
    stage_bar_count: int
    trigger_type: str | None = None
    trigger_details: dict | None = None
    swept_level: float | None = None
    displacement: object | None = None


class ConfluenceEngine:
    """State machine for PD Array -> Signal -> Displacement -> Entry FVG.

    Operation 4 deliberately exposes each stage so replay sessions can show
    exactly why a setup is waiting, rejected, or accepted.
    """

    def __init__(
        self,
        min_rr: float = 1.25,
        context_expiry_bars: int = 16,
        displacement_expiry_bars: int = 8,
        entry_fvg_expiry_bars: int = 8,
        stop_buffer_fraction: float = 0.0001,
    ):
        self.min_rr = min_rr
        # Each setup stage gets its own fresh timer. The original Operation 4
        # used one timer from the initial PD-array touch, which could expire a
        # valid setup immediately after it finally reached displacement.
        self.stage_expiry_bars = {
            "WAIT_SIGNAL": context_expiry_bars,
            "WAIT_DISPLACEMENT": displacement_expiry_bars,
            "WAIT_ENTRY_FVG": entry_fvg_expiry_bars,
        }
        self.stop_buffer_fraction = stop_buffer_fraction
        self.contexts: dict[tuple[str, str], PendingContext] = {}
        self.last_setup: StrategySetup | None = None
        self.events: list[str] = []
        self.diagnostics: dict[tuple[str, str], dict] = {}

    def _log(self, message: str, event_time: datetime | None = None) -> None:
        event_time = event_time or datetime.now(timezone.utc)
        now = event_time.astimezone(timezone.utc).strftime("%H:%M:%S")
        self.events.append(f"{now} {message}")
        self.events = self.events[-16:]

    def _set_diag(
        self,
        symbol: str,
        timeframe: str,
        market_time: datetime,
        *,
        stage: str,
        direction: str | None = None,
        pd_array: bool = False,
        signal: bool = False,
        displacement: bool = False,
        entry_fvg: bool = False,
        retracement: bool = False,
        rr: bool = False,
        trigger_type: str | None = None,
        note: str = "",
        setup_id: str | None = None,
    ) -> dict:
        item = {
            "symbol": symbol,
            "timeframe": timeframe,
            "market_time": market_time.isoformat(),
            "stage": stage,
            "direction": direction,
            "pd_array": bool(pd_array),
            "signal": bool(signal),
            "displacement": bool(displacement),
            "entry_fvg": bool(entry_fvg),
            "retracement": bool(retracement),
            "rr": bool(rr),
            "trigger_type": trigger_type,
            "note": note,
            "setup_id": setup_id,
        }
        self.diagnostics[(symbol, timeframe)] = item
        return item

    def diagnostic(self, symbol: str, timeframe: str) -> dict | None:
        value = self.diagnostics.get((symbol, timeframe))
        return dict(value) if value else None

    def clear_symbol(self, symbol: str) -> None:
        """Clear active scanner state for a symbol without deleting history."""
        for key in [key for key in self.contexts if key[0] == symbol]:
            self.contexts.pop(key, None)
        for key in [key for key in self.diagnostics if key[0] == symbol]:
            self.diagnostics.pop(key, None)
        if self.last_setup and self.last_setup.symbol == symbol:
            self.last_setup = None

    def _stage_expired(self, context: PendingContext, current_bar_count: int) -> tuple[bool, int, int]:
        limit = self.stage_expiry_bars.get(context.stage, 16)
        elapsed = max(0, current_bar_count - context.stage_bar_count)
        return elapsed > limit, elapsed, limit

    def on_candle(self, symbol: str, timeframe: str, histories: dict[tuple[str, str], list]):
        candles = histories.get((symbol, timeframe), [])
        if not candles:
            return None
        market_time = candles[-1].close_time

        if len(candles) < 6:
            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage="WARMUP",
                note=f"Need more candles ({len(candles)}/6 minimum).",
            )
            return None

        key = (symbol, timeframe)
        context = self.contexts.get(key)

        if context:
            expired, elapsed, limit = self._stage_expired(context, len(candles))
            if expired:
                expired_stage = context.stage
                self._log(
                    f"{symbol} {timeframe}: {expired_stage} expired after {elapsed} bars",
                    market_time,
                )
                self.contexts.pop(key, None)
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="EXPIRED",
                    direction=context.direction,
                    pd_array=True,
                    signal=context.trigger_type is not None,
                    displacement=context.displacement is not None,
                    trigger_type=context.trigger_type,
                    note=f"{expired_stage} expired after {elapsed} bars (limit {limit}). Waiting for a new PD-array sequence.",
                )
                return None

        if context is None:
            pd_array = find_pd_array_touch(candles)
            if not pd_array:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="WAIT_PD_ARRAY",
                    note="Waiting for price to touch an active FVG PD array.",
                )
                return None

            context = PendingContext(
                symbol=symbol,
                timeframe=timeframe,
                direction=pd_array.direction,
                pd_array=pd_array,
                stage="WAIT_SIGNAL",
                started_bar_count=len(candles),
                stage_bar_count=len(candles),
            )
            self.contexts[key] = context
            self._log(f"{symbol} {timeframe}: {pd_array.direction} PD-array FVG touched", market_time)

        # Baseline checks carried through subsequent diagnostic states.
        passed_pd = True
        passed_signal = context.trigger_type is not None
        passed_displacement = context.displacement is not None

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
                context.stage_bar_count = len(candles)
                self._log(f"{symbol} {timeframe}: liquidity sweep confirmed", market_time)
                passed_signal = True

            elif smt and smt.direction == context.direction:
                context.trigger_type = "smt"
                context.trigger_details = {
                    "leader": smt.leader,
                    "laggard": smt.laggard,
                    "description": smt.description,
                }
                context.stage = "WAIT_DISPLACEMENT"
                context.stage_bar_count = len(candles)
                self._log(f"{symbol} {timeframe}: SMT confirmed", market_time)
                passed_signal = True

            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage=context.stage,
                direction=context.direction,
                pd_array=passed_pd,
                signal=passed_signal,
                trigger_type=context.trigger_type,
                note=(
                    f"Signal confirmed by {context.trigger_type.replace('_', ' ')}."
                    if passed_signal
                    else "PD array touched. Waiting for liquidity sweep or NQ/ES SMT."
                ),
            )
            return None

        if context.stage == "WAIT_DISPLACEMENT":
            # Displacement must occur after the signal bar, not on a duplicate
            # evaluation of the same candle when the paired market closes.
            if len(candles) <= context.stage_bar_count:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage=context.stage,
                    direction=context.direction,
                    pd_array=True,
                    signal=True,
                    trigger_type=context.trigger_type,
                    note="Signal confirmed. Waiting for a later displacement candle.",
                )
                return None

            displacement = detect_displacement(candles)
            if displacement and displacement.direction == context.direction:
                context.displacement = displacement
                context.stage = "WAIT_ENTRY_FVG"
                context.stage_bar_count = len(candles)
                passed_displacement = True
                self._log(f"{symbol} {timeframe}: displacement confirmed", market_time)

            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage=context.stage,
                direction=context.direction,
                pd_array=True,
                signal=True,
                displacement=passed_displacement,
                trigger_type=context.trigger_type,
                note=(
                    "Displacement confirmed. Waiting for the 3-candle entry FVG."
                    if passed_displacement
                    else "Waiting for aggressive displacement after the signal."
                ),
            )
            return None

        if context.stage == "WAIT_ENTRY_FVG":
            if len(candles) <= context.stage_bar_count:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage=context.stage,
                    direction=context.direction,
                    pd_array=True,
                    signal=True,
                    displacement=True,
                    trigger_type=context.trigger_type,
                    note="Displacement confirmed. Waiting for a later candle to complete the FVG.",
                )
                return None

            fvg = detect_fvg(candles)
            entry_ok = bool(fvg and fvg.direction == context.direction)
            retrace_ok = bool(entry_ok and fvg_in_retracement_zone(fvg, context.displacement))

            if not entry_ok:
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage=context.stage,
                    direction=context.direction,
                    pd_array=True,
                    signal=True,
                    displacement=True,
                    trigger_type=context.trigger_type,
                    note="Waiting for a same-direction entry FVG after displacement.",
                )
                return None

            if not retrace_ok:
                self._log(f"{symbol} {timeframe}: entry FVG outside 50-79% zone", market_time)
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage=context.stage,
                    direction=context.direction,
                    pd_array=True,
                    signal=True,
                    displacement=True,
                    entry_fvg=True,
                    trigger_type=context.trigger_type,
                    note="Entry FVG formed but is outside the 50-79% displacement retracement zone.",
                )
                return None

            setup = self._build_setup(candles, context, fvg, market_time)
            if setup:
                self.last_setup = setup
                self.contexts.pop(key, None)
                self._log(
                    f"{symbol} {timeframe}: SETUP {setup.direction.upper()} RR {setup.risk_reward:.2f}",
                    market_time,
                )
                self._set_diag(
                    symbol,
                    timeframe,
                    market_time,
                    stage="SETUP_READY",
                    direction=context.direction,
                    pd_array=True,
                    signal=True,
                    displacement=True,
                    entry_fvg=True,
                    retracement=True,
                    rr=True,
                    trigger_type=context.trigger_type,
                    note=f"Valid setup ready at {setup.risk_reward:.2f}R.",
                    setup_id=setup.setup_id,
                )
                return setup

            self._set_diag(
                symbol,
                timeframe,
                market_time,
                stage="WAIT_VALID_RR",
                direction=context.direction,
                pd_array=True,
                signal=True,
                displacement=True,
                entry_fvg=True,
                retracement=True,
                trigger_type=context.trigger_type,
                note="Entry FVG qualifies, but stop/target structure does not yet produce a valid trade.",
            )

        return None

    @staticmethod
    def _detect_pair_smt(symbol: str, timeframe: str, histories):
        if symbol == "NQ":
            pair = "ES"
        elif symbol == "ES":
            pair = "NQ"
        else:
            return None

        leader = histories.get((symbol, timeframe), [])
        laggard = histories.get((pair, timeframe), [])
        return detect_smt(leader, laggard)

    def _build_setup(
        self,
        candles,
        context: PendingContext,
        entry_fvg: FairValueGap,
        market_time: datetime,
    ):
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
            self._log(f"{context.symbol} {context.timeframe}: rejected low RR {rr:.2f}", market_time)
            return None

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
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            risk_reward=rr,
            metadata={
                "entry_rule": "FVG midpoint",
                "discount_rule": "50-79% displacement retracement",
                "pd_array_type": "ACTIVE_FVG",
                "operation": 4,
            },
        )
