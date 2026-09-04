from __future__ import annotations

from uuid import uuid4

from src.strategies.gold_momentum72r import GoldMomentumPullbackEngine72R
from src.strategies.ict import find_recent_fvgs
from src.strategies.models import StrategySetup


VERIFY_MODES = {"VERIFY", "VERIFICATION", "TEST"}


class CandidateCollector80:
    """Collect raw strategy candidates without legacy collision deduplication."""

    def __init__(self, engine, *, continuation=None) -> None:
        self.engine = engine
        self.continuation = continuation
        self._early_arm_seen: set[tuple] = set()

    def _momentum(self) -> GoldMomentumPullbackEngine72R:
        momentum = getattr(self.engine, "_gold_momentum_pullback_72r", None)
        if momentum is None:
            momentum = GoldMomentumPullbackEngine72R()
            setattr(self.engine, "_gold_momentum_pullback_72r", momentum)
        return momentum

    @staticmethod
    def _annotate(setup, strategy: str, *, source: str = "CANDLE_CLOSE"):
        if setup is None:
            return None
        setup.metadata.setdefault("strategy", strategy)
        setup.metadata.setdefault("candidate_source_80", source)
        if strategy == "ICT_CONFLUENCE":
            setup.metadata.setdefault("checklist_total", 6)
            setup.metadata.setdefault("checklist_score", 6)
        return setup

    def _early_arm_candidate(self, symbol: str, timeframe: str, histories):
        """Turn a Gold 7.2H pre-arm into a chart preview or reduced-risk 8.0 candidate.

        Four-of-six arms are visualization only. A five-of-six or better arm can
        enter the normal Operation 8.0 pipeline only when a fresh post-displacement
        same-direction FVG still exists. Session, quality, arbiter, account, paper,
        no-chase and broker safety remain downstream and authoritative.
        """
        if str(symbol).upper() != "GC":
            return None

        ict_engine = getattr(self.engine, "ict", None)
        planner = getattr(ict_engine, "_early_entry_planner_72h", None)
        if planner is None:
            return None

        key = (symbol, timeframe)
        arm = getattr(planner, "arms", {}).get(key)
        context = getattr(ict_engine, "contexts", {}).get(key)
        candles = histories.get(key, [])
        if not arm or context is None or not candles:
            return None

        score = int(arm.get("checklist_score") or 0)
        if score < 4:
            return None
        if str(arm.get("direction") or "") != str(getattr(context, "direction", "")):
            return None

        displacement = getattr(context, "displacement", None)
        if displacement is None or getattr(context, "pd_array", None) is None:
            return None

        recent_fvgs = find_recent_fvgs(candles, lookback=20)
        fresh_entry_fvg = next(
            (
                fvg
                for fvg in reversed(recent_fvgs)
                if fvg.direction == context.direction
                and fvg.formed_at > displacement.candle_time
            ),
            None,
        )
        display_fvg = fresh_entry_fvg or context.pd_array

        age_bars = int(arm.get("age_bars") or 0)
        rr = float(arm.get("risk_reward") or 0.0)
        executable = (
            score >= 5
            and fresh_entry_fvg is not None
            and age_bars <= int(getattr(planner, "MAX_PRIORITY_AGE_BARS", 6))
            and rr >= float(getattr(planner, "MIN_PREARM_RR", 1.50))
        )
        state = "EXECUTABLE" if executable else "PREVIEW"
        signature = (
            state,
            symbol,
            timeframe,
            str(arm.get("direction")),
            round(float(arm.get("entry") or 0.0), 4),
            round(float(arm.get("stop") or 0.0), 4),
            round(float(arm.get("target") or 0.0), 4),
            score,
        )
        if signature in self._early_arm_seen:
            return None
        self._early_arm_seen.add(signature)

        retracement = float(arm.get("retracement_fraction") or 1.0)
        risk_cap = 0.45 if retracement < float(getattr(planner, "OTE_MIN", 0.705)) else 0.55
        metadata = {
            "strategy": "ICT_CONFLUENCE",
            "candidate_source_80": "EARLY_ARM_72H",
            "operation": 8.0,
            "checklist_score": score,
            "checklist_total": int(arm.get("checklist_total") or 6),
            "entry_type": str(arm.get("entry_type") or "EARLY_OTE"),
            "entry_rule": (
                "Operation 8.0 converts a fresh 7.2H Gold pre-arm at 5/6+ into "
                "the normal decision pipeline; 4/6 remains chart-preview only."
            ),
            "no_chase": True,
            "preview_only_80": not executable,
            "execution_tier": (
                "EARLY_ARM_A_REDUCED_80" if executable else "EARLY_ARM_PREVIEW_80"
            ),
            "risk_multiplier": risk_cap if executable else 0.0,
            "entry_risk_cap": risk_cap if executable else 0.0,
            "setup_quality": "A_EARLY_ARM",
            "early_entry_arm_72h": {
                **dict(arm),
                "state": (
                    "PIPELINE_CANDIDATE_80" if executable else "CHART_PREVIEW_80"
                ),
                "executable": bool(executable),
                "risk_cap": risk_cap if executable else 0.0,
            },
        }

        return StrategySetup(
            setup_id=uuid4().hex[:12],
            symbol=symbol,
            timeframe=timeframe,
            direction=context.direction,
            created_at=candles[-1].close_time,
            pd_array=context.pd_array,
            trigger_type=context.trigger_type,
            trigger_details=context.trigger_details or {},
            displacement=displacement,
            entry_fvg=display_fvg,
            entry_price=float(arm["entry"]),
            stop_price=float(arm["stop"]),
            target_price=float(arm["target"]),
            risk_reward=rr,
            status="PENDING" if executable else "PRE_ARMED",
            metadata=metadata,
        )

    def collect(self, symbol: str, timeframe: str, histories, mode: str) -> list:
        candidates = []

        ict = self._annotate(
            self.engine.ict.on_candle(symbol, timeframe, histories),
            "ICT_CONFLUENCE",
        )
        if ict is not None:
            candidates.append(ict)

        rejection = self._annotate(
            self.engine.rejection_block.on_candle(symbol, timeframe, histories),
            "REJECTION_BLOCK_10_10",
        )
        if rejection is not None:
            candidates.append(rejection)

        reversal_engine = getattr(self.engine, "reversal", None)
        if reversal_engine is not None:
            reversal = self._annotate(
                reversal_engine.on_candle(symbol, timeframe, histories),
                "MSS_REVERSAL",
            )
            if reversal is not None:
                candidates.append(reversal)

        # Preserve the established stale-thesis continuation before trying the
        # newer momentum-recognition fallback.
        if not candidates and self.continuation is not None:
            continuation = self._annotate(
                self.continuation.on_candle(symbol, timeframe, histories),
                "TREND_CONTINUATION_REARM",
            )
            if continuation is not None:
                candidates.append(continuation)

        normalized_mode = str(mode or "").strip().upper()
        if (
            not candidates
            and normalized_mode in VERIFY_MODES
            and symbol == "GC"
            and timeframe in {"5m", "15m"}
        ):
            momentum = self._momentum()
            setup = self._annotate(
                momentum.on_candle(symbol, timeframe, histories, normalized_mode),
                "GOLD_MOMENTUM_PULLBACK_72R",
            )
            diagnostic = momentum.diagnostic(symbol, timeframe)
            if diagnostic and diagnostic.get("stage") in {"WAIT_ENTRY_FVG", "WAIT_PULLBACK", "SETUP_READY"}:
                self.engine.diagnostics[(symbol, timeframe)] = diagnostic
            if setup is not None:
                candidates.append(setup)

        # Persist the 7.2H plan through the same setup store so the dashboard can
        # draw confluence geometry and ENTRY / SL / TP before execution. A preview
        # never executes; 5/6+ fresh arms are reduced-risk candidates.
        if ict is None:
            early = self._early_arm_candidate(symbol, timeframe, histories)
            if early is not None:
                candidates.append(early)

        try:
            self.engine._refresh_diagnostic(symbol, timeframe)
            self.engine._refresh_events()
        except Exception:
            pass
        if candidates:
            non_preview = [
                setup for setup in candidates
                if not bool(setup.metadata.get("preview_only_80"))
            ]
            self.engine.last_setup = (non_preview or candidates)[-1]
        return candidates
