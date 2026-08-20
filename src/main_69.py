from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src import main_68 as op68


runtime = op68.runtime
op65 = op68.op65
op58 = op68.op58


# ---------------------------------------------------------------------------
# Operation 6.9A: 1-minute charts are precision execution timeframes again.
#
# Operation 6.8 intentionally made every 1m setup scout-only after a cluster of
# rapid losses. That blanket firewall also blocked fully qualified A/A+ setups.
# Operation 6.9 removes only that blanket restriction. The complete quality,
# context, geometry, cooldown, session, and evaluation guards that existed
# before the 6.8 firewall remain in force.
#
# A/A+ 1m candidates may execute at the risk already assigned by the existing
# strategy/session logic. B+ 1m candidates may execute only after the existing
# quality gate accepts them and are additionally capped at 40% risk. This keeps
# 1m useful for surgical entry timing without turning it into a free-fire mode.
# ---------------------------------------------------------------------------
_previous_quality_gate_69 = op68._previous_quality_gate_68


def _risk_multiplier(setup) -> float:
    try:
        return float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _adaptive_quality_gate_69(connection, setup, histories=None):
    allowed, reason = _previous_quality_gate_69(connection, setup, histories)
    if not allowed:
        return False, reason

    if str(getattr(setup, "timeframe", "")) != "1m":
        return True, reason

    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    context = setup.metadata.get("a_plus_context", {}) or {}
    grade = str(context.get("quality_grade") or "").upper()
    score = context.get("quality_score")

    # Rejection-block candidates do not use a_plus_context, but the inherited
    # gate only allows a perfect 10/10 checklist with >=3R. Treat that strict
    # pass as equivalent to an A+ precision candidate for this firewall layer.
    if not grade and strategy == "REJECTION_BLOCK_10_10":
        grade = "A+"

    if grade == "B+":
        current = _risk_multiplier(setup)
        setup.metadata["risk_multiplier"] = min(current, 0.40)
        setup.metadata["execution_tier"] = "ONE_MINUTE_B_PLUS_REDUCED_69"
        mode = "AUTONOMOUS_REDUCED"
        note = (
            "B+ 1m candidate cleared the inherited quality gate and is allowed "
            "only at a 40% maximum risk multiplier."
        )
    else:
        # A/A+ or another setup that already survived the inherited strategy
        # gate keeps its existing risk assignment. We do not add a second score
        # hurdle here because the inherited gate is the source of truth.
        mode = "AUTONOMOUS_PRECISION"
        note = (
            "1m candidate cleared the inherited chart-quality, context, "
            "geometry, cooldown, session, and risk gates."
        )

    setup.metadata["one_minute_precision_69"] = {
        "operation": 6.9,
        "mode": mode,
        "autonomous_execution": True,
        "strategy": strategy,
        "quality_grade": grade or "PRIOR_GATE_PASS",
        "quality_score": score,
        "risk_multiplier": _risk_multiplier(setup),
        "five_minute_confirmation_required": False,
        "reason": note,
    }

    grade_text = grade or "qualified"
    return True, (
        f"Operation 6.9 1m precision execution: {strategy} {grade_text} candidate "
        f"may execute at {_risk_multiplier(setup):.0%} max risk; 5m confirmation "
        "is context/support, not a mandatory entry trigger."
    )


op58._adaptive_quality_gate = _adaptive_quality_gate_69


# ---------------------------------------------------------------------------
# Operation 6.9B: keep the 6.8 stability choice for intrabar probing.
#
# 1m is autonomous again, but execution remains based on completed 1m candles.
# The sub-candle 0.25s stability probe stays 5m-only for now. This separates
# "allow 1m trades" from "allow unstable forming-1m trades" so the new build
# can recover precision entries without reintroducing the rapid-fire behavior
# that Operation 6.8 was designed to stop.
# ---------------------------------------------------------------------------
op65.INTRABAR_TIMEFRAMES = {"5m"}


# ---------------------------------------------------------------------------
# Operation 6.9C: update the dashboard build audit at runtime.
# ---------------------------------------------------------------------------
def _patch_runtime_manifest_69() -> None:
    path = Path(__file__).resolve().parent / "dashboard" / "static" / "runtime-build.json"
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.setdefault("build", {})["operation"] = "Operation 6.9"
        rules = manifest.setdefault("rules", [])
        by_name = {str(item.get("name")): item for item in rules if isinstance(item, dict)}

        if "Autonomous timeframes" in by_name:
            by_name["Autonomous timeframes"]["value"] = (
                "1m / 5m / 15m / 1h autonomous when inherited quality and risk gates pass; "
                "1m B+ capped at 40% risk"
            )
            by_name["Autonomous timeframes"]["source"] = "src/main_69.py + src/risk/session_consistency.py"

        if "1m quality firewall" in by_name:
            by_name["1m quality firewall"]["name"] = "1m precision execution"
            by_name["1m quality firewall"]["value"] = (
                "A/A+ and otherwise strict inherited-quality 1m passes may execute; "
                "B+ may execute at <=40% risk; 5m confirmation is not mandatory"
            )
            by_name["1m quality firewall"]["source"] = "src/main_69.py"
        elif "1m precision execution" not in by_name:
            rules.append(
                {
                    "name": "1m precision execution",
                    "value": (
                        "A/A+ and strict inherited-quality 1m passes may execute; "
                        "B+ may execute at <=40% risk; 5m confirmation is not mandatory"
                    ),
                    "source": "src/main_69.py",
                }
            )

        if "Intrabar acceleration" in by_name:
            by_name["Intrabar acceleration"]["value"] = (
                "5m only; 1m autonomous execution uses completed 1m candles for stability"
            )
            by_name["Intrabar acceleration"]["source"] = "src/main_69.py + src/main_65.py"

        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        runtime.console.log(f"Operation 6.9 manifest audit warning: {exc}")


if __name__ == "__main__":
    _patch_runtime_manifest_69()
    runtime.console.log(
        "Operation 6.9 active: qualified 1m setups are autonomous again; A/A+ keep "
        "their inherited risk tier, B+ is capped at 40%, 5m confirmation is no longer "
        "mandatory, and forming-candle acceleration remains 5m-only."
    )
    op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
