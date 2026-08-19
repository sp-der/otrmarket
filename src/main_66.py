from __future__ import annotations

import asyncio

from src import main_65 as op65


runtime = op65.runtime
base = op65.base

# ---------------------------------------------------------------------------
# Operation 6.6A: every autonomous execution timeframe can use the same stable
# intrabar acceleration. The durable ICT state remains candle-close driven;
# only the deep-copied probe sees the forming candle.
# ---------------------------------------------------------------------------
ALL_EXECUTION_TIMEFRAMES = {"1m", "5m", "15m", "1h"}
op65.INTRABAR_TIMEFRAMES = set(ALL_EXECUTION_TIMEFRAMES)


# ---------------------------------------------------------------------------
# Operation 6.6B: fast-eval profit pacing.
#
# EvaluationRiskGuard owns the realized $/session lock. This wrapper also caps
# the *planned* profit of a newly accepted setup to the remaining session profit
# budget, so a late-session 3R idea cannot intentionally size beyond the user's
# configured session objective.
# ---------------------------------------------------------------------------
_previous_setup_risk_66 = base._setup_risk


def _setup_risk_66(decision, setup) -> tuple[float, float]:
    applied_risk, multiplier = _previous_setup_risk_66(decision, setup)
    snapshot = getattr(decision, "snapshot", {}) or {}
    remaining = snapshot.get("session_profit_remaining")
    rr = float(getattr(setup, "risk_reward", 0.0) or 0.0)

    if remaining is None or rr <= 0:
        return applied_risk, multiplier

    remaining = max(0.0, float(remaining))
    planned_profit = max(0.0, float(applied_risk) * rr)
    if planned_profit <= remaining:
        setup.metadata.setdefault("fast_eval_66", {}).update(
            {
                "session_profit_remaining": round(remaining, 2),
                "planned_profit": round(planned_profit, 2),
                "risk_capped_to_session": False,
            }
        )
        return applied_risk, multiplier

    capped_risk = round(remaining / rr, 2)
    capped_risk = max(0.0, min(float(applied_risk), capped_risk))
    decision_risk = float(getattr(decision, "risk_dollars", 0.0) or 0.0)
    capped_multiplier = (capped_risk / decision_risk) if decision_risk > 0 else 0.0
    setup.metadata.setdefault("fast_eval_66", {}).update(
        {
            "session_profit_remaining": round(remaining, 2),
            "planned_profit_before_cap": round(planned_profit, 2),
            "planned_profit_after_cap": round(capped_risk * rr, 2),
            "risk_before_cap": round(float(applied_risk), 2),
            "risk_after_cap": round(capped_risk, 2),
            "risk_capped_to_session": True,
        }
    )
    return capped_risk, capped_multiplier


base._setup_risk = _setup_risk_66


if __name__ == "__main__":
    runtime.console.log(
        "Operation 6.6 active: stable intrabar execution on 1m/5m/15m/1h, "
        "Operation 6.5 durable-state protections preserved, and fast-eval "
        "session profit pacing enabled."
    )
    op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
