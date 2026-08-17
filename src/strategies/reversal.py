from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.strategies.ict import detect_displacement, detect_liquidity_sweep, detect_smt, find_recent_fvgs
from src.strategies.models import StrategySetup
from src.strategies.regime import classify_regime
from src.strategies.structure import detect_swings, nearest_target_swing

BAR_SECONDS = {"5m": 300, "15m": 900}
FRESHNESS_BARS = {"5m": 4, "15m": 5}


@dataclass
class PendingReversal:
    direction: str
    displacement: object
    break_level: float
    stop_anchor: float
    trigger_type: str
    trigger_details: dict
    started_bar_count: int
    regime_before: dict


class ReversalEngine:
    """MSS/CHOCH reversal detector that waits for a first pullback instead of chasing."""

    def __init__(self, min_rr: float = 1.5) -> None:
        self.min_rr = min_rr
        self.contexts = {}
        self.diagnostics = {}
        self.events = []
        self.last_setup = None

    def clear_symbol(self, symbol: str) -> None:
        for key in [k for k in self.contexts if k[0] == symbol]:
            self.contexts.pop(key, None)
        for key in [k for k in self.diagnostics if k[0] == symbol]:
            self.diagnostics.pop(key, None)
        if self.last_setup and self.last_setup.symbol == symbol:
            self.last_setup = None

    def diagnostic(self, symbol: str, timeframe: str):
        value = self.diagnostics.get((symbol, timeframe))
        return dict(value) if value else None

    def _diag(self, symbol, timeframe, market_time, stage, direction=None, mss=False,
              displacement=False, entry_fvg=False, retracement=False, rr=False,
              trigger_type=None, note="", setup_id=None):
        self.diagnostics[(symbol, timeframe)] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "market_time": market_time.isoformat(),
            "stage": stage,
            "direction": direction,
            "pd_array": bool(mss),
            "signal": bool(mss),
            "displacement": bool(displacement),
            "entry_fvg": bool(entry_fvg),
            "retracement": bool(retracement),
            "rr": bool(rr),
            "trigger_type": trigger_type,
            "note": f"REVERSAL · {note}",
            "setup_id": setup_id,
            "checklist_score": sum(int(v) for v in (mss, mss, displacement, entry_fvg, retracement, rr)),
            "checklist_total": 6,
        }

    @staticmethod
    def _pair_smt(symbol, timeframe, histories, direction):
        if symbol not in {"NQ", "ES"}:
            return None
        pair = "ES" if symbol == "NQ" else "NQ"
        own = histories.get((symbol, timeframe), [])
        other = histories.get((pair, timeframe), [])
        for first, second in ((own, other), (other, own)):
            if not first or not second:
                continue
            smt = detect_smt(first, second)
            if smt and smt.direction == direction:
                return smt
        return None

    @staticmethod
    def _structure_break(candles, direction):
        swings = detect_swings(candles[:-1])
        if direction == "bearish":
            broken = next((s for s in reversed(swings) if s.kind == "low"), None)
            stop_swing = next((s for s in reversed(swings) if s.kind == "high"), None)
            if not broken or candles[-1].close >= broken.price:
                return None
            return broken.price, max(candles[-1].high, stop_swing.price if stop_swing else candles[-1].high)
        broken = next((s for s in reversed(swings) if s.kind == "high"), None)
        stop_swing = next((s for s in reversed(swings) if s.kind == "low"), None)
        if not broken or candles[-1].close <= broken.price:
            return None
        return broken.price, min(candles[-1].low, stop_swing.price if stop_swing else candles[-1].low)

    def _catalyst(self, symbol, timeframe, candles, histories, direction):
        smt = self._pair_smt(symbol, timeframe, histories, direction)
        if smt:
            return "smt", {"leader": smt.leader, "laggard": smt.laggard, "description": smt.description}
        for end in range(len(candles), max(6, len(candles) - 4) - 1, -1):
            sweep = detect_liquidity_sweep(candles[:end])
            if sweep and sweep.direction == direction:
                return "liquidity_sweep", {"swept_level": sweep.swept_level, "swing_time": sweep.swing_time.isoformat()}
        return "market_structure_shift", {}

    @staticmethod
    def _ote(context, retracement):
        move = context.displacement.high - context.displacement.low
        if context.direction == "bullish":
            return context.displacement.high - retracement * move
        return context.displacement.low + retracement * move

    def _fresh_fvg(self, candles, context, timeframe):
        for fvg in reversed(find_recent_fvgs(candles, lookback=16)):
            if fvg.direction != context.direction or fvg.formed_at < context.displacement.candle_time:
                continue
            age = (candles[-1].close_time - fvg.formed_at).total_seconds() / BAR_SECONDS[timeframe]
            if age <= FRESHNESS_BARS[timeframe]:
                return fvg
        return None

    @staticmethod
    def _target(candles, direction, entry):
        swing = nearest_target_swing(candles, direction, entry)
        if swing:
            return swing.price
        if direction == "bearish":
            levels = [c.low for c in candles[-30:] if c.low < entry]
            return min(levels) if levels else None
        levels = [c.high for c in candles[-30:] if c.high > entry]
        return max(levels) if levels else None

    def _build_setup(self, symbol, timeframe, candles, context, fvg):
        latest = candles[-1]
        candidates = [
            ("FVG_MIDPOINT", fvg.midpoint),
            ("OTE_70_5", self._ote(context, 0.705)),
            ("OTE_79", self._ote(context, 0.79)),
        ]
        touched = [(name, price) for name, price in candidates if latest.low <= price <= latest.high]
        if not touched:
            return None, "No approved pullback level touched; waiting instead of chasing."

        for entry_type, raw_entry in touched:
            raw_stop = context.stop_anchor * (1.0001 if context.direction == "bearish" else 0.9999)
            raw_target = self._target(candles, context.direction, raw_entry)
            if raw_target is None:
                continue
            entry, stop, target = normalize_trade_prices(symbol, context.direction, raw_entry, raw_stop, raw_target)
            geometry = validate_trade_geometry(symbol, context.direction, entry, stop, target)
            if not geometry.valid or float(geometry.risk_reward or 0) < self.min_rr:
                continue
            rr = float(geometry.risk_reward)
            setup = StrategySetup(
                setup_id=uuid4().hex[:12], symbol=symbol, timeframe=timeframe,
                direction=context.direction, created_at=latest.close_time,
                pd_array=fvg, trigger_type=context.trigger_type,
                trigger_details=context.trigger_details, displacement=context.displacement,
                entry_fvg=fvg, entry_price=float(entry), stop_price=float(stop),
                target_price=float(target), risk_reward=rr,
                metadata={
                    "strategy": "MSS_REVERSAL", "operation": 5.8,
                    "entry_type": entry_type,
                    "entry_priority": ["FVG_MIDPOINT", "OTE_70_5", "OTE_79"],
                    "mss_break_level": context.break_level,
                    "regime_before": context.regime_before,
                    "no_chase": True,
                    "freshness_bars": FRESHNESS_BARS[timeframe],
                    "risk_multiplier": 0.75,
                },
            )
            return setup, f"First-pullback reversal ready via {entry_type} at {rr:.2f}R."
        return None, "Pullback touched, but stop/target geometry did not clear the 1.50R reversal floor."

    def on_candle(self, symbol, timeframe, histories):
        candles = histories.get((symbol, timeframe), [])
        if timeframe not in BAR_SECONDS or not candles:
            return None
        market_time = candles[-1].close_time
        if len(candles) < 22:
            self._diag(symbol, timeframe, market_time, "WARMUP", note=f"Need reversal history ({len(candles)}/22).")
            return None

        key = (symbol, timeframe)
        context = self.contexts.get(key)
        latest = candles[-1]
        if context:
            elapsed = len(candles) - context.started_bar_count
            invalid = latest.close > context.stop_anchor if context.direction == "bearish" else latest.close < context.stop_anchor
            if invalid or elapsed > FRESHNESS_BARS[timeframe] + 1:
                self.contexts.pop(key, None)
                self._diag(symbol, timeframe, market_time, "EXPIRED", context.direction, True, True,
                           trigger_type=context.trigger_type,
                           note="Reversal invalidated or first-pullback window expired.")
                return None
            fvg = self._fresh_fvg(candles, context, timeframe)
            if not fvg:
                self._diag(symbol, timeframe, market_time, "WAIT_ENTRY_FVG", context.direction, True, True,
                           trigger_type=context.trigger_type,
                           note="MSS + displacement passed. Waiting for fresh same-direction FVG.")
                return None
            setup, reason = self._build_setup(symbol, timeframe, candles, context, fvg)
            if not setup:
                self._diag(symbol, timeframe, market_time, "WAIT_PULLBACK", context.direction, True, True, True,
                           trigger_type=context.trigger_type, note=reason)
                return None
            self.contexts.pop(key, None)
            self.last_setup = setup
            self._diag(symbol, timeframe, market_time, "SETUP_READY", context.direction, True, True, True, True, True,
                       context.trigger_type, reason, setup.setup_id)
            self.events.append(f"{market_time.strftime('%H:%M:%S')} {symbol} {timeframe}: {reason}")
            self.events = self.events[-24:]
            return setup

        displacement = detect_displacement(candles)
        if not displacement or displacement.body_ratio < 1.65 or displacement.range_ratio < 1.35:
            self._diag(symbol, timeframe, market_time, "WAIT_REVERSAL",
                       note="Watching for MSS/CHOCH plus displacement.")
            return None
        structure = self._structure_break(candles, displacement.direction)
        if not structure:
            self._diag(symbol, timeframe, market_time, "WAIT_REVERSAL", displacement.direction,
                       displacement=True,
                       note="Strong displacement detected, but a confirmed swing has not broken.")
            return None
        break_level, stop_anchor = structure
        trigger_type, trigger_details = self._catalyst(symbol, timeframe, candles, histories, displacement.direction)
        if trigger_type == "market_structure_shift" and (displacement.body_ratio < 2.0 or displacement.range_ratio < 1.55):
            self._diag(symbol, timeframe, market_time, "WAIT_REVERSAL", displacement.direction, True, True,
                       trigger_type=trigger_type,
                       note="MSS without sweep/SMT needs >=2.00x body and >=1.55x range impulse.")
            return None
        self.contexts[key] = PendingReversal(
            direction=displacement.direction, displacement=displacement,
            break_level=float(break_level), stop_anchor=float(stop_anchor),
            trigger_type=trigger_type, trigger_details=trigger_details,
            started_bar_count=len(candles), regime_before=classify_regime(candles[:-1]),
        )
        self._diag(symbol, timeframe, market_time, "WAIT_ENTRY_FVG", displacement.direction, True, True,
                   trigger_type=trigger_type,
                   note=f"{displacement.direction.title()} MSS broke {break_level:.2f}. No chase: waiting for first pullback.")
        return None


def _grade(score):
    return "A+" if score >= 90 else "A" if score >= 80 else "B+" if score >= 72 else "B" if score >= 60 else "RESEARCH"


def evaluate_reversal_context(setup, histories):
    context_tf = "1h" if setup.timeframe == "15m" else "30m"
    context_history = [c for c in histories.get((setup.symbol, context_tf), []) if c.close_time <= setup.created_at]
    context_regime = classify_regime(context_history)
    htf_direction = context_regime.get("direction", "neutral")
    conflict = htf_direction not in {"neutral", setup.direction}
    body = float(getattr(setup.displacement, "body_ratio", 0) or 0)
    candle_range = float(getattr(setup.displacement, "range_ratio", 0) or 0)
    trigger = str(setup.trigger_type or "market_structure_shift").lower()
    rr = float(setup.risk_reward or 0)
    components = {
        "mss_structure_break": 20,
        "displacement": 20 if body >= 2.0 and candle_range >= 1.55 else 16,
        "catalyst": 15 if trigger == "smt" else 13 if trigger == "liquidity_sweep" else 10,
        "first_pullback_freshness": 12,
        "higher_timeframe_context": 18 if htf_direction == setup.direction else 11 if htf_direction == "neutral" else 3,
        "target_room": min(15, max(0, round(rr * 5))),
    }
    score = min(100, int(sum(components.values())))
    grade = _grade(score)
    details = {
        "profile": "REVERSAL_INTELLIGENCE_5_8", "quality_score": score,
        "quality_grade": grade, "score_components": components,
        "context_timeframe": context_tf, "higher_timeframe_bias": htf_direction,
        "higher_timeframe_regime": context_regime.get("regime"),
        "narrative_conflict": conflict, "displacement_body_ratio": body,
        "displacement_range_ratio": candle_range, "entry_type": setup.metadata.get("entry_type"),
    }
    if score < 72:
        return False, f"Reversal Intelligence score {score}/100 ({grade}); below B+ floor.", details
    if conflict and score < 80:
        return False, f"Countertrend reversal scored {score}/100 ({grade}); require A-grade 80+ against {context_tf}.", details
    return True, f"Reversal Intelligence score {score}/100 ({grade}); first-pullback reversal eligible.", details
