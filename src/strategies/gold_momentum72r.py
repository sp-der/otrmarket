from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.strategies.execution_quality import PRIMARY_CONTEXT_TIMEFRAME, _structure_bias
from src.strategies.ict import detect_displacement, detect_liquidity_sweep, find_recent_fvgs
from src.strategies.models import FairValueGap, StrategySetup
from src.strategies.structure import detect_swings, nearest_target_swing


VERIFY_MODES = {"VERIFY", "VERIFICATION", "TEST"}
SUPPORTED_TIMEFRAMES = {"5m", "15m"}
BAR_SECONDS = {"5m": 300, "15m": 900}
CONTEXT_MAX_BARS = {"5m": 5, "15m": 3}
FVG_MAX_AGE_BARS = {"5m": 2.0, "15m": 2.0}


@dataclass
class PendingGoldMomentum:
    direction: str
    displacement: object
    trigger_type: str
    trigger_details: dict
    stop_anchor: float
    started_bar_count: int
    started_at: datetime
    context_timeframe: str
    context_bias: str


class GoldMomentumPullbackEngine72R:
    """Catch strong GC legs the legacy PD-array-first lane can miss.

    Narrow by design: VERIFY only, GC only, 5m/15m only, aligned primary HTF
    context, sweep or swing-break catalyst, and no impulse chasing. A candidate
    appears only after a fresh same-direction FVG/OTE first pullback. Normal OTR
    quality, cooldown, exposure, geometry and execution gates remain downstream.
    """

    def __init__(self, min_rr: float = 1.5) -> None:
        self.min_rr = float(min_rr)
        self.contexts: dict[tuple[str, str], PendingGoldMomentum] = {}
        self.diagnostics: dict[tuple[str, str], dict] = {}
        self.events: list[str] = []
        self.last_setup = None

    def clear_symbol(self, symbol: str) -> None:
        for key in [key for key in self.contexts if key[0] == symbol]:
            self.contexts.pop(key, None)
        for key in [key for key in self.diagnostics if key[0] == symbol]:
            self.diagnostics.pop(key, None)
        if self.last_setup and getattr(self.last_setup, "symbol", None) == symbol:
            self.last_setup = None

    def diagnostic(self, symbol: str, timeframe: str):
        item = self.diagnostics.get((symbol, timeframe))
        return dict(item) if item else None

    def _diag(self, symbol, timeframe, market_time, stage, *, direction=None,
              displacement=False, entry_fvg=False, retracement=False, rr=False,
              trigger_type=None, note="", setup_id=None):
        item = {
            "symbol": symbol,
            "timeframe": timeframe,
            "market_time": market_time.isoformat(),
            "stage": stage,
            "direction": direction,
            "pd_array": bool(displacement),
            "signal": bool(trigger_type),
            "displacement": bool(displacement),
            "entry_fvg": bool(entry_fvg),
            "retracement": bool(retracement),
            "rr": bool(rr),
            "trigger_type": trigger_type,
            "note": f"MOMENTUM 7.2R · {note}",
            "setup_id": setup_id,
            "strategy_name": "GOLD_MOMENTUM_PULLBACK_72R",
            "strategy_score": 100 if rr else 83 if retracement else 67 if entry_fvg else 50 if displacement else 0,
        }
        self.diagnostics[(symbol, timeframe)] = item
        return item

    @staticmethod
    def _history_at_or_before(histories, symbol, timeframe, market_time):
        return [c for c in histories.get((symbol, timeframe), []) if c.close_time <= market_time]

    def _primary_bias(self, histories, timeframe, market_time):
        context_tf = PRIMARY_CONTEXT_TIMEFRAME.get(timeframe, timeframe)
        history = self._history_at_or_before(histories, "GC", context_tf, market_time)
        bias, details = _structure_bias(history)
        return context_tf, bias, details

    @staticmethod
    def _recent_sweep(candles, direction, lookback: int = 3):
        start = max(6, len(candles) - lookback + 1)
        for end in range(len(candles), start - 1, -1):
            sweep = detect_liquidity_sweep(candles[:end])
            if sweep and sweep.direction == direction:
                return sweep
        return None

    @staticmethod
    def _structure_break(candles, direction):
        if len(candles) < 7:
            return None
        swings = detect_swings(candles[:-1])
        latest = candles[-1]
        if direction == "bullish":
            broken = next((s for s in reversed(swings) if s.kind == "high"), None)
            stop_swing = next((s for s in reversed(swings) if s.kind == "low"), None)
            if not broken or latest.close <= broken.price:
                return None
            stop_anchor = min(latest.low, stop_swing.price if stop_swing else latest.low)
            return float(broken.price), float(stop_anchor)
        broken = next((s for s in reversed(swings) if s.kind == "low"), None)
        stop_swing = next((s for s in reversed(swings) if s.kind == "high"), None)
        if not broken or latest.close >= broken.price:
            return None
        stop_anchor = max(latest.high, stop_swing.price if stop_swing else latest.high)
        return float(broken.price), float(stop_anchor)

    @staticmethod
    def _ote(context: PendingGoldMomentum, retracement: float) -> float:
        move = context.displacement.high - context.displacement.low
        if context.direction == "bullish":
            return context.displacement.high - retracement * move
        return context.displacement.low + retracement * move

    def _fresh_fvg(self, candles, context, timeframe) -> FairValueGap | None:
        latest_time = candles[-1].close_time
        seconds = BAR_SECONDS[timeframe]
        for fvg in reversed(find_recent_fvgs(candles, lookback=12)):
            if fvg.direction != context.direction or fvg.formed_at <= context.displacement.candle_time:
                continue
            age = (latest_time - fvg.formed_at).total_seconds() / seconds
            if age <= FVG_MAX_AGE_BARS[timeframe]:
                return fvg
        return None

    def _build_setup(self, symbol, timeframe, candles, context, fvg):
        latest = candles[-1]
        candidates = [
            ("FVG_MIDPOINT", fvg.midpoint),
            ("OTE_70_5", self._ote(context, 0.705)),
            ("OTE_79", self._ote(context, 0.79)),
        ]
        touched = [(kind, price) for kind, price in candidates if latest.low <= price <= latest.high]
        if not touched:
            return None, "Strong leg recognized; waiting for the first approved FVG/OTE pullback instead of chasing."

        for entry_type, raw_entry in touched:
            raw_stop = context.stop_anchor * (1.0001 if context.direction == "bearish" else 0.9999)
            target_swing = nearest_target_swing(candles, context.direction, raw_entry)
            if target_swing is None:
                continue
            entry, stop, target = normalize_trade_prices(
                symbol, context.direction, raw_entry, raw_stop, float(target_swing.price)
            )
            geometry = validate_trade_geometry(symbol, context.direction, entry, stop, target)
            rr = float(geometry.risk_reward or 0.0) if geometry.valid else 0.0
            if not geometry.valid or rr < self.min_rr:
                continue
            setup = StrategySetup(
                setup_id=uuid4().hex[:12],
                symbol=symbol,
                timeframe=timeframe,
                direction=context.direction,
                created_at=latest.close_time,
                pd_array=fvg,
                trigger_type=context.trigger_type,
                trigger_details=context.trigger_details,
                displacement=context.displacement,
                entry_fvg=fvg,
                entry_price=float(entry),
                stop_price=float(stop),
                target_price=float(target),
                risk_reward=rr,
                metadata={
                    "strategy": "GOLD_MOMENTUM_PULLBACK_72R",
                    "operation": "7.2R",
                    "entry_type": entry_type,
                    "entry_priority": ["FVG_MIDPOINT", "OTE_70_5", "OTE_79"],
                    "recognition_lane": "ALIGNED_MOMENTUM_FIRST_PULLBACK",
                    "context_timeframe": context.context_timeframe,
                    "context_bias_at_arm": context.context_bias,
                    "no_chase": True,
                    "risk_multiplier": 0.75,
                    "execution_tier": "GOLD_MOMENTUM_REDUCED_72R",
                },
            )
            return setup, f"Aligned Gold momentum first-pullback ready via {entry_type} at {rr:.2f}R."
        return None, f"Pullback arrived, but no structural geometry cleared {self.min_rr:.2f}R."

    def on_candle(self, symbol: str, timeframe: str, histories, trading_mode: str):
        mode = str(trading_mode or "").strip().upper()
        if mode not in VERIFY_MODES or symbol != "GC" or timeframe not in SUPPORTED_TIMEFRAMES:
            return None
        candles = histories.get((symbol, timeframe), [])
        if len(candles) < 22:
            if candles:
                self._diag(symbol, timeframe, candles[-1].close_time, "WARMUP",
                           note=f"Need momentum history ({len(candles)}/22).")
            return None

        market_time = candles[-1].close_time
        key = (symbol, timeframe)
        context = self.contexts.get(key)
        if context is not None:
            elapsed = len(candles) - context.started_bar_count
            context_tf, bias, _ = self._primary_bias(histories, timeframe, market_time)
            invalid = candles[-1].close < context.stop_anchor if context.direction == "bullish" else candles[-1].close > context.stop_anchor
            if elapsed > CONTEXT_MAX_BARS[timeframe] or invalid or bias != context.direction:
                self.contexts.pop(key, None)
                self._diag(symbol, timeframe, market_time, "EXPIRED", direction=context.direction,
                           displacement=True, trigger_type=context.trigger_type,
                           note=f"Momentum pullback context expired/invalidated; {context_tf} bias is {bias}.")
                return None

            fvg = self._fresh_fvg(candles, context, timeframe)
            if fvg is None:
                self._diag(symbol, timeframe, market_time, "WAIT_ENTRY_FVG", direction=context.direction,
                           displacement=True, trigger_type=context.trigger_type,
                           note="Aligned impulse armed. Waiting for a fresh same-direction FVG after displacement.")
                return None
            setup, reason = self._build_setup(symbol, timeframe, candles, context, fvg)
            if setup is None:
                self._diag(symbol, timeframe, market_time, "WAIT_PULLBACK", direction=context.direction,
                           displacement=True, entry_fvg=True, trigger_type=context.trigger_type, note=reason)
                return None

            self.contexts.pop(key, None)
            self.last_setup = setup
            self._diag(symbol, timeframe, market_time, "SETUP_READY", direction=context.direction,
                       displacement=True, entry_fvg=True, retracement=True, rr=True,
                       trigger_type=context.trigger_type, note=reason, setup_id=setup.setup_id)
            self.events.append(f"{market_time.strftime('%H:%M:%S')} {symbol} {timeframe}: {reason}")
            self.events = self.events[-24:]
            return setup

        displacement = detect_displacement(candles)
        if displacement is None or displacement.body_ratio < 1.90 or displacement.range_ratio < 1.50:
            self._diag(symbol, timeframe, market_time, "WAIT_MOMENTUM",
                       note="Watching for strong aligned displacement plus sweep/MSS; no chase.")
            return None
        context_tf, bias, _ = self._primary_bias(histories, timeframe, market_time)
        if bias != displacement.direction:
            self._diag(symbol, timeframe, market_time, "WAIT_CONTEXT", direction=displacement.direction,
                       displacement=True,
                       note=f"Strong displacement detected, but {context_tf} is {bias}; momentum lane requires primary-context alignment.")
            return None

        sweep = self._recent_sweep(candles, displacement.direction)
        structure = self._structure_break(candles, displacement.direction)
        if sweep is None and structure is None:
            self._diag(symbol, timeframe, market_time, "WAIT_CATALYST", direction=displacement.direction,
                       displacement=True,
                       note="Strong aligned displacement detected, but no sweep or confirmed swing break yet.")
            return None
        if sweep is not None:
            trigger_type = "liquidity_sweep"
            trigger_details = {"swept_level": float(sweep.swept_level), "swing_time": sweep.swing_time.isoformat()}
        else:
            if displacement.body_ratio < 2.20 or displacement.range_ratio < 1.65:
                self._diag(symbol, timeframe, market_time, "WAIT_CATALYST", direction=displacement.direction,
                           displacement=True, trigger_type="market_structure_shift",
                           note="MSS-only momentum needs >=2.20x body and >=1.65x range to replace the missing sweep catalyst.")
                return None
            trigger_type = "market_structure_shift"
            trigger_details = {"break_level": float(structure[0])}

        if structure is not None:
            stop_anchor = float(structure[1])
        elif displacement.direction == "bullish":
            stop_anchor = float(displacement.low)
        else:
            stop_anchor = float(displacement.high)
        self.contexts[key] = PendingGoldMomentum(
            direction=displacement.direction,
            displacement=displacement,
            trigger_type=trigger_type,
            trigger_details=trigger_details,
            stop_anchor=stop_anchor,
            started_bar_count=len(candles),
            started_at=market_time,
            context_timeframe=context_tf,
            context_bias=bias,
        )
        self._diag(symbol, timeframe, market_time, "WAIT_ENTRY_FVG", direction=displacement.direction,
                   displacement=True, trigger_type=trigger_type,
                   note=f"Strong {displacement.direction} Gold leg armed with {trigger_type.replace('_', ' ')}; waiting for first fresh FVG/OTE pullback.")
        return None
