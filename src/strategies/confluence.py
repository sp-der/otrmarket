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
from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
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
    entry_fvg_seen: bool = False
    retracement_seen: bool = False


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
            "WAIT_QUALIFYING_FVG": entry_fvg_expiry_bars,
            "WAIT_VALID_RR": entry_fvg_expiry_bars,
        }
        self.stop_buffer_fraction = stop_buffer_fraction
        self.contexts: dict[tuple[str, str], PendingContext] = {}
        self.last_setup: StrategySetup | None = None
        self.events: list[str] = []
        self.diagnostics: dict[tuple[str, str], dict] = {}
        self.risk_rejections: dict[tuple[str, str], str] = {}

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
        for key in [key for key in self.risk_rejections if key[0] == symbol]:
            self.risk_rejections.pop(key, None)
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
                    entry_fvg=context.entry_fvg_seen,
                    retracement=context.retracement_seen,
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

        if context.stage in {"WAIT_ENTRY_FVG", "WAIT_QUALIFYING_FVG", "WAIT_VALID_RR"}:
            # The entry-candidate timer starts when displacement is confirmed and
            # intentionally does NOT reset when a non-qualifying FVG appears.
            # This lets us keep scanning for a better same-direction FVG without
            # allowing a setup to live forever.
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
                    entry_fvg=context.entry_fvg_seen,
                    retracement=context.retracement_seen,
                    trigger_type=context.trigger_type,
                    note=(
                        "A qualifying FVG was seen, but structure/R:R is not valid yet. Waiting for a later candidate."
                        if context.stage == "WAIT_VALID_RR"
                        else "An entry FVG was seen outside the 50-79% zone. Waiting for a qualifying same-direction FVG."
                        if context.stage == "WAIT_QUALIFYING_FVG"
                        else "Displacement confirmed. Waiting for a later candle to complete the FVG."
                    ),
                )
                return None

            fvg = detect_fvg(candles)
            entry_ok = bool(fvg and fvg.direction == context.direction)

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
                    entry_fvg=context.entry_fvg_seen,
                    retracement=context.retracement_seen,
                    trigger_type=context.trigger_type,
                    note=(
                        "Qualifying FVG found previously, but no valid risk/reward yet. Scanning for another entry candidate."
                        if context.stage == "WAIT_VALID_RR"
                        else "Entry FVG detected previously, but it missed the 50-79% zone. Scanning for another same-direction FVG."
                        if context.entry_fvg_seen
                        else "Waiting for a same-direction entry FVG after displacement."
                    ),
                )
                return None

            context.entry_fvg_seen = True
            retrace_ok = bool(fvg_in_retracement_zone(fvg, context.displacement))

            if not retrace_ok:
                # Seeing an FVG is progress, but it is not an entry candidate
                # unless it falls inside the displacement retracement zone.
                # Keep the original post-displacement timer running.
                if not context.retracement_seen:
                    context.stage = "WAIT_QUALIFYING_FVG"
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
                    retracement=context.retracement_seen,
                    trigger_type=context.trigger_type,
                    note="Entry FVG detected, but it is outside the 50-79% displacement retracement zone. Scanning for another candidate.",
                )
                return None

            context.retracement_seen = True
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

            # The FVG is correctly positioned, but current swing structure or
            # R:R does not qualify. Keep scanning within the same timer window.
            context.stage = "WAIT_VALID_RR"
            rejection = self.risk_rejections.get(key)
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
                retracement=True,
                trigger_type=context.trigger_type,
                note=(
                    f"Risk check rejected this candidate: {rejection} Scanning for another candidate."
                    if rejection
                    else "Entry FVG is inside the 50-79% zone, but current stop/target structure does not produce a valid trade. Scanning for another candidate."
                ),
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

    def _reject_setup(self, context: PendingContext, reason: str, market_time: datetime):
        self.risk_rejections[(context.symbol, context.timeframe)] = reason
        self._log(f"{context.symbol} {context.timeframe}: risk rejected - {reason}", market_time)
        return None

    def _build_setup(
        self,
        candles,
        context: PendingContext,
        entry_fvg: FairValueGap,
        market_time: datetime,
    ):
        key = (context.symbol, context.timeframe)
        self.risk_rejections.pop(key, None)
        raw_entry = entry_fvg.midpoint
        swings = detect_swings(candles)

        if context.direction == "bullish":
            low_swings = [s for s in swings if s.kind == "low" and s.price < raw_entry]
            if not low_swings:
                return self._reject_setup(context, "No valid swing low exists below entry for the stop.", market_time)
            # Only trust a swept level if it is actually on the protective side
            # of the entry. This prevents a stale/misaligned sweep from creating
            # inverted trade geometry.
            if context.swept_level is not None and context.swept_level < raw_entry:
                stop_anchor = context.swept_level
            else:
                stop_anchor = low_swings[-1].price
            raw_stop = stop_anchor * (1 - self.stop_buffer_fraction)
        else:
            high_swings = [s for s in swings if s.kind == "high" and s.price > raw_entry]
            if not high_swings:
                return self._reject_setup(context, "No valid swing high exists above entry for the stop.", market_time)
            if context.swept_level is not None and context.swept_level > raw_entry:
                stop_anchor = context.swept_level
            else:
                stop_anchor = high_swings[-1].price
            raw_stop = stop_anchor * (1 + self.stop_buffer_fraction)

        target_swing = nearest_target_swing(candles, context.direction, raw_entry)
        if not target_swing:
            return self._reject_setup(context, "No valid opposing swing target exists beyond entry.", market_time)
        raw_target = target_swing.price

        entry, stop, target = normalize_trade_prices(
            context.symbol, context.direction, raw_entry, raw_stop, raw_target
        )
        geometry = validate_trade_geometry(context.symbol, context.direction, entry, stop, target)
        if not geometry.valid:
            return self._reject_setup(context, geometry.reason, market_time)

        rr = float(geometry.risk_reward or 0.0)
        if rr < self.min_rr:
            return self._reject_setup(
                context,
                f"Risk/reward {rr:.2f}R is below minimum {self.min_rr:.2f}R.",
                market_time,
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
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            risk_reward=rr,
            metadata={
                "entry_rule": "FVG midpoint rounded to exchange tick",
                "discount_rule": "50-79% displacement retracement",
                "pd_array_type": "ACTIVE_FVG",
                "geometry_guard": "long stop<entry<target; short target<entry<stop",
                "operation": 4.5,
            },
        )

