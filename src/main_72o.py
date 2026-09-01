from __future__ import annotations

import asyncio

from src import main_72 as op72
from src.execution.live.config import ExecutionConfig


runtime = op72.runtime
_previous_setup_risk_72o = op72.op71.op70.op59.op58.base._setup_risk


def _setup_risk_72o(decision, setup):
    """Use a fixed simulated risk unit in VERIFY so R and dollar P/L reconcile.

    Verification is meant to test the trading brain, not session-dependent sizing.
    Quality/structure gates still decide whether a trade exists; this only removes
    session/grade multipliers from the simulated dollar accounting while VERIFY is
    active. EVAL/FUNDED sizing remains untouched.
    """
    if not op72._verification_enabled_72():
        return _previous_setup_risk_72o(decision, setup)

    risk = op72._verification_risk_72()
    metadata = getattr(setup, "metadata", {}) or {}
    metadata["verify_sizing_72o"] = {
        "profile": "FIXED_VERIFY_RISK_7_2O",
        "risk_dollars": round(risk, 2),
        "risk_multiplier": 1.0,
        "note": "VERIFY uses one fixed simulated risk unit so cumulative R and dollar P/L are directly comparable.",
    }
    setup.metadata = metadata
    return round(risk, 2), 1.0


op72.op71.op70.op59.op58.base._setup_risk = _setup_risk_72o


if __name__ == "__main__":
    op72.op71._patch_runtime_manifest_71()
    op72._patch_runtime_manifest_72()
    config = ExecutionConfig.from_env()
    runtime.console.log(
        "Operation 7.2O active: 7.2N strategy stack preserved; VERIFY sizing is now fixed-risk for clean R/$ diagnostics; "
        f"broker gateway mode={config.mode.value}."
    )
    op72.op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
