from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from src.storage.database_concurrency80 import install as install_sqlite_concurrency80

# Install before inherited strategy/runtime modules bind database helpers. The
# dashboard and engine are separate Railway processes, so both entrypoints own
# the same lightweight WAL connection contract explicitly.
install_sqlite_concurrency80()

from src import main_58 as op58
from src import main_61 as op61
from src import main_62 as op62
from src import main_65 as op65
from src import main_72t as legacy
from src.execution.live.config import ExecutionConfig
from src.otr8.pipeline import OTRPipeline80


runtime = legacy.runtime
pipeline_80: OTRPipeline80 | None = None


def _patch_runtime_manifest_80() -> None:
    path = Path(__file__).resolve().parent / "dashboard" / "static" / "runtime-build.json"
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.setdefault("build", {})["operation"] = "Operation 8.0"
        manifest["build"]["engine"] = "src.main_80"
        manifest["build"]["architecture"] = "EXPLICIT_PIPELINE_8_0"
        rules = manifest.setdefault("rules", [])
        by_name = {str(item.get("name")): item for item in rules if isinstance(item, dict)}
        additions = {
            "OTR 8.0 decision pipeline": (
                "candidate collection → regime → session → quality → setup arbiter → account guard → trade plan → execution handoff; every decision is traced",
                "src/otr8/pipeline.py",
            ),
            "Gold regime engine": (
                "Deterministic trend expansion / pullback / range / chop / volatility expansion / reversal context using 4H/1H/15m structure; context never bypasses quality gates",
                "src/otr8/regime.py",
            ),
            "Setup arbiter": (
                "Qualified same-candle candidates compete on narrative, RR, 4H alignment, regime fit and strategy evidence; no strategy has unconditional priority",
                "src/otr8/arbiter.py",
            ),
            "Canonical trade plan": (
                "Strategy-side WHAT is frozen before paper/broker translation; the existing ExecutionIntent remains the broker-side HOW contract",
                "src/otr8/models.py + src/execution/live/models.py",
            ),
            "Execution state machine": (
                "Broker events are idempotent, legal forward transitions only, stale/out-of-order callbacks cannot regress command state",
                "src/execution/state_machine80.py + src/execution/live/store.py",
            ),
            "Protection reconciliation": (
                "A filled broker position must have a matching working protective stop or reconciliation fails closed and new dispatch is blocked",
                "src/execution/live/store.py",
            ),
        }
        for name, (value, source) in additions.items():
            if name in by_name:
                by_name[name].update(value=value, source=source)
            else:
                rules.append({"name": name, "value": value, "source": source})
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        runtime.console.log(f"Operation 8.0 manifest audit warning: {exc}")


def _install_pipeline_80() -> OTRPipeline80:
    global pipeline_80
    if pipeline_80 is not None:
        return pipeline_80

    pipeline_80 = OTRPipeline80(
        runtime=runtime,
        session_gate=op58.evaluate_session_consistency_58,
        quality_gate=op58._adaptive_quality_gate,
        setup_risk=op58.base._setup_risk,
        continuation=op62.continuation,
        shadow_register=op58.base._register_48_shadow,
        counterfactual_module=op61,
        observer=op58._observe,
        mode_provider=lambda: os.getenv("OTR_TRADING_MODE", ""),
    )
    runtime.evaluate_strategy = pipeline_80.evaluate

    # Route the one accelerated 5m forming-candle lane through the exact same
    # decision pipeline instead of keeping a second copy of session/quality/risk
    # logic alive in Operation 6.5.
    def handle_intrabar_80(connection, setup, histories, confirmations: int, stable_seconds: float):
        setup.metadata.setdefault("strategy", "ICT_CONFLUENCE")
        setup.metadata.setdefault("checklist_total", 6)
        setup.metadata.setdefault("checklist_score", 6)
        setup.metadata["intrabar_execution_65"] = {
            "operation": 8.0,
            "legacy_source": 6.5,
            "mode": "STABLE_STATE_COPY",
            "source": "FORMING_CANDLE",
            "confirmations": int(confirmations),
            "stable_seconds": round(float(stable_seconds), 3),
            "timeframe": setup.timeframe,
            "durable_state_untouched": True,
            "no_chase_preserved": True,
            "central_pipeline": True,
        }
        handled = pipeline_80.process_candidates(
            connection,
            [setup],
            histories,
            source="INTRABAR",
        )
        return handled[-1] if handled else None

    op65._handle_intrabar_setup_65 = handle_intrabar_80
    return pipeline_80


def main() -> None:
    # Install the proven 7.2T infrastructure pieces explicitly, then replace only
    # orchestration with 8.0. The legacy detector/gate modules remain frozen as
    # a parity reference and can still be replay-regression tested.
    legacy.op72s.op72r.op72q.op72.op71._patch_runtime_manifest_71()
    legacy.op72s.op72r.op72q.op72._patch_runtime_manifest_72()
    _patch_runtime_manifest_80()

    legacy.op72s._install_verify_trade_tag_trigger_72s()
    derived = legacy.backfill_4h_candles_72t()
    legacy._install_4h_context_72t()
    legacy._install_single_symbol_execution_guard_72t()
    reconciliation = legacy._reconcile_persisted_active_72t()
    capture = legacy.install_training_capture_72t()
    legacy._install_idempotent_training_trade_triggers_72t()
    _install_pipeline_80()

    config = ExecutionConfig.from_env()
    runtime.console.log(
        "Operation 8.0 active: explicit decision pipeline + Gold regime context + setup arbiter + canonical trade plan; "
        "7.2Q Gold 1m firewall, 7.2R momentum recognition, 7.2S ledger, 7.2T 4H/training/one-symbol protection retained; "
        "5m intrabar now uses the central pipeline; broker event state regression is blocked and filled positions require protective-stop reconciliation; "
        f"4h_backfill_rows={derived}; active_survivors={reconciliation.get('surviving', 0)}; "
        f"training_run={capture.get('run_id') or os.getenv('OTR_VERIFY_RUN_ID', 'none')}; "
        f"broker gateway mode={config.mode.value}, armed={config.armed}."
    )
    legacy.op72s.op72r.op72q.op72.op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")


if __name__ == "__main__":
    main()