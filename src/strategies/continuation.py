from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from uuid import uuid4

from src.risk.geometry import normalize_trade_prices, validate_trade_geometry
from src.strategies.ict import detect_displacement, find_recent_fvgs
from src.strategies.models import StrategySetup
from src.strategies.structure import detect_swings, nearest_target_swing


BAR_SECONDS = {"1m": 60, "5m": 300}
MAX_WATCH_BARS = {"1m": 30, "5m": 14}
MAX_REARMS_PER_THESIS = 2


@dataclass
class ContinuationWatch:
    symbol: str
    timeframe: str
    direction: str
    source_setup_id: str
    source_stop: float
    armed_at: datetime
    rearm_count: int
    stage: str = "WAIT_PULLBACK"
    pullback_at: datetime | None = None
    displacement: object | None = None


class ContinuationRearmEngine:
    """Re-arm a stale directional thesis instead of chasing the missed first entry."""

    def __init__(self, min_rr: float = 1.50) -> None:
        self.min_rr = min_rr
        self.watches: dict[tuple[str, str], ContinuationWatch] = {}
        self.diagnostics: dict[tuple[str, str], dict] = {}
        self.last_setup = None
        self.events: list[str] = []

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def max_watch_seconds(self, timeframe: str) -> int:
        return BAR_SECONDS.get(timeframe, 60) * MAX_WATCH_BARS.get(timeframe, 20)

    def clear_symbol(self, symbol: str) -> None:
        for key in [k for k in self.watches if k[0] == symbol]:
            self.watches.pop(key, None)
        for key in [k for k in self.diagnostics if k[0] == symbol]:
            self.diagnostics.pop(key, None)
        if self.last_setup and self.last_setup.symbol == symbol:
            self.last_setup = None

    def diagnostic(self, symbol: str, timeframe: str):
        item = self.diagnostics.get((symbol, timeframe))
        return dict(item) if item else None

    def _diag(
        self,
        symbol,
        timeframe,
        market_time,
        stage,
        direction=None,
        pullback=False,
        displacement=False,
        fvg=False,
        rr=False,
        note="",
        setup_id=None,
    ):
        item = {
            "symbol": symbol,
            "timeframe": timeframe,
            "market_time": market_time.isoformat(),
            "stage": stage,
            "direction": direction,
            "pd_array": True,
            "signal": bool(pullback),
            "displacement": bool(displacement),
            "entry_fvg": bool(fvg),
            "retracement": bool(pullback),
            "rr": bool(rr),
            "trigger_type": "market_structure_shift",
            "note": f"CONTINUATION · {note}",
            "setup_id": setup_id,
            "checklist_score": sum(int(v) for v in (True, pullback, displacement, fvg, pullback, rr)),
            "checklist_total": 6,
            "strategy_name": "TREND_CONTINUATION_REARM",
        }
        self.diagnostics[(symbol, timeframe)] = item
        return item

    def arm_from_stale(self, setup, event_time) -> bool:
        if setup.timeframe not in BAR_SECONDS:
            return False
        prior_count = int(setup.metadata.get("continuation_rearm_count", 0) or 0)
        if prior_count >= MAX_REARMS_PER_THESIS:
            return False
        if isinstance(event_time, str):
            event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        event_time = self._aware(event_time)
        key = (setup.symbol, setup.timeframe)
        self.watches[key] = ContinuationWatch(
            symbol=setup.symbol,
            timeframe=setup.timeframe,
            direction=setup.direction,
            source_setup_id=setup.setup_id,
            source_stop=float(setup.stop_price),
            armed_at=event_time,
            rearm_count=prior_count,
        )
        self.events.append(
            f"{event_time.strftime('%H:%M:%S')} {setup.symbol} {setup.timeframe}: "
            f"stale {setup.direction} entry converted to continuation watch"
        )
        self.events = self.events[-24:]
        return True

    def _expired_or_invalid(self, watch: ContinuationWatch, latest) -> str | None:
        age = (self._aware(latest.close_time) - watch.armed_at).total_seconds()
        if age > self.max_watch_seconds(watch.timeframe):
            return "Continuation watch expired before a fresh pullback/resumption formed."
        if watch.direction == "bearish" and latest.close >= watch.source_stop:
            return "Bearish thesis invalidated by a close back through the original stop."
        if watch.direction == "bullish" and latest.close <= watch.source_stop:
            return "Bullish thesis invalidated by a close back through the original stop."
        return None

    @staticmethod
    def _pullback_present(candles, watch: ContinuationWatch) -> bool:
        after = [c for c in candles if c.close_time >= watch.armed_at]
        recent = after[-6:]
        if len(recent) < 2:
            return False
        avg_range = mean(max(c.range, 1e-12) for c in recent)
        if watch.direction == "bearish":
            counter = any(c.direction == "bullish" for c in recent)
            rebound = max(c.high for c in recent) - min(c.low for c in recent)
        else:
            counter = any(c.direction == "bearish" for c in recent)
            rebound = max(c.high for c in recent) - min(c.low for c in recent)
        return counter and rebound >= 0.75 * avg_range

    @staticmethod
    def _resumption_break(candles, direction: str) -> bool:
        if len(candles) < 5:
            return False
        latest = candles[-1]
        prior = candles[-5:-1]
        if direction == "bearish":
            return latest.close < min(c.low for c in prior)
        return latest.close > max(c.high for c in prior)

    @staticmethod
    def _ote_79(displacement, direction: str) -> float:
        move = displacement.high - displacement.low
        if direction == "bullish":
            return displacement.high - 0.79 * move
        return displacement.low + 0.79 * move

    def _build_setup(self, candles, watch: ContinuationWatch, fvg):
        latest = candles[-1]
        candidates = [
            ("FVG_MIDPOINT", fvg.midpoint),
            ("OTE_79", self._ote_79(watch.displacement, watch.direction)),
        ]
        swings = detect_swings(candles[-40:])

        for entry_type, raw_entry in candidates:
            if watch.direction == "bearish" and raw_entry <= latest.close:
                continue
            if watch.direction == "bullish" and raw_entry >= latest.close:
                continue

            if watch.direction == "bearish":
                highs = [s.price for s in swings if s.kind == "high" and s.price > raw_entry]
                stop_anchor = highs[-1] if highs else max(c.high for c in candles[-8:])
                raw_stop = stop_anchor * 1.0001
            else:
                lows = [s.price for s in swings if s.kind == "low" and s.price < raw_entry]
                stop_anchor = lows[-1] if lows else min(c.low for c in candles[-8:])
                raw_stop = stop_anchor * 0.9999

            target_swing = nearest_target_swing(candles, watch.direction, raw_entry)
            if target_swing:
                raw_target = target_swing.price
            elif watch.direction == "bearish":
                levels = [c.low for c in candles[-40:] if c.low < latest.close]
                raw_target = min(levels) if levels else None
            else:
                levels = [c.high for c in candles[-40:] if c.high > latest.close]
                raw_target = max(levels) if levels else None
            if raw_target is None:
                continue

            if watch.direction == "bearish" and raw_target >= latest.close:
                continue
            if watch.direction == "bullish" and raw_target <= latest.close:
                continue

            entry, stop, target = normalize_trade_prices(
                watch.symbol, watch.direction, raw_entry, raw_stop, raw_target
            )
            geometry = validate_trade_geometry(
                watch.symbol, watch.direction, entry, stop, target
            )
            rr = float(geometry.risk_reward or 0.0) if geometry.valid else 0.0
            if not geometry.valid or rr < self.min_rr:
                continue

            setup = StrategySetup(
                setup_id=uuid4().hex[:12],
                symbol=watch.symbol,
                timeframe=watch.timeframe,
                direction=watch.direction,
                created_at=latest.close_time,
                pd_array=fvg,
                trigger_type="market_structure_shift",
                trigger_details={
                    "source": "stale_move_continuation",
                    "source_setup_id": watch.source_setup_id,
                },
                displacement=watch.displacement,
                entry_fvg=fvg,
                entry_price=float(entry),
                stop_price=float(stop),
                target_price=float(target),
                risk_reward=rr,
                metadata={
                    "strategy": "TREND_CONTINUATION_REARM",
                    "operation": 6.2,
                    "source_stale_setup_id": watch.source_setup_id,
                    "continuation_rearm_count": watch.rearm_count + 1,
                    "prearmed_limit": True,
                    "entry_type": entry_type,
                    "no_chase": True,
                    "risk_multiplier": 0.55,
                    "setup_quality": "STALE_THESIS_CONTINUATION",
                },
            )
            return setup
        return None

    def on_candle(self, symbol: str, timeframe: str, histories):
        if timeframe not in BAR_SECONDS:
            return None
        key = (symbol, timeframe)
        watch = self.watches.get(key)
        candles = histories.get(key, [])
        if not watch or len(candles) < 8:
            return None
        latest = candles[-1]

        invalid_reason = self._expired_or_invalid(watch, latest)
        if invalid_reason:
            self.watches.pop(key, None)
            self._diag(
                symbol, timeframe, latest.close_time, "EXPIRED",
                watch.direction, note=invalid_reason
            )
            return None

        if watch.stage == "WAIT_PULLBACK":
            if not self._pullback_present(candles, watch):
                self._diag(
                    symbol, timeframe, latest.close_time, "WAIT_PULLBACK",
                    watch.direction,
                    note="Original entry went stale. Holding the thesis and waiting for a countertrend pullback.",
                )
                return None
            watch.stage = "WAIT_RESUMPTION"
            watch.pullback_at = latest.close_time
            self._diag(
                symbol, timeframe, latest.close_time, "WAIT_RESUMPTION",
                watch.direction, pullback=True,
                note="Countertrend pullback formed. Waiting for same-direction displacement and structure continuation.",
            )
            return None

        if watch.stage == "WAIT_RESUMPTION":
            displacement = detect_displacement(candles)
            if (
                not displacement
                or displacement.direction != watch.direction
                or displacement.candle_time <= watch.pullback_at
                or displacement.body_ratio < 1.50
                or displacement.range_ratio < 1.30
                or not self._resumption_break(candles, watch.direction)
            ):
                self._diag(
                    symbol, timeframe, latest.close_time, "WAIT_RESUMPTION",
                    watch.direction, pullback=True,
                    note="Pullback is valid, but continuation displacement/structure break has not confirmed yet.",
                )
                return None
            watch.displacement = displacement
            watch.stage = "WAIT_FRESH_FVG"
            self._diag(
                symbol, timeframe, latest.close_time, "WAIT_FRESH_FVG",
                watch.direction, pullback=True, displacement=True,
                note="Continuation displacement confirmed. Pre-arming the next fresh same-direction FVG/OTE limit.",
            )
            return None

        fvgs = [
            fvg
            for fvg in find_recent_fvgs(candles, lookback=12)
            if fvg.direction == watch.direction
            and fvg.formed_at > watch.displacement.candle_time
        ]
        if not fvgs:
            self._diag(
                symbol, timeframe, latest.close_time, "WAIT_FRESH_FVG",
                watch.direction, pullback=True, displacement=True,
                note="Continuation confirmed. Waiting for a fresh FVG to pre-arm instead of chasing.",
            )
            return None

        setup = self._build_setup(candles, watch, fvgs[-1])
        if not setup:
            self._diag(
                symbol, timeframe, latest.close_time, "WAIT_VALID_REARM",
                watch.direction, pullback=True, displacement=True, fvg=True,
                note=f"Fresh continuation FVG exists, but renewed geometry is below {self.min_rr:.2f}R or the entry would chase.",
            )
            return None

        self.watches.pop(key, None)
        self.last_setup = setup
        self._diag(
            symbol, timeframe, latest.close_time, "SETUP_READY",
            watch.direction, pullback=True, displacement=True, fvg=True, rr=True,
            note=(
                f"Pre-armed continuation limit via {setup.metadata['entry_type']} "
                f"at {setup.risk_reward:.2f}R after stale first entry."
            ),
            setup_id=setup.setup_id,
        )
        self.events.append(
            f"{latest.close_time.strftime('%H:%M:%S')} {symbol} {timeframe}: "
            f"continuation re-arm {setup.direction} {setup.risk_reward:.2f}R"
        )
        self.events = self.events[-24:]
        return setup
