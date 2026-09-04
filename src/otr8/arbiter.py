from __future__ import annotations

from typing import Callable

from src.strategies.market_intelligence import evaluate_market_narrative

from .models import CandidateAssessment80, RegimeSnapshot80


class SetupArbiter80:
    """Rank already-qualified candidates without granting permission to trade.

    Every candidate must clear the normal session/quality/risk gates before this
    class can choose it. The arbiter only allocates the single GC idea slot when
    two strategy families mature together.
    """

    GRADE_POINTS = {
        "A+": 10.0,
        "A": 8.0,
        "A_COUNTERTREND": 7.0,
        "B+": 5.0,
        "B": 2.0,
    }

    def __init__(self, narrative_fn: Callable = evaluate_market_narrative) -> None:
        self.narrative_fn = narrative_fn

    @staticmethod
    def _regime_points(strategy: str, direction: str, regime: RegimeSnapshot80) -> float:
        name = regime.regime
        aligned = regime.direction in {"neutral", direction}
        if name == "TREND_EXPANSION":
            if strategy in {"ICT_CONFLUENCE", "GOLD_MOMENTUM_PULLBACK_72R", "TREND_CONTINUATION_REARM"}:
                return 10.0 if aligned else 2.0
            return 4.0 if aligned else 1.0
        if name == "TREND_PULLBACK":
            if strategy in {"ICT_CONFLUENCE", "TREND_CONTINUATION_REARM"}:
                return 9.0 if aligned else 3.0
            if strategy == "MSS_REVERSAL":
                return 7.0 if aligned else 3.0
            return 6.0 if aligned else 3.0
        if name == "REVERSAL_DEVELOPING":
            if strategy in {"MSS_REVERSAL", "REJECTION_BLOCK_10_10"}:
                return 10.0 if aligned else 1.0
            return 5.0 if aligned else 2.0
        if name == "VOLATILITY_EXPANSION":
            if strategy == "GOLD_MOMENTUM_PULLBACK_72R":
                return 9.0 if aligned else 2.0
            if strategy in {"ICT_CONFLUENCE", "TREND_CONTINUATION_REARM"}:
                return 7.0 if aligned else 2.0
            return 4.0
        if name == "RANGE":
            return 9.0 if strategy in {"MSS_REVERSAL", "REJECTION_BLOCK_10_10"} else 5.0
        if name == "CHOP":
            return 2.0
        if name == "WARMUP":
            return 0.0
        return 5.0 if aligned else 3.0

    @staticmethod
    def _strategy_points(setup) -> float:
        strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
        trigger = str(getattr(setup, "trigger_type", "") or "")
        if strategy == "REJECTION_BLOCK_10_10":
            score = int(setup.metadata.get("checklist_score", 0) or 0)
            total = int(setup.metadata.get("checklist_total", 10) or 10)
            return 8.0 if score >= total >= 10 else 0.0
        if strategy == "MSS_REVERSAL":
            return 6.0 if trigger in {"liquidity_sweep", "smt"} else 3.0
        if strategy == "GOLD_MOMENTUM_PULLBACK_72R":
            return 7.0
        if strategy == "TREND_CONTINUATION_REARM":
            return 6.0
        return 5.0

    def assess(self, setup, histories, regime: RegimeSnapshot80) -> CandidateAssessment80:
        strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
        rr = max(0.0, float(getattr(setup, "risk_reward", 0.0) or 0.0))
        try:
            narrative = self.narrative_fn(setup, histories) or {}
        except Exception as exc:
            narrative = {"score": 50, "grade": "B", "error": str(exc), "market_map": {}}

        narrative_score = max(0.0, min(100.0, float(narrative.get("score", 50) or 50)))
        narrative_points = narrative_score * 0.50
        rr_points = min(rr, 4.0) / 4.0 * 15.0

        context = setup.metadata.get("a_plus_context", {}) or {}
        grade = str(context.get("quality_grade") or narrative.get("grade") or "B").upper()
        quality_points = self.GRADE_POINTS.get(grade, 4.0)

        market_map = narrative.get("market_map", {}) or {}
        four_h = (
            market_map.get("timeframes", {})
            .get("4h", {})
            .get("structure", {})
            .get("direction")
        )
        if four_h == setup.direction:
            htf_points = 10.0
            htf_reason = "4H structure aligns"
        elif four_h in {None, "unknown", "neutral", "mixed"}:
            htf_points = 5.0
            htf_reason = "4H structure is neutral/unavailable"
        else:
            htf_points = 0.0
            htf_reason = "4H structure opposes"

        regime_points = self._regime_points(strategy, setup.direction, regime)
        strategy_points = self._strategy_points(setup)
        score = min(
            100.0,
            narrative_points + rr_points + quality_points + htf_points + regime_points + strategy_points,
        )

        reasons = (
            f"narrative {narrative_score:.0f}/100",
            f"{rr:.2f}R geometry",
            htf_reason,
            f"regime {regime.regime}",
            f"grade {grade}",
        )
        return CandidateAssessment80(
            setup_id=str(setup.setup_id),
            strategy=strategy,
            timeframe=str(setup.timeframe),
            direction=str(setup.direction),
            score=round(score, 2),
            risk_reward=round(rr, 4),
            narrative_score=round(narrative_points, 2),
            higher_timeframe_score=round(htf_points, 2),
            regime_score=round(regime_points, 2),
            quality_score=round(quality_points, 2),
            strategy_score=round(strategy_points, 2),
            reasons=reasons,
            details={"market_narrative": narrative, "quality_grade": grade},
        )

    def choose(self, candidates: list, histories, regimes: dict[str, RegimeSnapshot80]):
        assessments = []
        by_id = {}
        for setup in candidates:
            regime = regimes[str(setup.setup_id)]
            assessment = self.assess(setup, histories, regime)
            assessments.append(assessment)
            by_id[assessment.setup_id] = setup

        if not assessments:
            return None, []

        chosen_assessment = max(
            assessments,
            key=lambda item: (item.score, item.risk_reward, item.setup_id),
        )
        chosen = by_id[chosen_assessment.setup_id]
        chosen.metadata["setup_arbiter_80"] = {
            "selected": True,
            "score": chosen_assessment.score,
            "assessments": [item.to_dict() for item in assessments],
        }
        for assessment in assessments:
            setup = by_id[assessment.setup_id]
            if setup is chosen:
                continue
            setup.metadata["setup_arbiter_80"] = {
                "selected": False,
                "score": assessment.score,
                "winner_setup_id": chosen_assessment.setup_id,
                "winner_score": chosen_assessment.score,
                "reason": (
                    f"Another qualified GC candidate ranked higher: "
                    f"{chosen_assessment.strategy} {chosen_assessment.score:.2f} > {assessment.score:.2f}."
                ),
            }
        return chosen, assessments
