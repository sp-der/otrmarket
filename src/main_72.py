from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src import main_71 as op71
from src.execution.live.config import ExecutionConfig
from src.execution.live.gateway import ExecutionGateway


runtime = op71.runtime
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
        f"broker gateway mode={config.mode.value}, armed={config.armed}, account={config.account}. "
        "PAPER is the safe default."
    )
    op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
