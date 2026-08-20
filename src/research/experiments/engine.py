from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import median
from typing import Any

from src.research.execution.metrics import raw_metrics
from src.research.replay.runs import canonical_json


BASELINE_PENDING_LIFETIMES = {"1m": 15, "5m": 8, "15m": 4, "1h": 2}
CANDIDATE_A_PENDING_LIFETIMES = {"1m": 18, "5m": 12, "15m": 8, "1h": 5}
INCOMPLETE_CAPTURE_IDS = {"retained-operation70-phase1"}
VERDICTS = {"INCOMPLETE_DATA", "INSUFFICIENT_SAMPLE", "WORSE", "MIXED", "PROMISING", "NEEDS_OUT_OF_SAMPLE_TEST"}


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    experiment_name: str
    hypothesis: str
    baseline_run_id: str
    git_commit: str
    data_capture_id: str
    markets: tuple[str, ...]
    contracts: tuple[str, ...]
    start_time: str
    end_time: str
    replay_mode: str
    fill_model: str
    ambiguity_policy: str
    account_profile: dict
    execution_config: dict
    baseline_configuration: dict
    status: str = "DEFINED"
    created_at: str = ""


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    candidate_name: str
    run_id: str
    configuration: dict
    equivalence_status: str = "EQUIVALENT"
    created_at: str = ""


def configuration_diff(baseline: Any, candidate: Any, prefix: str = "") -> list[dict]:
    if isinstance(baseline, dict) and isinstance(candidate, dict):
        result = []
        for key in sorted(set(baseline) | set(candidate)):
            path = f"{prefix}.{key}" if prefix else key
            result.extend(configuration_diff(baseline.get(key), candidate.get(key), path))
        return result
    if baseline != candidate:
        return [{"path": prefix, "baseline": baseline, "candidate": candidate}]
    return []


EQUIVALENCE_FIELDS = (
    "git_commit", "capture_id", "start_time", "end_time", "replay_mode", "fill_model",
    "ambiguity_policy", "markets", "contracts", "account_profile", "execution_config",
)


def enforce_equivalence(baseline: dict, candidate: dict, allowed_configuration_paths: set[str], allow_non_equivalent: bool = False) -> str:
    mismatches = [field for field in EQUIVALENCE_FIELDS if baseline.get(field) != candidate.get(field)]
    config_changes = configuration_diff(baseline.get("configuration", {}), candidate.get("configuration", {}))
    unauthorized = [item["path"] for item in config_changes if item["path"] not in allowed_configuration_paths]
    if mismatches or unauthorized:
        if allow_non_equivalent:
            return "NON_EQUIVALENT"
        details = ", ".join(mismatches + unauthorized)
        raise ValueError(f"Non-equivalent experiment comparison: {details}")
    return "EQUIVALENT"


def _trace_payload(trace: dict) -> dict:
    return trace.get("payload") or trace.get("payload_json") or {}


def thesis_identity(trace: dict) -> tuple[str, str, str]:
    payload = _trace_payload(trace)
    explicit = payload.get("thesis_id") or payload.get("deterministic_thesis_id")
    if explicit:
        return str(explicit), "EXPLICIT_THESIS_ID", "HIGH"
    catalyst = trace.get("catalyst") or payload.get("catalyst") or {}
    structure = {
        "symbol": trace.get("symbol"), "strategy": trace.get("strategy_type"),
        "direction": trace.get("direction"), "timeframe": trace.get("timeframe"),
        "signal_time": payload.get("signal_time") or trace.get("event_time"),
        "pd_array_id": payload.get("pd_array_id") or catalyst.get("pd_array_id"),
        "catalyst_time": payload.get("catalyst_time") or catalyst.get("timestamp"),
        "displacement_time": payload.get("displacement_time"), "fvg_id": payload.get("fvg_id"),
    }
    populated = sum(value is not None for value in structure.values())
    confidence = "HIGH" if populated >= 8 else "MEDIUM" if populated >= 6 else "UNKNOWN"
    return digest(structure), "STRUCTURAL_FIELDS", confidence


def _setup_records(traces: list[dict], trades: list[dict]) -> dict[str, dict]:
    by_setup: dict[str, dict] = {}
    for trace in traces:
        setup_id = trace.get("setup_id")
        if not setup_id:
            continue
        item = by_setup.setdefault(setup_id, {"setup_id": setup_id, "traces": [], "trade": None})
        item["traces"].append(trace)
        if not item.get("identity_trace") and trace.get("event_type") in {"SETUP_DETECTED", "SETUP_DECISION", "ORDER_STATE"}:
            item["identity_trace"] = trace
    for trade in trades:
        setup_id = trade.get("setup_id")
        item = by_setup.setdefault(setup_id, {"setup_id": setup_id, "traces": [], "trade": None})
        item["trade"] = trade
    for item in by_setup.values():
        trace = item.get("identity_trace") or (item["traces"][0] if item["traces"] else {})
        item["thesis_key"], item["matching_basis"], item["matching_confidence"] = thesis_identity(trace)
        item.update({key: trace.get(key) for key in ("symbol", "timeframe", "strategy_type", "direction", "setup_grade", "event_time")})
        item["expired_trace"] = next((t for t in item["traces"] if "EXPIRE" in f"{t.get('event_type','')} {t.get('decision','')} {t.get('reason','')}".upper()), None)
    return by_setup


def _trade_filled(item: dict | None) -> bool:
    return bool(item and item.get("trade") and item["trade"].get("fill_time"))


def match_setups(baseline_traces: list[dict], candidate_traces: list[dict], baseline_trades: list[dict], candidate_trades: list[dict], divergence_time: str | None = None) -> list[dict]:
    base = _setup_records(baseline_traces, baseline_trades)
    cand = _setup_records(candidate_traces, candidate_trades)
    base_keys = {item["thesis_key"]: item for item in base.values() if item["matching_confidence"] != "UNKNOWN"}
    cand_keys = {item["thesis_key"]: item for item in cand.values() if item["matching_confidence"] != "UNKNOWN"}
    matches = []
    for key in sorted(set(base_keys) | set(cand_keys)):
        b, c = base_keys.get(key), cand_keys.get(key)
        classification = "BASELINE_ONLY" if b and not c else "CANDIDATE_ONLY" if c and not b else "SAME_SETUP_BOTH_FILL"
        if (not b or not c) and divergence_time and str((b or c).get("event_time") or "") > divergence_time:
            classification = "DOWNSTREAM_DIVERGENCE"
        if b and c:
            b_expired, c_filled = bool(b.get("expired_trace")), _trade_filled(c)
            if b_expired and c_filled:
                classification = "SAME_SETUP_BASELINE_EXPIRED_CANDIDATE_FILLED"
            elif b_expired and (c.get("expired_trace") or not _trade_filled(c)):
                classification = "SAME_SETUP_BASELINE_EXPIRED_CANDIDATE_EXPIRED"
            elif _trade_filled(b) and _trade_filled(c):
                bt, ct = b["trade"], c["trade"]
                classification = "SAME_SETUP_DIFFERENT_EXIT" if (bt.get("exit_reason"), bt.get("net_pnl")) != (ct.get("exit_reason"), ct.get("net_pnl")) else "SAME_SETUP_BOTH_FILL"
            elif divergence_time and str((b or c).get("event_time") or "") > divergence_time:
                classification = "DOWNSTREAM_DIVERGENCE"
        payload = {
            "thesis_key": key, "baseline_setup_id": b.get("setup_id") if b else None,
            "candidate_setup_id": c.get("setup_id") if c else None, "classification": classification,
            "matching_basis": (b or c)["matching_basis"], "matching_confidence": (b or c)["matching_confidence"],
            "baseline": b, "candidate": c,
        }
        matches.append(payload)
    return matches


def first_divergence(baseline: list[dict], candidate: list[dict]) -> dict | None:
    fields = ("event_time", "event_type", "symbol", "timeframe", "strategy_type", "direction", "decision", "reason")
    left = [tuple(row.get(field) for field in fields) for row in baseline]
    right = [tuple(row.get(field) for field in fields) for row in candidate]
    for index in range(max(len(left), len(right))):
        b = left[index] if index < len(left) else None
        c = right[index] if index < len(right) else None
        if b != c:
            times = [value[0] for value in (b, c) if value and value[0]]
            return {"index": index, "event_time": min(times) if times else None, "baseline": b, "candidate": c}
    return None


BEHAVIOR_RULES = {
    "scanner_candidates": ("SCANNER",), "accepted_setups": ("ACCEPT", "SETUP_READY"),
    "blocked_setups": ("BLOCK",), "pending_orders": ("PENDING",), "fills": ("FILL",),
    "expirations": ("EXPIRE",), "stale_cancellations": ("STALE",),
    "stop_before_entry_cancellations": ("STOP_BREACHED_BEFORE_ENTRY",),
    "target_progress_cancellations": ("TARGET_PROGRESS_75",), "smt_suppressions": ("SMT_SUPPRESS",),
    "recovery_activations": ("RECOVERY",), "symbol_recovery_activations": ("SYMBOL_RECOVERY",),
    "account_recovery_activations": ("ACCOUNT_RECOVERY",), "b_plus_blocks": ("B+", "BLOCK"),
    "cooldown_blocks": ("COOLDOWN", "RESET WINDOW"), "risk_reductions": ("REDUCED", "RISK CAP"),
    "continuation_rearms": ("CONTINUATION", "REARM"), "concurrency_blocks": ("CONCURRENT",),
}


def behavior_metrics(traces: list[dict], trades: list[dict]) -> dict:
    lines = [f"{row.get('event_type','')} {row.get('decision','')} {row.get('reason','')}".upper() for row in traces]
    result = {key: sum(all(word in line for word in words) for line in lines) for key, words in BEHAVIOR_RULES.items()}
    pending_bars = []
    for row in traces:
        payload = _trace_payload(row)
        if payload.get("bars_elapsed") is not None:
            pending_bars.append(float(payload["bars_elapsed"]))
    result["orders_remained_alive_never_filled"] = sum(t.get("status") == "PENDING" and not t.get("fill_time") for t in trades)
    result["average_pending_bars"] = sum(pending_bars) / len(pending_bars) if pending_bars else 0
    result["median_pending_bars"] = median(pending_bars) if pending_bars else 0
    return result


MONETARY_FIELDS = (
    "net_pnl", "gross_pnl", "win_rate", "profit_factor", "expectancy_dollars", "expectancy_r",
    "average_r", "average_winner", "average_loser", "payoff_ratio", "maximum_drawdown_dollars",
    "maximum_drawdown_percent", "maximum_intraday_drawdown", "maximum_realized_drawdown",
    "return_percent", "ending_balance", "wins", "losses", "commissions", "fees",
    "adverse_slippage", "price_improvement", "total_trades",
)


def extended_metrics(trades: list[dict], equity: list[dict], starting_balance: float = 0) -> tuple[dict, dict]:
    metrics, segments = raw_metrics(trades, equity)
    closed = [row for row in trades if row.get("status") == "CLOSED"]
    ending = float(equity[-1].get("balance")) if equity else starting_balance + float(metrics["net_pnl"])
    metrics.update({
        "ending_balance": ending, "return_percent": 100 * (ending - starting_balance) / starting_balance if starting_balance else None,
        "wins": sum(float(row.get("net_pnl") or 0) > 0 for row in closed),
        "losses": sum(float(row.get("net_pnl") or 0) < 0 for row in closed),
        "commissions": sum(float(row.get("commission") or 0) for row in closed),
        "fees": sum(float(row.get("fees") or 0) for row in closed),
        "adverse_slippage": sum(float(row.get("adverse_slippage_cost") or 0) for row in closed),
        "price_improvement": sum(float(row.get("price_improvement") or 0) for row in closed),
        "maximum_realized_drawdown": max([0] + [float(row.get("realized_drawdown") or 0) for row in equity]),
    })
    return metrics, segments


def deltas(baseline: dict, candidate: dict, fields=MONETARY_FIELDS) -> dict:
    result = {}
    for field in fields:
        b, c = baseline.get(field), candidate.get(field)
        absolute = c - b if isinstance(b, (int, float)) and isinstance(c, (int, float)) else None
        percentage = 100 * absolute / abs(b) if absolute is not None and b not in (None, 0) else None
        result[field] = {"baseline": b, "candidate": c, "absolute_delta": absolute, "percentage_delta": percentage}
    return result


def segment_deltas(baseline: dict, candidate: dict) -> dict:
    result = {}
    for dimension in sorted(set(baseline) | set(candidate)):
        segments = {}
        for segment in sorted(set(baseline.get(dimension, {})) | set(candidate.get(dimension, {}))):
            b, c = baseline.get(dimension, {}).get(segment), candidate.get(dimension, {}).get(segment)
            if b is not None or c is not None:
                segments[segment] = {"baseline": b, "candidate": c, "deltas": deltas(b or {}, c or {})}
        if segments:
            result[dimension] = segments
    return result


def pending_timeframe_analysis(matches: list[dict], baseline_lifetimes: dict, candidate_lifetimes: dict) -> dict:
    result = {}
    for timeframe in ("1m", "5m", "15m", "1h"):
        items = [m for m in matches if (m.get("baseline") or m.get("candidate") or {}).get("timeframe") == timeframe]
        kept = [m for m in items if m["classification"].startswith("SAME_SETUP_BASELINE_EXPIRED_CANDIDATE")]
        filled = [m for m in kept if m["classification"].endswith("FILLED")]
        wins = [m for m in filled if ((m.get("candidate") or {}).get("trade") or {}).get("net_pnl", 0) > 0]
        losses = [m for m in filled if ((m.get("candidate") or {}).get("trade") or {}).get("net_pnl", 0) < 0]
        additional = []
        for match in filled:
            b, c = match["baseline"], match["candidate"]
            expired = b.get("expired_trace") or {}
            trade = c.get("trade") or {}
            payload = _trace_payload(expired)
            bars_after_expiration = None
            if expired.get("event_time") and trade.get("fill_time"):
                expired_at = datetime.fromisoformat(str(expired["event_time"]).replace("Z", "+00:00"))
                filled_at = datetime.fromisoformat(str(trade["fill_time"]).replace("Z", "+00:00"))
                bar_minutes = {"1m": 1, "5m": 5, "15m": 15, "1h": 60}[timeframe]
                bars_after_expiration = max(0, (filled_at - expired_at).total_seconds() / (bar_minutes * 60))
                additional.append(bars_after_expiration)
            bars_elapsed = float(payload.get("bars_elapsed") or baseline_lifetimes[timeframe])
            match["pending_lifetime_effect"] = {
                "baseline_expiration_time": expired.get("event_time"),
                "structure_valid_at_baseline_expiration": payload.get("structure_valid_at_expiration", "UNKNOWN"),
                "bars_elapsed": payload.get("bars_elapsed"), "candidate_remaining_lifetime": max(0, candidate_lifetimes[timeframe] - bars_elapsed),
                "candidate_later_fills": True, "bars_from_baseline_expiration_to_candidate_fill": bars_after_expiration,
                "fill_price": trade.get("actual_fill"), "stop": trade.get("stop_price"),
                "target": trade.get("target_price"), "resulting_r": trade.get("realized_r"), "net_pnl": trade.get("net_pnl"),
                "mfe": trade.get("mfe_r"), "mae": trade.get("mae_r"), "eventual_cancellation_reason": trade.get("exit_reason"),
            }
        validity = {"VALID": 0, "INVALID": 0, "UNKNOWN": 0}
        for match in kept:
            state = _trace_payload((match.get("baseline") or {}).get("expired_trace") or {}).get("structure_valid_at_expiration", "UNKNOWN")
            state = str(state).upper() if state is not True and state is not False else "VALID" if state else "INVALID"
            validity[state if state in validity else "UNKNOWN"] += 1
        result[timeframe] = {
            "baseline_lifetime": baseline_lifetimes[timeframe], "candidate_lifetime": candidate_lifetimes[timeframe],
            "baseline_expirations": sum(bool((m.get("baseline") or {}).get("expired_trace")) for m in items),
            "candidate_expirations": sum(bool((m.get("candidate") or {}).get("expired_trace")) for m in items),
            "baseline_expired_setups_kept_alive": len(kept), "later_fills": len(filled), "later_winners": len(wins),
            "later_losers": len(losses), "later_cancelled": len(kept) - len(filled), "never_filled": len(kept) - len(filled),
            "average_additional_bars_before_fill": sum(additional) / len(additional) if additional else None,
            "median_additional_bars_before_fill": median(additional) if additional else None,
            "added_gross_pnl": sum(((m.get("candidate") or {}).get("trade") or {}).get("gross_pnl") or 0 for m in filled),
            "added_net_pnl": sum(((m.get("candidate") or {}).get("trade") or {}).get("net_pnl") or 0 for m in filled),
            "added_max_drawdown_contribution": None, "structure_validity": validity,
        }
    return result


def verdict(metrics: dict, segments: dict, data_quality: str, minimum_sample: int = 30) -> dict:
    samples = {"portfolio": int(metrics.get("candidate", {}).get("total_trades") or 0)}
    for dimension in ("symbol", "direction", "strategy_type", "timeframe", "session"):
        samples[dimension] = {key: int((value.get("candidate") or {}).get("total_trades") or 0) for key, value in segments.get(dimension, {}).items()}
    if data_quality != "COMPLETE":
        return {"verdict": "INCOMPLETE_DATA", "reasons": ["Historical capture is incomplete; monetary deltas are diagnostic only."], "sample_counts": samples}
    if samples["portfolio"] < minimum_sample:
        return {"verdict": "INSUFFICIENT_SAMPLE", "reasons": [f"Candidate has {samples['portfolio']} trades; minimum is {minimum_sample}."], "sample_counts": samples}
    delta = metrics["deltas"]
    expectancy = delta["expectancy_dollars"]["absolute_delta"] or 0
    pf = delta["profit_factor"]["absolute_delta"] or 0
    dd = delta["maximum_drawdown_dollars"]["absolute_delta"] or 0
    if expectancy < 0 and pf < 0 and dd > 0:
        value, reasons = "WORSE", ["Expectancy and profit factor fell while drawdown increased."]
    elif expectancy > 0 and pf > 0 and dd <= 0:
        value, reasons = "NEEDS_OUT_OF_SAMPLE_TEST", ["Multiple dimensions improved; out-of-sample testing is mandatory."]
    elif expectancy > 0 or pf > 0:
        value, reasons = "PROMISING", ["Some quality dimensions improved, but consistency is not established."]
    else:
        value, reasons = "MIXED", ["Results do not agree across expectancy, profit factor, and drawdown."]
    assert value != "PRODUCTION_READY"
    return {"verdict": value, "reasons": reasons, "sample_counts": samples}


def _sanitize(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def compare_runs(*, experiment_id: str, candidate_id: str, baseline_manifest: dict, candidate_manifest: dict,
                 baseline_traces: list[dict], candidate_traces: list[dict], baseline_trades: list[dict],
                 candidate_trades: list[dict], baseline_equity: list[dict], candidate_equity: list[dict],
                 data_quality_status: str, minimum_sample: int = 30, allow_non_equivalent: bool = False) -> dict:
    if baseline_manifest.get("capture_id") in INCOMPLETE_CAPTURE_IDS:
        data_quality_status = "INCOMPLETE"
    allowed = {f"pending_lifetime_bars.{tf}" for tf in BASELINE_PENDING_LIFETIMES}
    equivalence = enforce_equivalence(baseline_manifest, candidate_manifest, allowed, allow_non_equivalent)
    divergence = first_divergence(baseline_traces, candidate_traces)
    matches = match_setups(baseline_traces, candidate_traces, baseline_trades, candidate_trades, (divergence or {}).get("event_time"))
    starting_balance = float((baseline_manifest.get("account_profile") or {}).get("starting_balance") or 0)
    b_metrics, b_segments = extended_metrics(baseline_trades, baseline_equity, starting_balance)
    c_metrics, c_segments = extended_metrics(candidate_trades, candidate_equity, starting_balance)
    metric_delta = deltas(b_metrics, c_metrics)
    behavior_b, behavior_c = behavior_metrics(baseline_traces, baseline_trades), behavior_metrics(candidate_traces, candidate_trades)
    behavior_delta = deltas(behavior_b, behavior_c, tuple(sorted(set(behavior_b) | set(behavior_c))))
    behavior_delta = {key: {"baseline": value["baseline"], "candidate": value["candidate"], "absolute_delta": value["absolute_delta"]} for key, value in behavior_delta.items()}
    segments = segment_deltas(b_segments, c_segments)
    baseline_lifetimes = baseline_manifest["configuration"]["pending_lifetime_bars"]
    candidate_lifetimes = candidate_manifest["configuration"]["pending_lifetime_bars"]
    timeframes = pending_timeframe_analysis(matches, baseline_lifetimes, candidate_lifetimes)
    metric_bundle = {"baseline": b_metrics, "candidate": c_metrics, "deltas": metric_delta}
    final_verdict = verdict(metric_bundle, segments, data_quality_status, minimum_sample)
    result = _sanitize({
        "experiment_id": experiment_id, "candidate_id": candidate_id,
        "created_at": datetime.now(timezone.utc).isoformat(), "equivalence_status": equivalence,
        "first_divergence": divergence, "setup_matches": matches, "metric_deltas": metric_delta,
        "behavior_deltas": behavior_delta, "segment_deltas": segments, "timeframe_analysis": timeframes,
        "metrics": metric_bundle, "verdict": final_verdict, "data_quality_status": data_quality_status,
    })
    result["digests"] = {
        "baseline": digest({"manifest": baseline_manifest, "traces": baseline_traces, "trades": baseline_trades, "equity": baseline_equity}),
        "candidate": digest({"manifest": candidate_manifest, "traces": candidate_traces, "trades": candidate_trades, "equity": candidate_equity}),
        "matches": digest(result["setup_matches"]), "metrics": digest(result["metric_deltas"]),
        "behavior": digest(result["behavior_deltas"]), "verdict": digest(result["verdict"]),
    }
    result["digests"]["comparison"] = digest({key: result["digests"][key] for key in ("baseline", "candidate", "matches", "metrics", "behavior", "verdict")})
    return result


class ExperimentEngine:
    def __init__(self, store):
        self.store = store

    def define(self, spec: ExperimentSpec, candidates: list[CandidateSpec]) -> dict:
        created = spec.created_at or datetime.now(timezone.utc).isoformat()
        definition = asdict(spec)
        definition["created_at"] = created
        definition["data_quality_status"] = "INCOMPLETE" if spec.data_capture_id in INCOMPLETE_CAPTURE_IDS else "COMPLETE"
        definition["definition_digest"] = digest(definition)
        candidate_rows = []
        for candidate in candidates:
            row = asdict(candidate)
            row["created_at"] = candidate.created_at or created
            row["configuration_diff"] = configuration_diff(spec.baseline_configuration, candidate.configuration)
            row["definition_digest"] = digest({"experiment": definition["definition_digest"], **row})
            candidate_rows.append(row)
        self.store.create(definition, candidate_rows)
        return {"definition": definition, "candidates": candidate_rows}

    def persist_comparison(self, result: dict) -> int:
        return self.store.append_comparison(result)


class PairedReplayExecutor:
    """Run a frozen baseline and one candidate through the same ReplayRunner."""

    def __init__(self, replay_runner):
        self.replay_runner = replay_runner

    def run(self, *, experiment_id: str, candidate_id: str, baseline_manifest, candidate_run_id: str,
            candidate_lifetimes: dict, historical_database, data_quality_status: str, minimum_sample: int = 30) -> dict:
        baseline_lifetimes = dict(baseline_manifest.pending_lifetime_bars)
        if baseline_lifetimes != BASELINE_PENDING_LIFETIMES:
            raise ValueError("Baseline pending lifetimes do not match authoritative Operation 7.0")
        candidate_manifest = replace(
            baseline_manifest, run_id=candidate_run_id, parent_run_id=baseline_manifest.run_id,
            pending_lifetime_bars=dict(candidate_lifetimes), created_at="",
        )
        baseline_result = self.replay_runner.run(baseline_manifest, historical_database)
        candidate_result = self.replay_runner.run(candidate_manifest, historical_database)
        def comparison_manifest(manifest):
            value = asdict(manifest)
            value["capture_id"] = value.pop("capture_id")
            value["markets"] = list(value.pop("markets"))
            value["contracts"] = list(value.pop("contracts"))
            value["configuration"] = {**value.get("configuration", {}), "pending_lifetime_bars": dict(manifest.pending_lifetime_bars)}
            return value
        return compare_runs(
            experiment_id=experiment_id, candidate_id=candidate_id,
            baseline_manifest=comparison_manifest(baseline_manifest), candidate_manifest=comparison_manifest(candidate_manifest),
            baseline_traces=baseline_result.get("traces", []), candidate_traces=candidate_result.get("traces", []),
            baseline_trades=baseline_result.get("execution_trades", []), candidate_trades=candidate_result.get("execution_trades", []),
            baseline_equity=baseline_result.get("equity", []), candidate_equity=candidate_result.get("equity", []),
            data_quality_status=data_quality_status, minimum_sample=minimum_sample,
        )
