from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src import main_71 as op71
from src.execution.live.config import ExecutionConfig
from src.execution.live.gateway import ExecutionGateway
from src.risk.eval_history72 import install_eval_history_filter
from src.risk.eval_sizing72 import apply_eval_sizing72
from src.risk.momentum_scalp72 import evaluate_scalp_operating_mode72
from src.strategies.momentum_scalp72 import MomentumScalpEngine72
from src.strategies.reversal_guard72 import assess_one_minute_reversal_context


# Dashboard and strategy engine are separate Railway processes. Install the
# prior-run filter here as well so preserved historical trades never consume
# current eval daily/session/P&L counters after a 7.2G counter reset.
install_eval_history_filter()

runtime = op71.runtime


# Operation 7.2S: add a dedicated 1m momentum-scalp detector beside, not inside,
# the existing ICT/rejection/reversal brain. Existing setups always win a same-
# candle collision so the faster lane can never stack duplicate risk on top of
# a primary setup.
_momentum_scalp_72 = MomentumScalpEngine72()
_previous_on_candle_all_72 = runtime.strategy.on_candle_all
_previous_strategy_diagnostic_72 = runtime.strategy.diagnostic
_previous_clear_symbol_72 = runtime.strategy.clear_symbol


def _diag_progress(value) -> float:
    if not value:
        return -1.0
    try:
        score = float(value.get("checklist_score", 0) or 0)
        total = max(1.0, float(value.get("checklist_total", 1) or 1))
        return score / total
    except (TypeError, ValueError):
        return -1.0


def _on_candle_all_72(symbol: str, timeframe: str, histories):
    candidates = list(_previous_on_candle_all_72(symbol, timeframe, histories))
    scalp = _momentum_scalp_72.on_candle(symbol, timeframe, histories)
    if scalp is not None:
        if candidates:
            primary = candidates[0]
            detected = primary.metadata.setdefault("also_detected_by", [])
            if "MOMENTUM_SCALP" not in detected:
                detected.append("MOMENTUM_SCALP")
            primary.metadata["momentum_scalp_collision_deduped_72"] = True
        else:
            candidates.append(scalp)
            runtime.console.log(
                f"SCALP 7.2S CANDIDATE {scalp.symbol} 1m {scalp.direction.upper()} "
                f"{scalp.risk_reward:.2f}R via {scalp.metadata.get('entry_type')}"
            )
    return candidates


def _strategy_diagnostic_72(symbol: str, timeframe: str):
    primary = _previous_strategy_diagnostic_72(symbol, timeframe)
    scalp = _momentum_scalp_72.diagnostic(symbol, timeframe)
    if _diag_progress(scalp) > _diag_progress(primary):
        return scalp
    return primary or scalp


def _clear_symbol_72(symbol: str) -> None:
    _previous_clear_symbol_72(symbol)
    _momentum_scalp_72.clear_symbol(symbol)


runtime.strategy.on_candle_all = _on_candle_all_72
runtime.strategy.diagnostic = _strategy_diagnostic_72
runtime.strategy.clear_symbol = _clear_symbol_72

# Do not contaminate the Operation 4.8 shadow baseline with a strategy that did
# not exist in that baseline. The normal 7.2 paper ledger still receives the
# scalp candidate after all current guards pass.
_previous_shadow_register_72 = op71.op70.op59.op58.base._register_48_shadow


def _register_48_shadow_72(connection, setup):
    if str(setup.metadata.get("strategy", "")).upper() == "MOMENTUM_SCALP":
        return None
    return _previous_shadow_register_72(connection, setup)


op71.op70.op59.op58.base._register_48_shadow = _register_48_shadow_72


# Operation 7.2R hotfix: keep 1m as a precision execution timeframe, but stop
# treating a local MSS as sufficient evidence that the larger move reversed.
# The inherited 7.1 quality/operating-mode gate still runs first for every
# primary setup. Momentum scalps use their own smaller-risk quota and guard path.
_previous_quality_gate_72 = op71.op70.op59.op58._adaptive_quality_gate


def _momentum_scalp_quality_gate_72(connection, setup, histories=None):
    if setup.timeframe != "1m":
        return False, "Momentum scalp lane is restricted to completed 1m candles."
    rr = float(setup.risk_reward or 0.0)
    if rr < 1.25:
        return False, f"Momentum scalp offers only {rr:.2f}R; require at least 1.25R."

    details = setup.metadata.get("scalp_context", {}) or {}
    body = float(details.get("body_ratio", 0.0) or 0.0)
    candle_range = float(details.get("range_ratio", 0.0) or 0.0)
    htf = str(details.get("five_minute_direction", "neutral"))
    progress = float(setup.metadata.get("signal_target_progress", 1.0) or 0.0)
    if body < 1.55 or candle_range < 1.25:
        return False, (
            f"Momentum impulse weakened below scalp floor: {body:.2f}x body / "
            f"{candle_range:.2f}x range."
        )
    if htf not in {"neutral", setup.direction}:
        return False, f"5m context is {htf}; scalp direction is {setup.direction}."
    if progress > 0.35:
        return False, f"Scalp signal already traveled {progress:.0%} of target; 35% no-chase ceiling exceeded."

    # Preserve the same active-exposure and symbol cooldown protections used by
    # primary setups. Operation 7.0 has already patched these helpers to be
    # symbol-aware instead of freezing unrelated markets.
    active_ok, active_reason = op71.op70.op59.op58.base._active_risk_gate(setup)
    if not active_ok:
        return False, active_reason
    cooldown_ok, cooldown_reason = op71.op70.op59.op58.base._same_symbol_cooldown(connection, setup)
    if not cooldown_ok:
        return False, cooldown_reason

    # After a realized loss, reuse Operation 7.0's account/symbol recovery logic
    # before the scalp-specific quota. This can only reduce risk or reject.
    post_ok, post_reason = op71.op70._post_loss_risk_70(connection, setup)
    if not post_ok:
        return False, post_reason

    mode_allowed, mode_reason, mode_details = evaluate_scalp_operating_mode72(connection, setup)
    setup.metadata["operating_mode"] = mode_details
    if not mode_allowed:
        return False, mode_reason

    return True, (
        f"Operation 7.2S momentum scalp passed: {body:.2f}x body / {candle_range:.2f}x range, "
        f"5m {htf}, {rr:.2f}R, signal progress {progress:.0%}. {post_reason} {mode_reason}"
    )


def _adaptive_quality_gate_72(connection, setup, histories=None):
    strategy = str(setup.metadata.get("strategy", "")).upper()
    if strategy == "MOMENTUM_SCALP":
        return _momentum_scalp_quality_gate_72(connection, setup, histories)

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
# Momentum scalps receive an additional <=$125 default hard cap in eval_sizing72.
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
                "EVAL paper risk is grade-aware: A+ up to $500, A up to $350, B+ up to $100; momentum scalps are separately capped at $125 by default; upstream daily drawdown/MLL caps still win",
                "src/main_72.py + src/risk/eval_sizing72.py + src/risk/momentum_scalp72.py",
            ),
            "Momentum scalp lane": (
                "Completed 1m displacement + micro-structure break + aligned/neutral 5m context + first 50-62% or micro-FVG pullback; target 1.5R; signal rejected after 35% target progress; max 3/session, 6/day, two scalp losses disable that symbol for the session",
                "src/strategies/momentum_scalp72.py + src/risk/momentum_scalp72.py + src/main_72.py",
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
        "1m MSS reversals require larger-chart confirmation; "
        "7.2S momentum scalps use 1m impulse + micro break + first pullback with <=$125 default risk, "
        "3/session and 6/day scalp quotas, and a two-loss symbol kill switch; "
        "EVAL primary sizing allows A+ <= $500, A <= $350, B+ <= $100 after all upstream risk caps; "
        "fresh eval resets preserve prior trade history; "
        f"broker gateway mode={config.mode.value}, armed={config.armed}, account={config.account}. "
        "PAPER is the safe default."
    )
    op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
