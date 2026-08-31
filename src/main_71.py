from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src import main_70 as op70
from src.risk.operating_mode import evaluate_operating_mode
from src.storage.learning_71 import observe_market_opportunity_71


runtime = op70.runtime


# Operation 7.1 keeps every Operation 7.0 quality/recovery rule, then applies
# an explicit account operating profile. EVAL mode has no trade-count cap: it
# can keep taking qualified opportunities while the EvaluationRiskGuard owns
# the realized per-session profit cap. FUNDED mode protects a $350-$500 winning
# day by requiring A+ quality above the floor and locking new risk at the
# ceiling. Neither mode forces a trade or stretches a target.
_previous_quality_gate_71 = op70.op59.op58._adaptive_quality_gate


def _adaptive_quality_gate_71(connection, setup, histories=None):
    allowed, reason = _previous_quality_gate_71(connection, setup, histories)
    if not allowed:
        return False, reason

    mode_allowed, mode_reason, mode_details = evaluate_operating_mode(connection, setup)
    setup.metadata["operating_mode"] = mode_details
    if not mode_allowed:
        return False, mode_reason

    return True, f"{reason} {mode_reason}"


op70.op59.op58._adaptive_quality_gate = _adaptive_quality_gate_71

# The inherited Operation 5.8 observer still decides when a completed market
# move becomes a lesson. Operation 7.1 enriches only newly-created lessons with
# the market map that existed at that historical cutoff. This keeps the learner
# causal while teaching it about premium/discount, equal liquidity, inverse
# FVGs, order blocks, breaker candidates, rejection behavior and MTF structure.
op70.op59.op58.observe_market_opportunity = observe_market_opportunity_71


def _patch_runtime_manifest_71() -> None:
    path = Path(__file__).resolve().parent / "dashboard" / "static" / "runtime-build.json"
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.setdefault("build", {})["operation"] = "Operation 7.1"
        rules = manifest.setdefault("rules", [])
        by_name = {str(item.get("name")): item for item in rules if isinstance(item, dict)}

        additions = {
            "Market intelligence": (
                "Multi-timeframe structure + dealing range + equal liquidity + active/inverse FVG + "
                "order-block/breaker candidates + rejection behavior + session liquidity + NQ/ES SMT",
                "src/strategies/market_intelligence.py + src/strategies/execution_quality.py",
            ),
            "Market intelligence learning": (
                "Large-move lessons retain causal market-map context and aggregate MTF, dealing-range, liquidity, FVG, OB/breaker and rejection feature statistics",
                "src/storage/learning_71.py + src/main_71.py",
            ),
            "Evaluation operating mode": (
                "No trade-count cap; keep taking qualified trades and let the evaluation risk guard bank each named session at its realized profit cap (normally $1,500)",
                "src/risk/operating_mode.py + src/risk/evaluation.py + src/main_71.py",
            ),
            "Funded operating mode": (
                "$350-$500 daily objective; above $350 only A+ may add <=35% risk; at $500 new risk locks",
                "src/risk/operating_mode.py + src/main_71.py",
            ),
        }
        for name, (value, source) in additions.items():
            if name in by_name:
                by_name[name]["value"] = value
                by_name[name]["source"] = source
            else:
                rules.append({"name": name, "value": value, "source": source})

        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        runtime.console.log(f"Operation 7.1 manifest audit warning: {exc}")


if __name__ == "__main__":
    _patch_runtime_manifest_71()
    runtime.console.log(
        "Operation 7.1 active: Operation 7.0 execution/recovery rules remain intact; "
        "Market Intelligence 1.0 grades chart narrative, enriches causal market lessons, "
        "EVAL mode has no trade-count cap and uses the evaluation risk guard's realized "
        "per-session profit cap; FUNDED mode retains its profit-protection behavior."
    )
    op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
