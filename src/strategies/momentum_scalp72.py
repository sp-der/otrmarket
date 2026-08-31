from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.strategies.ict import detect_displacement, find_recent_fvgs
from src.strategies.models import Displacement, FairValueGap, StrategySetup
from src.strategies.regime import classify_regime


MIN_RR = 1.25
TARGET_RR = 1.50
CONTEXT_BARS = 4
MAX_SIGNAL_PROGRESS = 0.35


@dataclass
class PendingMomentumScalp:
    direction: str
    displacement: Displacement
    break_level: float
    started_bar_count: int
    five_minute_regime: dict


class MomentumScalpEngine72:
    """Fast 1m first-pullback lane for strong, context-supported impulses.

    This engine is intentionally separate from ICT 6/6. It does not predict the
    beginning of a move and it never chases a mature impulse. A candidate must
    first print strong 1m displacement through recent micro structure, then wait
    for the first shallow pullback. The normal session, account-risk, cooldown,
    evaluation and paper-entry lifecycle still run after this detector.
    """

    def __init__(self) -> None:
        self.contexts: dict[tuple[str, str], PendingMomentumScalp] = {}
        self.diagnostics: dict[tuple[str, str], dict] = {}
        self.events: list[str] = []
        self.last_setup = None

    def clear_symbol(self, symbol: str) -> None:
        for key in [key for key in self.contexts if key[0] == symbol]:
            self.contexts.pop(key, None)
        for key in [key for key in self.diagnostics if key[0] == symbol]:
            self.diagnostics.pop(key, None)
        if self.last_setup and self.last_setup.symbol == symbol:
            self.last_setup = None

    def diagnostic(self, symbol: str, timeframe: str):
        value = self.diagnostics.get((symbol, timeframe))
        return dict(value) if value else None

    def _diag(
        self,
        symbol,
        timeframe,
        market_time,
        stage,
        *,
        direction=None,
        displacement=False,
        structure=False,
        htf=False,
        pullback=False,
        geometry=False,
        note="",
        setup_id=None,
    ) -> None:
        checks = (displacement, structure, htf, pullback, geometry)
        self.diagnostics[(symbol, timeframe)] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "market_time": market_time.isoformat(),
            "stage": stage,
            "direction": direction,
            "pd_array": bool(displacement),
            "signal": bool(structure),
            "displacement": bool(displacement),
            "entry_fvg": bool(pullback),
            "retracement": bool(pullback),
            "rr": bool(geometry),
            "trigger_type": "market_structure_shift" if structure else None,
            "note": f"SCALP · {note}",
            "setup_id": setup_id,
            "strategy_name": "MOMENTUM_SCALP",
            "strategy_score": round(sum(int(value) for value in checks) / len(checks) * 100),
            "checklist_score": sum(int(value) for value in checks),
            "checklist_total": len(checks),
        }

    @staticmethod
    def _history(histories, symbol: str, timeframe: str, cutoff):
        return [c for c in histories.get((symbol, timeframe), []) if c.close_time <= cutoff]

    @staticmethod
    def _micro_break(candles, direction: str):
        if len(candles) < 9:
            return None
        latest = candles[-1]
        previous = candles[-9:-1]
        if direction == "bullish":
            level = max(c.high for c in previous)
            return level if latest.close > level else None
        level = min(c.low for c in previous)
        return level if latest.close < level else None

    @staticmethod
    def _five_minute_context(histories, symbol: str, market_time, direction: str):
        candles = MomentumScalpEngine72._history(histories, symbol, "5m", market_time)
        regime = classify_regime(candles)
        htf_direction = str(regime.get("direction", "neutral"))
        if htf_direction == direction:
            return True, regime, "5m context aligned."
        if htf_direction == "neutral" and regime.get("regime") != "WARMUP":
            return True, regime, "5m context neutral; strong 1m impulse may scalp at reduced risk."
        return False, regime, f"5m context is {htf_direction}; momentum scalp is {direction}."

    @staticmethod
    def _fresh_fvg(candles, context: PendingMomentumScalp):
        for fvg in reversed(find_recent_fvgs(candles, lookback=12)):
            if fvg.direction != context.direction:
                continue
            if fvg.formed_at < context.displacement.candle_time:
                continue
            return fvg
        return None

    @staticmethod
    def _synthetic_pullback_zone(symbol: str, timeframe: str, context: PendingMomentumScalp, market_time):
        move = context.displacement.high - context.displacement.low
        if move <= 0:
            return None
        if context.direction == "bullish":
            lower = context.displacement.high - 0.62 * move
            upper = context.displacement.high - 0.50 * move
        else:
            lower = context.displacement.low + 0.50 * move
            upper = context.displacement.low + 0.62 * move
        return FairValueGap(
            symbol=symbol,
            timeframe=timeframe,
            direction=context.direction,
            lower=min(lower, upper),
            upper=max(lower, upper),
            formed_at=context.displacement.candle_time,
            candle1_time=context.displacement.candle_time,
            candle3_time=market_time,
        )

    @staticmethod
    def _entry_candidates(context: PendingMomentumScalp, zone: FairValueGap):
        move = context.displacement.high - context.displacement.low
        if context.direction == "bullish":
            fib50 = context.displacement.high - 0.50 * move
            fib62 = context.displacement.high - 0.62 * move
        else:
            fib50 = context.displacement.low + 0.50 * move
            fib62 = context.displacement.low + 0.62 * move
        return [
            ("MICRO_FVG_MID", zone.midpoint),
            ("SHALLOW_50", fib50),
            ("SHALLOW_62", fib62),
        ]

    def _build_setup(self, symbol, timeframe, candles, context: PendingMomentumScalp):
        latest = candles[-1]
        fvg = self._fresh_fvg(candles, context)
        synthetic = False
        if fvg is None:
            # A very strong impulse may not leave a textbook three-candle FVG.
            # In that case the 50-62% micro pullback zone is allowed only because
            # the arming impulse already passed the stricter displacement gate.
            if context.displacement.body_ratio < 1.90 or context.displacement.range_ratio < 1.45:
                return None, "Waiting for a fresh micro FVG or a stronger impulse before using the shallow pullback zone."
            fvg = self._synthetic_pullback_zone(symbol, timeframe, context, latest.close_time)
            synthetic = True

        touched = [
            (name, price)
            for name, price in self._entry_candidates(context, fvg)
            if latest.low <= price <= latest.high
        ]
        if not touched:
            return None, "Impulse armed. Waiting for first 50-62% / micro-FVG pullback instead of chasing."

        for entry_type, raw_entry in touched:
            if context.direction == "bullish":
                raw_stop = min(latest.low, fvg.lower) * 0.99995
                risk = raw_entry - raw_stop
                raw_target = raw_entry + TARGET_RR * risk
                progress = max(0.0, (latest.close - raw_entry) / max(raw_target - raw_entry, 1e-12))
            else:
                raw_stop = max(latest.high, fvg.upper) * 1.00005
                risk = raw_stop - raw_entry
                raw_target = raw_entry - TARGET_RR * risk
                progress = max(0.0, (raw_entry - latest.close) / max(raw_entry - raw_target, 1e-12))
            if risk <= 0 or progress > MAX_SIGNAL_PROGRESS:
                continue

            entry, stop, target = normalize_trade_prices(
                symbol, context.direction, raw_entry, raw_stop, raw_target
            )
            geometry = validate_trade_geometry(symbol, context.direction, entry, stop, target)
            rr = float(geometry.risk_reward or 0.0)
            if not geometry.valid or rr < MIN_RR:
                continue

            htf_direction = str(context.five_minute_regime.get("direction", "neutral"))
            risk_multiplier = 0.30 if htf_direction == "neutral" else 0.40
            setup = StrategySetup(
                setup_id=uuid4().hex[:12],
                symbol=symbol,
                timeframe=timeframe,
                direction=context.direction,
                created_at=latest.close_time,
                pd_array=fvg,
                trigger_type="market_structure_shift",
                trigger_details={
                    "micro_break_level": context.break_level,
                    "five_minute_regime": context.five_minute_regime,
                },
                displacement=context.displacement,
                entry_fvg=fvg,
                entry_price=float(entry),
                stop_price=float(stop),
                target_price=float(target),
                risk_reward=rr,
                metadata={
                    "strategy": "MOMENTUM_SCALP",
                    "operation": "7.2S",
                    "entry_type": entry_type,
                    "synthetic_micro_pullback_zone": synthetic,
                    "risk_multiplier": risk_multiplier,
                    "execution_tier": "MOMENTUM_SCALP_72",
                    "no_chase": True,
                    "signal_target_progress": round(progress, 4),
                    "scalp_context": {
                        "quality_grade": "A",
                        "five_minute_direction": htf_direction,
                        "five_minute_regime": context.five_minute_regime.get("regime"),
                        "body_ratio": round(context.displacement.body_ratio, 3),
                        "range_ratio": round(context.displacement.range_ratio, 3),
                        "micro_break_level": context.break_level,
                        "max_signal_progress": MAX_SIGNAL_PROGRESS,
                        "target_rr": TARGET_RR,
                    },
                    "a_plus_context": {
                        "quality_grade": "A",
                        "quality_score": 82 if htf_direction == context.direction else 78,
                        "context_timeframe": "5m",
                        "higher_timeframe_bias": htf_direction,
                    },
                },
            )
            return setup, (
                f"Momentum scalp ready via {entry_type}: first pullback after micro break, "
                f"{rr:.2f}R, signal progress {progress:.0%}."
            )
        return None, "Pullback touched, but scalp geometry was already too extended or failed the RR floor."

    def on_candle(self, symbol: str, timeframe: str, histories):
        candles = histories.get((symbol, timeframe), [])
        if timeframe != "1m" or not candles:
            return None
        market_time = candles[-1].close_time
        if len(candles) < 22:
            self._diag(symbol, timeframe, market_time, "WARMUP", note=f"Need 1m scalp history ({len(candles)}/22).")
            return None

        key = (symbol, timeframe)
        context = self.contexts.get(key)
        latest = candles[-1]
        if context is not None:
            elapsed = len(candles) - context.started_bar_count
            invalid = (
                latest.close < context.break_level if context.direction == "bullish"
                else latest.close > context.break_level
            )
            if invalid or elapsed > CONTEXT_BARS:
                self.contexts.pop(key, None)
                self._diag(
                    symbol,
                    timeframe,
                    market_time,
                    "EXPIRED",
                    direction=context.direction,
                    displacement=True,
                    structure=True,
                    htf=True,
                    note="Momentum thesis lost the micro break or first-pullback window expired.",
                )
                return None

            setup, reason = self._build_setup(symbol, timeframe, candles, context)
            if setup is None:
                self._diag(
                    symbol,
                    timeframe,
                    market_time,
                    "WAIT_PULLBACK",
                    direction=context.direction,
                    displacement=True,
                    structure=True,
                    htf=True,
                    note=reason,
                )
                return None

            self.contexts.pop(key, None)
            self.last_setup = setup
            self._diag(
                symbol,
                timeframe,
                market_time,
                "SETUP_READY",
                direction=context.direction,
                displacement=True,
                structure=True,
                htf=True,
                pullback=True,
                geometry=True,
                note=reason,
                setup_id=setup.setup_id,
            )
            self.events.append(f"{market_time.strftime('%H:%M:%S')} {symbol} 1m: {reason}")
            self.events = self.events[-24:]
            return setup

        displacement = detect_displacement(
            candles,
            lookback=12,
            body_multiplier=1.55,
            range_multiplier=1.25,
            close_extreme_fraction=0.28,
        )
        if displacement is None:
            self._diag(symbol, timeframe, market_time, "WAIT_IMPULSE", note="Watching for strong 1m displacement through micro structure.")
            return None

        break_level = self._micro_break(candles, displacement.direction)
        if break_level is None:
            self._diag(
                symbol,
                timeframe,
                market_time,
                "WAIT_BREAK",
                direction=displacement.direction,
                displacement=True,
                note="Strong candle detected, but recent 1m micro structure did not break on the close.",
            )
            return None

        htf_ok, regime, htf_reason = self._five_minute_context(
            histories, symbol, market_time, displacement.direction
        )
        if not htf_ok:
            self._diag(
                symbol,
                timeframe,
                market_time,
                "HTF_BLOCKED",
                direction=displacement.direction,
                displacement=True,
                structure=True,
                note=htf_reason,
            )
            return None

        self.contexts[key] = PendingMomentumScalp(
            direction=displacement.direction,
            displacement=displacement,
            break_level=float(break_level),
            started_bar_count=len(candles),
            five_minute_regime=regime,
        )
        self._diag(
            symbol,
            timeframe,
            market_time,
            "WAIT_PULLBACK",
            direction=displacement.direction,
            displacement=True,
            structure=True,
            htf=True,
            note=f"Momentum impulse armed after micro break at {break_level:.2f}. {htf_reason} Waiting first pullback.",
        )
        return None
