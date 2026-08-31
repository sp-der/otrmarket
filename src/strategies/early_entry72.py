from __future__ import annotations

from types import MethodType
from typing import Any, Callable

from src.strategies.ict import detect_fvg


class EarlyEntryPlanner72:
    """Pre-arm geometry for existing ICT ideas without creating a new strategy.

    A pre-arm is deliberately non-executable. It is built only after the
    existing ICT state machine has confirmed PD-array, catalyst, displacement
    and at least one post-displacement entry FVG (4/6 checklist progress).
    When the normal strategy later emits a fully-qualified setup, the planner
    may reuse an earlier, already-validated pullback geometry. All downstream
    session, quality, eval, no-chase and risk gates still run normally.
    """

    MIN_PREARM_RR = 1.50
    STRONG_PREARM_RR = 3.00
    MAX_PRIORITY_AGE_BARS = 6
    SMALL_TRADE_RR = 1.60
    RETRACEMENTS = (0.62, 0.705, 0.79)

    def __init__(self, logger: Callable[[str], None] | None = None) -> None:
        self.logger = logger
        self.arms: dict[tuple[str, str], dict[str, Any]] = {}

    def _log(self, message: str) -> None:
        if self.logger is not None:
            self.logger(message)

    @staticmethod
    def _score(diag: dict | None) -> int:
        if not diag:
            return 0
        keys = ("pd_array", "signal", "displacement", "entry_fvg", "retracement", "rr")
        return sum(int(bool(diag.get(key))) for key in keys)

    @staticmethod
    def _raw_retracement(context, fraction: float) -> float | None:
        displacement = getattr(context, "displacement", None)
        if displacement is None:
            return None
        move_range = float(displacement.high) - float(displacement.low)
        if move_range <= 0:
            return None
        if context.direction == "bullish":
            return float(displacement.high) - (fraction * move_range)
        return float(displacement.low) + (fraction * move_range)

    @staticmethod
    def _fraction_for_price(context, price: float) -> float | None:
        displacement = getattr(context, "displacement", None)
        if displacement is None:
            return None
        move_range = float(displacement.high) - float(displacement.low)
        if move_range <= 0:
            return None
        if context.direction == "bullish":
            return (float(displacement.high) - float(price)) / move_range
        return (float(price) - float(displacement.low)) / move_range

    @staticmethod
    def _pd_signature(pd_array) -> tuple[float | None, float | None, str | None]:
        if pd_array is None:
            return None, None, None
        return (
            float(getattr(pd_array, "lower", 0.0)),
            float(getattr(pd_array, "upper", 0.0)),
            str(getattr(pd_array, "direction", "")),
        )

    def _candidate_rows(self, engine, candles, context) -> list[dict[str, Any]]:
        candidates: list[tuple[str, float, float, dict[str, Any]]] = []

        # If the latest three-candle FVG is already inside the displacement
        # pullback zone, include its midpoint as the most literal early plan.
        fvg = detect_fvg(candles)
        zone = engine._retracement_zone(context)
        if fvg is not None and fvg.direction == context.direction and zone is not None:
            fraction = self._fraction_for_price(context, float(fvg.midpoint))
            zone_low, zone_high = zone
            if (
                fraction is not None
                and 0.50 <= fraction <= 0.79
                and zone_low <= float(fvg.midpoint) <= zone_high
            ):
                candidates.append(
                    (
                        "EARLY_FVG_MIDPOINT",
                        float(fvg.midpoint),
                        float(fraction),
                        {"fvg_lower": float(fvg.lower), "fvg_upper": float(fvg.upper)},
                    )
                )

        # The ladder is intentionally shallower-first. A 62% structural limit
        # gets a chance before 70.5/79 so a fast valid move does not require a
        # deep retracement merely to improve cosmetic R:R.
        for fraction in self.RETRACEMENTS:
            raw = self._raw_retracement(context, fraction)
            if raw is not None:
                candidates.append(
                    (
                        f"EARLY_OTE_{str(fraction).replace('.', '_')}",
                        raw,
                        fraction,
                        {"retracement": fraction},
                    )
                )

        rows: list[dict[str, Any]] = []
        for entry_type, raw_entry, fraction, details in candidates:
            attempt = engine._evaluate_entry_candidate(
                candles,
                context,
                entry_type,
                raw_entry,
                details,
            )
            if not attempt.get("valid"):
                continue
            rr = float(attempt.get("risk_reward") or 0.0)
            if rr < self.MIN_PREARM_RR:
                continue
            row = dict(attempt)
            row["retracement_fraction"] = float(fraction)
            rows.append(row)

        # Prefer the earliest pullback that still gives respectable geometry.
        # If several candidates have the same retracement depth, prefer R:R.
        return sorted(
            rows,
            key=lambda row: (
                float(row.get("retracement_fraction") or 9.0),
                -float(row.get("risk_reward") or 0.0),
            ),
        )

    def refresh(self, engine, symbol: str, timeframe: str, histories) -> dict | None:
        key = (symbol, timeframe)
        candles = histories.get(key, [])
        context = engine.contexts.get(key)
        diag = engine.diagnostics.get(key)

        if not candles or context is None:
            if key in self.arms:
                old = self.arms.pop(key)
                self._log(
                    f"EARLY ENTRY 7.2H cancelled {symbol} {timeframe} {old['direction']}: "
                    "underlying ICT context ended before confirmation."
                )
            return None

        score = self._score(diag)
        eligible_stage = context.stage in {
            "WAIT_ENTRY_FVG",
            "WAIT_QUALIFYING_FVG",
            "WAIT_VALID_RR",
        }
        if (
            not eligible_stage
            or getattr(context, "trigger_type", None) is None
            or getattr(context, "displacement", None) is None
            or not bool(getattr(context, "entry_fvg_seen", False))
            or score < 4
        ):
            return self.arms.get(key)

        rows = self._candidate_rows(engine, candles, context)
        if not rows:
            return self.arms.get(key)

        chosen = rows[0]
        age_bars = max(0, len(candles) - int(getattr(context, "stage_bar_count", len(candles))))
        arm = {
            "state": "PRE_ARMED",
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": context.direction,
            "market_time": candles[-1].close_time.isoformat(),
            "checklist_score": score,
            "checklist_total": 6,
            "stage": context.stage,
            "entry_type": chosen["entry_type"],
            "entry": float(chosen["entry"]),
            "stop": float(chosen["stop"]),
            "target": float(chosen["target"]),
            "risk_reward": float(chosen["risk_reward"]),
            "retracement_fraction": float(chosen["retracement_fraction"]),
            "age_bars": age_bars,
            "pd_signature": self._pd_signature(context.pd_array),
            "trigger_type": context.trigger_type,
            "executable": False,
            "reason": "Geometry prepared early; final strategy/session/quality/eval/no-chase gates are still required.",
        }

        previous = self.arms.get(key)
        self.arms[key] = arm
        if previous is None or any(
            previous.get(field) != arm.get(field)
            for field in ("entry", "stop", "target", "entry_type", "checklist_score")
        ):
            self._log(
                f"EARLY ENTRY 7.2H PRE_ARMED {symbol} {timeframe} {context.direction.upper()} "
                f"{score}/6 via {arm['entry_type']} entry {arm['entry']:.2f} "
                f"at {arm['risk_reward']:.2f}R; no order active yet."
            )

        if diag is not None:
            diag["early_entry_arm"] = dict(arm)
            note = str(diag.get("note", "")).strip()
            suffix = (
                f" PRE-ARMED {arm['entry_type'].replace('_', ' ')} "
                f"{arm['entry']:.2f} at {arm['risk_reward']:.2f}R; awaiting final confirmation."
            )
            if "PRE-ARMED" not in note:
                diag["note"] = (note + suffix).strip()

        return arm

    def promote(self, engine, setup, histories):
        key = (setup.symbol, setup.timeframe)
        arm = self.arms.get(key)
        if arm is None or arm.get("direction") != setup.direction:
            return setup

        if arm.get("pd_signature") != self._pd_signature(setup.pd_array):
            self.arms.pop(key, None)
            return setup

        context = engine.contexts.get(key)
        # super().on_candle removes the context when a setup becomes ready, so
        # derive the final entry depth from the stored displacement on setup.
        displacement = getattr(setup, "displacement", None)
        if displacement is None:
            self.arms.pop(key, None)
            return setup

        move_range = float(displacement.high) - float(displacement.low)
        if move_range <= 0:
            self.arms.pop(key, None)
            return setup
        if setup.direction == "bullish":
            final_fraction = (float(displacement.high) - float(setup.entry_price)) / move_range
        else:
            final_fraction = (float(setup.entry_price) - float(displacement.low)) / move_range

        arm_fraction = float(arm.get("retracement_fraction") or 1.0)
        arm_rr = float(arm.get("risk_reward") or 0.0)
        final_rr = float(setup.risk_reward or 0.0)
        min_promote_rr = max(self.MIN_PREARM_RR, min(2.0, final_rr * 0.75))

        # Only replace final geometry when the prepared level is materially
        # earlier/shallower and still carries enough structural reward. This is
        # an entry-timing improvement, never an excuse to downgrade a premium
        # setup into weak geometry.
        should_promote = (
            arm_fraction + 0.015 < final_fraction
            and arm_rr >= min_promote_rr
        )
        self.arms.pop(key, None)
        if not should_promote:
            setup.metadata["early_entry_arm_72h"] = {
                **arm,
                "state": "CONFIRMED_BUT_FINAL_GEOMETRY_KEPT",
                "final_entry": float(setup.entry_price),
                "final_risk_reward": final_rr,
            }
            return setup

        original = {
            "entry": float(setup.entry_price),
            "stop": float(setup.stop_price),
            "target": float(setup.target_price),
            "risk_reward": final_rr,
            "entry_type": setup.metadata.get("entry_type"),
        }
        setup.entry_price = float(arm["entry"])
        setup.stop_price = float(arm["stop"])
        setup.target_price = float(arm["target"])
        setup.risk_reward = arm_rr
        setup.metadata["entry_type"] = str(arm["entry_type"])
        setup.metadata["entry_rule"] = "Operation 7.2H pre-armed structural pullback activated only after full setup confirmation"
        setup.metadata["early_entry_arm_72h"] = {
            **arm,
            "state": "CONFIRMED_AND_ACTIVATED",
            "original_final_geometry": original,
        }
        setup.metadata["execution_tier"] = "EARLY_ENTRY_CONFIRMED_72H"
        self._log(
            f"EARLY ENTRY 7.2H ACTIVATED {setup.symbol} {setup.timeframe} {setup.direction.upper()} "
            f"entry {setup.entry_price:.2f} at {setup.risk_reward:.2f}R after full confirmation; "
            "all downstream gates remain active."
        )
        return setup

    def capital_priority_reason(self, setup) -> str | None:
        """Reserve capacity only against very small trades when a fresh 3R arm exists.

        This is intentionally narrow. A developing arm never blocks another
        respectable trade. It only protects an eval slot from <=1.60R ideas
        while a different 4/6+ ICT opportunity has fresh >=3R pre-armed
        geometry. The normal setup can still proceed once that arm expires or
        fails.
        """
        current_rr = float(getattr(setup, "risk_reward", 0.0) or 0.0)
        if current_rr > self.SMALL_TRADE_RR:
            return None
        strategy = str(getattr(setup, "metadata", {}).get("strategy", "ICT_CONFLUENCE"))
        if strategy == "REJECTION_BLOCK_10_10":
            return None

        candidates = []
        for key, arm in self.arms.items():
            if key == (setup.symbol, setup.timeframe):
                continue
            if int(arm.get("checklist_score") or 0) < 4:
                continue
            if float(arm.get("risk_reward") or 0.0) < self.STRONG_PREARM_RR:
                continue
            if int(arm.get("age_bars") or 999) > self.MAX_PRIORITY_AGE_BARS:
                continue
            candidates.append(arm)
        if not candidates:
            return None

        best = max(candidates, key=lambda arm: float(arm.get("risk_reward") or 0.0))
        return (
            f"Capital priority: holding eval capacity because {best['symbol']} {best['timeframe']} "
            f"has a fresh {best['checklist_score']}/6 pre-armed {best['risk_reward']:.2f}R ICT opportunity; "
            f"current {setup.symbol} {setup.timeframe} offers only {current_rr:.2f}R."
        )


def install_early_entry72(engine, *, logger: Callable[[str], None] | None = None) -> EarlyEntryPlanner72:
    """Install the 7.2H planner around one FlexibleConfluenceEngine instance."""
    existing = getattr(engine, "_early_entry_planner_72h", None)
    if existing is not None:
        return existing

    planner = EarlyEntryPlanner72(logger=logger)
    original_on_candle = engine.on_candle

    def wrapped_on_candle(self, symbol: str, timeframe: str, histories):
        setup = original_on_candle(symbol, timeframe, histories)
        if setup is not None:
            return planner.promote(self, setup, histories)
        planner.refresh(self, symbol, timeframe, histories)
        return None

    engine.on_candle = MethodType(wrapped_on_candle, engine)
    engine._early_entry_planner_72h = planner
    return planner
