from __future__ import annotations

from dataclasses import dataclass

from src.risk.momentum_scalp72 import scalp_risk_cap


GRADE_RISK_CAPS = {
    "A+": 500.0,
    "A": 350.0,
    "B+": 100.0,
}


@dataclass(frozen=True)
class EvalSizingDecision72:
    risk_dollars: float
    risk_multiplier: float
    grade: str
    grade_cap_dollars: float
    projected_profit_dollars: float
    objective_met: bool


def _quality_grade(setup) -> str:
    metadata = getattr(setup, "metadata", {}) or {}
    operating = metadata.get("operating_mode", {}) or {}
    if operating.get("quality_grade"):
        return str(operating["quality_grade"]).upper()

    context = metadata.get("a_plus_context", {}) or {}
    if context.get("quality_grade"):
        return str(context["quality_grade"]).upper()

    strategy = str(metadata.get("strategy", "")).upper()
    if strategy == "REJECTION_BLOCK_10_10":
        score = int(metadata.get("checklist_score", 0) or 0)
        total = int(metadata.get("checklist_total", 10) or 10)
        if score >= total >= 10:
            return "A+"

    return "A"


def apply_eval_sizing72(decision, setup, inherited_risk_dollars: float, inherited_multiplier: float):
    """Apply grade-aware EVAL sizing without weakening upstream safety caps.

    The evaluation guard still owns daily drawdown, MLL, concurrency, session
    locks, and available-risk headroom. This function can only reduce the risk
    that survived those gates. Raising the upstream EVAL_RISK_PER_TRADE budget
    therefore gives A+ setups room to reach $500 risk while A and B+ remain
    intentionally smaller. Operation 7.2S adds a separate <=$125 default scalp
    cap so frequency never inherits primary A/A+ size.
    """
    metadata = getattr(setup, "metadata", {}) or {}
    operating = metadata.get("operating_mode", {}) or {}
    mode = str(operating.get("mode", "")).upper()

    if mode not in {"EVAL", "EVALUATION"}:
        return round(float(inherited_risk_dollars), 2), float(inherited_multiplier)

    grade = _quality_grade(setup)
    strategy = str(metadata.get("strategy", "")).upper()
    grade_cap = float(GRADE_RISK_CAPS.get(grade, 350.0))
    if strategy == "MOMENTUM_SCALP":
        grade_cap = min(grade_cap, scalp_risk_cap())

    inherited = max(0.0, float(inherited_risk_dollars or 0.0))
    applied = round(min(inherited, grade_cap), 2)

    decision_cap = max(0.0, float(getattr(decision, "risk_dollars", 0.0) or 0.0))
    if decision_cap > 0:
        effective_multiplier = max(0.0, min(1.0, applied / decision_cap))
    else:
        effective_multiplier = max(0.0, min(1.0, float(inherited_multiplier or 0.0)))

    rr = max(0.0, float(getattr(setup, "risk_reward", 0.0) or 0.0))
    projected_profit = round(applied * rr, 2)
    default_objective = round(scalp_risk_cap() * 1.5, 2) if strategy == "MOMENTUM_SCALP" else 1500.0
    profit_objective = float(metadata.get("profit_objective_dollars", default_objective) or default_objective)

    details = {
        "profile": "EVAL_GRADE_SIZING_7_2",
        "grade": grade,
        "strategy": strategy,
        "grade_risk_cap_dollars": grade_cap,
        "upstream_risk_dollars": round(inherited, 2),
        "applied_risk_dollars": applied,
        "effective_risk_multiplier": round(effective_multiplier, 4),
        "projected_profit_dollars": projected_profit,
        "profit_objective_dollars": profit_objective,
        "profit_objective_met": projected_profit >= profit_objective,
        "note": "Profit objective never stretches the structural target or bypasses risk gates.",
    }
    metadata["eval_sizing_72"] = details
    setup.metadata = metadata

    return applied, effective_multiplier
