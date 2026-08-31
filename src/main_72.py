from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src import main_71 as op71
from src.execution.live.config import ExecutionConfig
from src.execution.live.gateway import ExecutionGateway
from src.risk.eval_history72 import install_eval_history_filter
from src.risk.eval_sizing72 import apply_eval_sizing72
from src.strategies.reversal_guard72 import assess_one_minute_reversal_context


# Dashboard and strategy engine are separate Railway processes. Install the
# prior-run filter here as well so preserved historical trades never consume
# current eval daily/session/P&L counters after a 7.2G counter reset.
install_eval_history_filter()

runtime = op71.runtime

# Operation 7.2R hotfix: keep 1m as a precision execution timeframe, but stop
# treating a local MSS as sufficient evidence that the larger move reversed.
# The inherited 7.1 quality/operating-mode gate still runs first. This final
# guard applies only to 1m MSS_REVERSAL candidates.
_previous_quality_gate_72 = op71.op70.op59.op58._adaptive_quality_gate


def _adaptive_quality_gate_72(connection, setup, histories=None):
    allowed, reason = _previous_quality_gate_72(connection, setup, histories)
    if not allowed:
        return False, reason

    if histories is None:
        histories = runtime.histories_snapshot()
    guard_allowed, guard_reason, guard_details = assess_one_minute_reversal_context(setup, histories)
    if guard_details.get("applicable"):
        setup.metadata["one_minute_reversal_guard_72"] = guard_details
    if not guard_allowed:
        return False, guard_reason

    if guard_details.get("applicable"):
        try:
            current = float(setup.metadata.get("risk_multiplier", 1.0))
        except (TypeError, ValueError):
            current = 1.0
        risk_cap = 0.50
        setup.metadata["risk_multiplier"] = min(current, risk_cap)
        setup.metadata["execution_tier"] = "ONE_MINUTE_REVERSAL_MTF_72"
        setup.metadata["one_minute_reversal_guard_72"]["risk_cap"] = risk_cap
        return True, f"{reason} {guard_reason} 1m reversal risk capped at {risk_cap:.0%}."

    return True, reason


op71.op70.op59.op58._adaptive_quality_gate = _adaptive_quality_gate_72

# Operation 7.2E eval sizing: let the evaluation guard expose up to $500 of
# available risk, then grade-cap the amount that reaches paper execution.
# This wrapper can only REDUCE the upstream risk decision, so daily drawdown,
# MLL, session, concurrency, reversal and other existing caps remain in force.
_previous_setup_risk_72 = op71.op70.op59.op58.base._setup_risk


def _setup_risk_72(decision, setup):
    inherited_risk, inherited_multiplier = _previous_setup_risk_72(decision, setup)
    return apply_eval_sizing72(
        decision,
        setup,
        inherited_risk_dollars=inherited_risk,
        inherited_multiplier=inherited_multiplier,
    )


op71.op70.op59.op58.base._setup_risk = _setup_risk_72

execution_gateway = ExecutionGateway()
_original_paper_register_72 = runtime.paper.register_setup


def _register_setup_72(setup, *, risk_dollars=None, guard_reason=None):
    """Preserve paper execution, then mirror only fully-approved setups."""
    position = _original_paper_register_72(setup, risk_dollars=risk_dollars, guard_reason=guard_reason)
    try:
        result = execution_gateway.handle_approved_setup(setup, risk_dollars=risk_dollars, guard_reason=guard_reason)
        runtime.console.log(
            f"EXECUTION 7.2 {result.get('code')}: {setup.symbol} {setup.timeframe} "
            f"{setup.direction.upper()} - {result.get('reason')}"
        )
    except Exception as exc:
        runtime.console.log(f"EXECUTION 7.2 fail-closed mirror error for {setup.setup_id}: {exc}")
    return position


runtime.paper.register_setup = _register_setup_72


def _patch_runtime_manifest_72() -> None:
    path = Path(__file__).resolve().parent / "dashboard" / "static" / "runtime-build.json"
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        config = ExecutionConfig.from_env()
        manifest.setdefault("build", {})["operation"] = "Operation 7.2"
        manifest["build"]["execution_mode"] = config.mode.value
        rules = manifest.setdefault("rules", [])
        by_name = {str(item.get("name")): item for item in rules if isinstance(item, dict)}
        additions = {
            "Broker execution gateway": (
                "Approved setups mirror into an idempotent micro-futures command queue; default PAPER mode emits no broker commands",
                "src/main_72.py + src/execution/live/",
            ),
            "Execution safety interlocks": (
                "Explicit arming + account lock + max micro/risk cap + TTL + sticky kill switch + fresh broker reconciliation required before dispatch",
                "src/execution/live/safety.py + src/execution/live/store.py",
            ),
            "Broker reconciliation": (
                "NinjaTrader snapshots are compared with OTR command/position state; mismatches fail closed and block new command delivery",
                "src/execution/live/store.py + src/execution/live/api.py",
            ),
            "1m reversal context guard": (
                "MSS_REVERSAL on 1m requires liquidity sweep/SMT catalyst, aligned 5m context, no 15m/30m opposition, and 15m or 30m confirmation; accepted reversal risk capped at 50%",
                "src/main_72.py + src/strategies/reversal_guard72.py",
            ),
            "Evaluation grade sizing": (
                "EVAL paper risk is grade-aware: A+ up to $500, A up to $350, B+ up to $100; upstream daily drawdown/MLL/session caps still win and $1,500 remains a structural objective",
                "src/main_72.py + src/risk/eval_sizing72.py",
            ),
            "Non-destructive eval reset": (
                "Fresh eval accounting preserves prior trade/setup/intelligence history and excludes prior-run setup IDs from new eval counters instead of deleting rows",
                "src/risk/eval_history72.py + src/dashboard/server_72.py",
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
        runtime.console.log(f"Operation 7.2 manifest audit warning: {exc}")


if __name__ == "__main__":
    op71._patch_runtime_manifest_71()
    _patch_runtime_manifest_72()
    config = ExecutionConfig.from_env()
    runtime.console.log(
        "Operation 7.2 active: Operation 7.1 market intelligence remains intact; "
        "1m MSS reversals now require larger-chart confirmation; "
        "EVAL sizing allows A+ <= $500, A <= $350, B+ <= $100 after all upstream risk caps; "
        "fresh eval resets preserve prior trade history; "
        f"broker gateway mode={config.mode.value}, armed={config.armed}, account={config.account}. "
        "PAPER is the safe default."
    )
    op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
