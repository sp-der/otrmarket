from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from src import main_80 as op80
from src import main_61 as op61
from src import main_62 as op62
from src.execution import paper as paper_module
from src.otr8 import candidates as candidates80
from src.otr8.execution_policy81 import (
    MIN_EXECUTION_RISK_DOLLARS,
    PENDING_BARS_81,
    counterfactual_expectancy81,
    eval_risk81,
    pending_expiry81,
    prepare_execution_zone81,
    quality_grade81,
    rr_decision81,
    stamp_registration81,
)
from src.strategies import early_entry72, gold_momentum72r
from src.strategies.execution_quality import evaluate_ict_context
from src.strategies.reversal import evaluate_reversal_context
from src.strategies.reversal_guard72 import assess_one_minute_reversal_context


runtime = op80.runtime
legacy = op80.legacy

_original_install_pipeline_80 = op80._install_pipeline_80
_original_quality_gate_80 = op80._quality_gate_80
_original_register_setup_81 = runtime.paper.register_setup
_original_upsert_paper_trade_81 = runtime.upsert_paper_trade
_original_pending_expiry_81 = paper_module._pending_expiry
_original_reconcile_active_72t = legacy._reconcile_active_connection_72t
_original_manifest_80 = op80._patch_runtime_manifest_80
_installed_81 = False


def _is_gc(setup) -> bool:
    return str(getattr(setup, "symbol", "") or "").upper() == "GC"


def _strategy(setup) -> str:
    return str((getattr(setup, "metadata", {}) or {}).get("strategy", "ICT_CONFLUENCE") or "ICT_CONFLUENCE").upper()


def _risk_guards81(connection, setup) -> tuple[bool, str]:
    """Preserve exposure/cooldown protections without reintroducing legacy risk multipliers."""
    return op61._risk_guards(connection, setup)


def _continuation_context81(connection, setup, histories) -> tuple[bool, str]:
    displacement = getattr(setup, "displacement", None)
    body = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
    candle_range = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
    if body < 1.50 or candle_range < 1.30:
        return False, (
            "Operation 8.1 continuation still needs confirmed resumption displacement "
            f"(>=1.50x body / >=1.30x range); got {body:.2f}x/{candle_range:.2f}x."
        )
    if histories is None:
        return False, "Operation 8.1 continuation requires multi-timeframe context."

    mtf = op61._narrative(setup, histories)
    setup.metadata["multi_timeframe_narrative_62"] = mtf
    primary_support = mtf.get("primary") == setup.direction
    if not (primary_support or mtf.get("supports_setup")):
        return False, (
            "Continuation thesis lacks higher-timeframe support: "
            f"{mtf['primary_timeframe']}={mtf['primary']}, "
            f"{mtf['intermediate_timeframe']}={mtf['intermediate']}, "
            f"{mtf['narrative_timeframe']}={mtf['narrative']}."
        )

    regime = setup.metadata.get("gold_regime_80", {}) or {}
    if str(regime.get("regime") or "") in {"CHOP", "WARMUP"}:
        return False, f"Continuation is disabled in {regime.get('regime')} regime."
    regime_direction = str(regime.get("direction") or "neutral")
    if regime_direction not in {"neutral", "", str(setup.direction)}:
        return False, f"Continuation direction opposes the active Gold regime ({regime_direction})."

    grade = "A+" if bool(mtf.get("strong_support")) and body >= 1.75 and candle_range >= 1.40 else "A"
    setup.metadata["a_plus_context"] = {
        "profile": "CONTINUATION_CONTEXT_8_1",
        "quality_grade": grade,
        "quality_score": 90 if grade == "A+" else 82,
        "context_timeframe": mtf.get("primary_timeframe"),
        "higher_timeframe_bias": mtf.get("primary"),
        "narrative": mtf,
    }
    guards_ok, guards_reason = _risk_guards81(connection, setup)
    if not guards_ok:
        return False, guards_reason
    return True, (
        f"Operation 8.1 continuation context passed as {grade}; no per-strategy daily trade quota applies."
    )


def _refresh_context81(connection, setup, histories) -> tuple[bool, str]:
    strategy = _strategy(setup)
    if strategy == "MSS_REVERSAL":
        allowed, reason, details = evaluate_reversal_context(setup, histories)
        setup.metadata["a_plus_context"] = details
        if not allowed:
            return False, reason
        if str(setup.timeframe) == "1m":
            firewall_ok, firewall_reason, firewall_details = assess_one_minute_reversal_context(setup, histories)
            if firewall_details.get("applicable"):
                setup.metadata["one_minute_reversal_guard_72"] = firewall_details
            if not firewall_ok:
                return False, firewall_reason
        return True, reason

    if strategy == "TREND_CONTINUATION_REARM":
        return _continuation_context81(connection, setup, histories)

    if strategy == "REJECTION_BLOCK_10_10":
        return True, "Rejection-block keeps its inherited 10/10 and 3R contract."

    # ICT confluence and aligned Gold momentum both reuse the established market
    # context grader. This keeps 8.1 from inventing a second definition of A/A+.
    allowed, reason, details = evaluate_ict_context(setup, histories)
    setup.metadata["a_plus_context"] = details
    return allowed, reason


def _salvageable_legacy_block81(setup, reason: str) -> bool:
    if not _is_gc(setup):
        return False
    lower = str(reason or "").lower()
    strategy = _strategy(setup)
    if strategy == "MSS_REVERSAL":
        return "reversal offers only" in lower and "1.50r" in lower
    if strategy == "TREND_CONTINUATION_REARM":
        return (
            ("continuation re-arm offers only" in lower and "1.50r" in lower)
            or "daily continuation re-arm execution limit reached" in lower
        )
    return False


def _quality_gate_81(connection, setup, histories=None):
    """A/A+-only Gold policy with evidence-aware 1.20-1.50R floors and zone activation."""
    if not _is_gc(setup):
        return _original_quality_gate_80(connection, setup, histories)

    if histories is None:
        histories = runtime.histories_snapshot()

    # 4/6 remains a chart plan only. Nothing below this line is allowed to turn
    # preview geometry into an executable order.
    if bool((getattr(setup, "metadata", {}) or {}).get("preview_only_80")):
        return _original_quality_gate_80(connection, setup, histories)

    original_allowed, original_reason = _original_quality_gate_80(connection, setup, histories)
    salvaged = False
    if not original_allowed:
        if not _salvageable_legacy_block81(setup, original_reason):
            return False, original_reason
        salvaged = True

    context_allowed, context_reason = _refresh_context81(connection, setup, histories)
    if not context_allowed:
        return False, context_reason

    # A legacy RR rejection happens before exposure/cooldown guards. Re-run those
    # guards explicitly on the only lanes 8.1 is allowed to rescue.
    if salvaged and _strategy(setup) != "TREND_CONTINUATION_REARM":
        guard_allowed, guard_reason = _risk_guards81(connection, setup)
        if not guard_allowed:
            return False, guard_reason

    evidence = counterfactual_expectancy81(connection, setup)
    rr_policy = rr_decision81(setup, evidence)
    setup.metadata["dynamic_rr_81"] = {
        "profile": "EXPECTANCY_AWARE_GOLD_RR_8_1",
        "allowed": rr_policy.allowed,
        "grade": rr_policy.grade,
        "floor": rr_policy.floor,
        "candidate_rr": round(float(setup.risk_reward or 0.0), 4),
        "reason": rr_policy.reason,
        "counterfactual_evidence": rr_policy.evidence,
        "legacy_gate_salvaged": salvaged,
    }
    if not rr_policy.allowed:
        return False, rr_policy.reason

    zone = prepare_execution_zone81(setup, rr_policy.floor)
    if not zone.allowed:
        return False, zone.reason

    # The zone uses the least-favorable first-touch edge. Re-grade that actual
    # executable geometry so an A+ midpoint cannot become a B+ edge-fill by fiat.
    final_context_allowed, final_context_reason = _refresh_context81(connection, setup, histories)
    if not final_context_allowed:
        return False, final_context_reason
    final_rr = rr_decision81(setup, evidence)
    setup.metadata["dynamic_rr_81"].update(
        final_grade=final_rr.grade,
        final_rr=round(float(setup.risk_reward or 0.0), 4),
        final_floor=final_rr.floor,
        final_allowed=final_rr.allowed,
        zone_reason=zone.reason,
    )
    if not final_rr.allowed:
        return False, final_rr.reason

    return True, (
        f"Operation 8.1 {final_rr.grade} Gold quality passed. {zone.reason} "
        f"{final_rr.reason} {final_context_reason}"
    )


# Keep the visible gate identity truthful in runtime audits/tests.
_quality_gate_81.__name__ = "_quality_gate_81"


def _setup_risk_81(decision, setup):
    applied, multiplier = eval_risk81(decision, setup)
    if applied and applied < MIN_EXECUTION_RISK_DOLLARS:
        # EvaluationRiskGuard should normally stop this first. Leave a loud
        # breadcrumb rather than silently creating a tiny trade if config drifts.
        setup.metadata.setdefault("risk_policy_81", {})["config_drift_warning"] = (
            f"Applied ${applied:.2f} is below the Operation 8.1 minimum ${MIN_EXECUTION_RISK_DOLLARS:.2f}."
        )
    return applied, multiplier


_setup_risk_81.__name__ = "_setup_risk_81"


def _register_setup_81(setup, *, risk_dollars=None, guard_reason=None):
    stamp_registration81(setup, runtime)
    return _original_register_setup_81(
        setup,
        risk_dollars=risk_dollars,
        guard_reason=guard_reason,
    )


def _upsert_paper_trade_81(connection, position, updated_at):
    _original_upsert_paper_trade_81(connection, position, updated_at)
    # Persist the registration clock and execution-zone payload so restart
    # reconciliation and the dashboard see the exact order-book plan.
    try:
        runtime.save_setup(connection, position.setup)
    except Exception as exc:
        runtime.console.log(f"Operation 8.1 setup metadata persistence warning: {exc}")


def _reconcile_active_connection_81(connection, event_time=None, current_price=None):
    """Use registration time for 8.1 pending orders while preserving 7.2T duplicate collapse."""
    if event_time is None:
        event_time, current_price = legacy._latest_event_72t(connection, "GC")
    event_time = legacy._parse_time_72t(event_time)
    if event_time is None:
        return _original_reconcile_active_72t(connection, event_time, current_price)

    expired_81 = 0
    try:
        connection.executescript(
            """
            DROP TRIGGER IF EXISTS training_trade_insert_72t;
            DROP TRIGGER IF EXISTS training_trade_update_72t;
            """
        )
        rows = connection.execute(
            """
            SELECT p.setup_id,p.timeframe,p.status,s.created_at,s.payload_json
            FROM paper_trades p
            JOIN strategy_setups s ON s.setup_id=p.setup_id
            WHERE p.status='PENDING'
            """
        ).fetchall()
        for setup_id, timeframe, status, created_at, payload_json in rows:
            registered_at = None
            if payload_json:
                try:
                    payload = json.loads(payload_json)
                    registered_at = (payload.get("metadata", {}) or {}).get("pending_registered_at_81")
                except (TypeError, ValueError, json.JSONDecodeError):
                    registered_at = None
            start = legacy._parse_time_72t(registered_at or created_at)
            if start is None:
                continue
            bars = int(PENDING_BARS_81.get(str(timeframe), 4))
            seconds = int(paper_module._BAR_SECONDS.get(str(timeframe), 60)) * bars
            if (event_time - start).total_seconds() > seconds:
                legacy._invalidate_persisted_pending_72t(
                    connection,
                    str(setup_id),
                    event_time,
                    current_price,
                    "EXPIRED_ON_RESTART_81",
                )
                expired_81 += 1
        connection.commit()
    except Exception as exc:
        runtime.console.log(f"Operation 8.1 restart-lifetime warning: {exc}")

    # Let 7.2T keep doing the valuable duplicate/open-position reconciliation,
    # but prevent its old signal-time expiry pass from invalidating 8.1 orders a
    # second time. This is startup-only and restored immediately.
    saved_bars = dict(paper_module._PENDING_BARS)
    try:
        for timeframe in tuple(paper_module._PENDING_BARS):
            paper_module._PENDING_BARS[timeframe] = 10**9
        summary = _original_reconcile_active_72t(connection, event_time, current_price)
    finally:
        paper_module._PENDING_BARS.clear()
        paper_module._PENDING_BARS.update(saved_bars)
    summary["expired"] = int(summary.get("expired", 0)) + expired_81
    return summary


def _patch_runtime_manifest_81() -> None:
    _original_manifest_80()
    path = Path(__file__).resolve().parent / "dashboard" / "static" / "runtime-build.json"
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.setdefault("build", {})["operation"] = "Operation 8.1"
        manifest["build"]["engine"] = "src.main_81"
        manifest["build"]["architecture"] = "GOLD_EXECUTION_CONVERSION_8_1"
        rules = manifest.setdefault("rules", [])
        additions = {
            "Gold first-touch entry zones": "FVG / OTE / order-block geometry executes on the conservative R:R-safe edge of the valid zone instead of waiting for one midpoint tick",
            "Registration-time pending life": "1m/5m/15m/1h pending lifetimes begin at approved order registration; strategy thesis age remains a separate clock",
            "Dynamic Gold R:R": "A+ can qualify from 1.20R, A from 1.30R, with regime and counterfactual expectancy able to tighten or cautiously relax the floor",
            "Gold eval sizing": "A+ up to $750, A and fresh 5/6 up to $500, 4/6 preview-only; lower grades do not execute",
            "No trade/profit quota": "No strategy-family trade quota or session profit lock is part of Operation 8.1; account drawdown and safety interlocks remain authoritative",
        }
        existing = {str(item.get("name")): item for item in rules if isinstance(item, dict)}
        for name, value in additions.items():
            if name in existing:
                existing[name].update(value=value, source="src/main_81.py + src/otr8/execution_policy81.py")
            else:
                rules.append({"name": name, "value": value, "source": "src/main_81.py + src/otr8/execution_policy81.py"})
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        runtime.console.log(f"Operation 8.1 manifest warning: {exc}")


def _install_pipeline_81():
    pipeline = _original_install_pipeline_80()
    pipeline.quality_gate = _quality_gate_81
    pipeline.setup_risk = _setup_risk_81
    try:
        pipeline.collector._momentum().min_rr = 1.20
    except Exception:
        pass
    runtime.evaluate_strategy = pipeline.evaluate
    return pipeline


def install_operation81() -> None:
    global _installed_81
    if _installed_81:
        return
    _installed_81 = True

    # Candidate creation must reach the dynamic policy. The final quality gate,
    # not the detector, owns whether 1.20/1.30/1.50 is actually executable.
    try:
        runtime.strategy.reversal.min_rr = 1.20
    except Exception:
        pass
    op62.continuation.min_rr = 1.20
    early_entry72.EarlyEntryPlanner72.MIN_PREARM_RR = 1.30
    candidates80.VERIFY_MODES.update({"EVAL", "EVALUATION"})
    gold_momentum72r.VERIFY_MODES.update({"EVAL", "EVALUATION"})

    paper_module._PENDING_BARS.update(PENDING_BARS_81)
    paper_module._pending_expiry = pending_expiry81
    runtime.paper.register_setup = _register_setup_81
    runtime.upsert_paper_trade = _upsert_paper_trade_81
    legacy._reconcile_active_connection_72t = _reconcile_active_connection_81

    op80._install_pipeline_80 = _install_pipeline_81
    op80._patch_runtime_manifest_80 = _patch_runtime_manifest_81


def main() -> None:
    install_operation81()
    runtime.console.log(
        "Operation 8.1 armed: Gold execution conversion uses first-touch FVG/OTE/OB zones, "
        "registration-time pending lifetimes, A/A+ dynamic 1.20-1.50R policy, explicit $750/$500 eval sizing, "
        "no strategy trade quota and no profit ceiling; 8.0 regime/arbiter/tracing plus legacy account/no-chase/exposure protections retained."
    )
    op80.main()


if __name__ == "__main__":
    main()
