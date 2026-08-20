from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable

CAPTURE_ID = "databento-glbx-20260501-20260818-v1"
HOLDOUT_START = "2026-08-07T00:00:00+00:00"
BASELINE_PENDING_LIFETIMES = {"1m": 15, "5m": 8, "15m": 4, "1h": 2}
HORIZON_MINUTES = {"1m": 45, "5m": 120, "15m": 240, "1h": 480}
BLOCK_MARKERS = ("BLOCK", "REJECT", "STALE", "DISABLED", "NO_TRADE", "RESEARCH_ONLY")


def _upper(value) -> str:
    return str(value or "").upper()


def _direction(value) -> str:
    """Normalize production strategy directions to execution-style LONG/SHORT."""
    direction = _upper(value)
    if direction in {"BULLISH", "LONG", "BUY"}:
        return "LONG"
    if direction in {"BEARISH", "SHORT", "SELL"}:
        return "SHORT"
    return direction


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def classify_gate(trace: dict) -> str:
    line = " ".join(_upper(trace.get(key)) for key in ("decision", "reason", "status"))
    if "SESSION" in line:
        return "SESSION"
    if "QUALITY" in line:
        return "QUALITY"
    if "RISK_REJECT" in line or "GEOMETRY" in line:
        return "RISK_GEOMETRY"
    if "RISK_REWARD" in line or "VALID_RR" in line or " RR" in line:
        return "RISK_REWARD"
    if "STALE" in line or "75%" in line or "TARGET_PROGRESS" in line:
        return "STALE_NO_CHASE"
    recovery = trace.get("recovery") or (trace.get("metadata") or {}).get("recovery_control_70") or {}
    recovery_mode = _upper(recovery.get("mode")) if isinstance(recovery, dict) else ""
    if "RECOVERY" in line or "COOLDOWN" in line or recovery_mode in {"SYMBOL_RECOVERY", "ACCOUNT_RECOVERY"}:
        return "RECOVERY"
    if "GUARD" in line or "EVAL" in line or "DAILY STOP" in line or "MAX LOSS" in line:
        return "EVALUATION_GUARD"
    return "OTHER_BLOCK"


def is_blocked_setup_trace(trace: dict) -> bool:
    if trace.get("event_type") != "SETUP_DECISION":
        return False
    decision = _upper(trace.get("decision"))
    reason = _upper(trace.get("reason"))
    return any(marker in decision or marker in reason for marker in BLOCK_MARKERS)


def geometry(trace: dict) -> tuple[float, float, float] | None:
    entry = trace.get("planned_entry", trace.get("entry"))
    stop = trace.get("stop")
    target = trace.get("target")
    if not all(_finite(value) for value in (entry, stop, target)):
        return None
    entry, stop, target = map(float, (entry, stop, target))
    direction = _direction(trace.get("direction"))
    valid = (direction == "LONG" and stop < entry < target) or (direction == "SHORT" and target < entry < stop)
    return (entry, stop, target) if valid else None


def load_blocked_setups(run_database: str | Path) -> tuple[dict, list[dict]]:
    path = Path(run_database).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run = dict(connection.execute("SELECT * FROM backtest_runs LIMIT 1").fetchone())
        if run.get("status") != "COMPLETE":
            raise ValueError(f"run is not COMPLETE: {path.name}")
        if run.get("end_time") > HOLDOUT_START:
            raise ValueError(f"run crosses final holdout firewall: {path.name}")
        rows = []
        for row in connection.execute("SELECT sequence_no,payload_json FROM decision_traces ORDER BY sequence_no"):
            payload = json.loads(row["payload_json"])
            if is_blocked_setup_trace(payload):
                payload["_sequence_no"] = row["sequence_no"]
                rows.append(payload)
    finally:
        connection.close()
    unique = {}
    anonymous = []
    for trace in rows:
        setup_id = trace.get("setup_id")
        if setup_id:
            unique.setdefault(str(setup_id), trace)
        else:
            anonymous.append(trace)
    return run, list(unique.values()) + anonymous


def load_causal_bars(historical_database: str | Path, start: str, end: str) -> dict[str, list[dict]]:
    path = Path(historical_database).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT cc.root_symbol,cc.open_time,cc.close_time,cc.open,cc.high,cc.low,cc.close,cc.contract
               FROM canonical_candles cc
               JOIN causal_research_series_bars rs
                 ON rs.capture_id=cc.capture_id AND rs.root_symbol=cc.root_symbol
                AND rs.open_time=cc.open_time AND rs.contract=cc.contract
              WHERE cc.capture_id=? AND cc.timeframe='1m'
                AND cc.close_time>? AND cc.close_time<=?
                AND cc.root_symbol IN ('NQ','ES','GC')
              ORDER BY cc.root_symbol,cc.close_time""",
            (CAPTURE_ID, start, min(end, HOLDOUT_START)),
        )
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            grouped[row["root_symbol"]].append(dict(row))
        return dict(grouped)
    finally:
        connection.close()


def _touches(bar: dict, price: float) -> bool:
    return float(bar["low"]) <= price <= float(bar["high"])


def evaluate_shadow(trace: dict, bars: list[dict], run_end: str) -> dict:
    base = {
        "setup_id": trace.get("setup_id"),
        "event_time": trace.get("event_time"),
        "symbol": trace.get("symbol"),
        "timeframe": trace.get("timeframe"),
        "strategy": trace.get("strategy_type"),
        "direction": _direction(trace.get("direction")),
        "grade": trace.get("setup_grade"),
        "quality_score": trace.get("quality_score"),
        "risk_reward": trace.get("risk_reward"),
        "gate": classify_gate(trace),
        "decision": trace.get("decision"),
        "reason": trace.get("reason"),
        "source_sequence": trace.get("_sequence_no"),
    }
    geom = geometry(trace)
    if not geom:
        return {**base, "outcome": "UNRESOLVED_NO_GEOMETRY", "resolved": False, "r_outcome": None}
    if not trace.get("event_time") or trace.get("symbol") not in {"NQ", "ES", "GC"}:
        return {**base, "outcome": "UNRESOLVED_BAD_CONTEXT", "resolved": False, "r_outcome": None}

    entry, stop, target = geom
    event_time = datetime.fromisoformat(trace["event_time"])
    max_end = min(datetime.fromisoformat(run_end), datetime.fromisoformat(HOLDOUT_START))
    horizon = timedelta(minutes=HORIZON_MINUTES.get(trace.get("timeframe"), 120))
    end_time = min(event_time + horizon, max_end)
    close_times = [row["close_time"] for row in bars]
    index = bisect_right(close_times, trace["event_time"])
    selected = []
    while index < len(bars) and datetime.fromisoformat(bars[index]["close_time"]) <= end_time:
        selected.append(bars[index])
        index += 1
    if not selected:
        return {**base, "entry": entry, "stop": stop, "target": target, "outcome": "UNRESOLVED_NO_FUTURE_BARS", "resolved": False, "r_outcome": None}

    direction = base["direction"]
    risk = abs(entry - stop)
    planned_r = abs(target - entry) / risk if risk else None
    entered = False
    entry_time = None
    max_high = entry
    min_low = entry
    ambiguous = False

    for bar in selected:
        if not entered:
            stop_before = _touches(bar, stop)
            target_before = _touches(bar, target)
            entry_touch = _touches(bar, entry)
            if not entry_touch:
                if stop_before:
                    return {**base, "entry": entry, "stop": stop, "target": target, "planned_r": planned_r,
                            "outcome": "INVALIDATED_BEFORE_ENTRY", "resolved": False, "r_outcome": None,
                            "terminal_time": bar["close_time"]}
                if target_before:
                    return {**base, "entry": entry, "stop": stop, "target": target, "planned_r": planned_r,
                            "outcome": "MISSED_MOVE_BEFORE_ENTRY", "resolved": False, "r_outcome": None,
                            "terminal_time": bar["close_time"]}
                continue
            entered = True
            entry_time = bar["close_time"]
        max_high = max(max_high, float(bar["high"]))
        min_low = min(min_low, float(bar["low"]))
        stop_hit = _touches(bar, stop)
        target_hit = _touches(bar, target)
        if stop_hit and target_hit:
            ambiguous = True
            outcome = "LOSS_AMBIGUOUS_STOP_FIRST"
            r_outcome = -1.0
        elif stop_hit:
            outcome = "LOSS"
            r_outcome = -1.0
        elif target_hit:
            outcome = "WIN"
            r_outcome = planned_r
        else:
            continue
        if direction == "LONG":
            mfe_r = max(0.0, (max_high - entry) / risk)
            mae_r = max(0.0, (entry - min_low) / risk)
        else:
            mfe_r = max(0.0, (entry - min_low) / risk)
            mae_r = max(0.0, (max_high - entry) / risk)
        return {**base, "entry": entry, "stop": stop, "target": target, "planned_r": planned_r,
                "entry_time": entry_time, "terminal_time": bar["close_time"], "outcome": outcome,
                "resolved": True, "r_outcome": r_outcome, "mfe_r": mfe_r, "mae_r": mae_r,
                "ambiguous_bar": ambiguous}

    if not entered:
        return {**base, "entry": entry, "stop": stop, "target": target, "planned_r": planned_r,
                "outcome": "NO_ENTRY_WITHIN_HORIZON", "resolved": False, "r_outcome": None}
    if direction == "LONG":
        mfe_r = max(0.0, (max_high - entry) / risk)
        mae_r = max(0.0, (entry - min_low) / risk)
    else:
        mfe_r = max(0.0, (entry - min_low) / risk)
        mae_r = max(0.0, (max_high - entry) / risk)
    return {**base, "entry": entry, "stop": stop, "target": target, "planned_r": planned_r,
            "entry_time": entry_time, "outcome": "OPEN_AT_HORIZON", "resolved": False,
            "r_outcome": None, "mfe_r": mfe_r, "mae_r": mae_r}


def _segment(rows: Iterable[dict], key: str) -> dict:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[str(row.get(key) or "UNKNOWN")].append(row)
    output = {}
    for name, values in sorted(buckets.items()):
        resolved = [row for row in values if row.get("resolved")]
        wins = sum(row.get("outcome") == "WIN" for row in resolved)
        losses = len(resolved) - wins
        net_r = sum(float(row.get("r_outcome") or 0) for row in resolved)
        output[name] = {
            "setups": len(values),
            "resolved": len(resolved),
            "wins": wins,
            "losses": losses,
            "resolved_win_rate": (wins / len(resolved) * 100.0) if resolved else None,
            "diagnostic_net_r": net_r,
        }
    return output


def summarize(rows: list[dict]) -> dict:
    resolved = [row for row in rows if row.get("resolved")]
    wins = [row for row in resolved if row.get("outcome") == "WIN"]
    losses = [row for row in resolved if row.get("outcome") != "WIN"]
    outcome_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        outcome_counts[row["outcome"]] += 1
    by_gate = _segment(rows, "gate")
    gate_review = {}
    for gate, values in by_gate.items():
        if values["resolved"] < 10:
            verdict = "INSUFFICIENT_SAMPLE"
        elif values["resolved_win_rate"] is not None and values["resolved_win_rate"] >= 55 and values["diagnostic_net_r"] > 0:
            verdict = "REVIEW_FOR_FOLLOWUP_RESEARCH"
        elif values["diagnostic_net_r"] < 0:
            verdict = "GATE_APPEARS_PROTECTIVE"
        else:
            verdict = "MIXED"
        gate_review[gate] = {**values, "research_verdict": verdict}
    return {
        "blocked_setups": len(rows),
        "geometry_eligible": sum(row.get("outcome") != "UNRESOLVED_NO_GEOMETRY" for row in rows),
        "resolved_counterfactuals": len(resolved),
        "shadow_winners_blocked": len(wins),
        "shadow_losses_prevented": len(losses),
        "resolved_win_rate": (len(wins) / len(resolved) * 100.0) if resolved else None,
        "diagnostic_net_r": sum(float(row.get("r_outcome") or 0) for row in resolved),
        "outcomes": dict(sorted(outcome_counts.items())),
        "by_gate": gate_review,
        "by_symbol": _segment(rows, "symbol"),
        "by_strategy": _segment(rows, "strategy"),
        "by_timeframe": _segment(rows, "timeframe"),
        "by_grade": _segment(rows, "grade"),
    }


def analyze_run(run_database: str | Path, historical_database: str | Path) -> dict:
    run, traces = load_blocked_setups(run_database)
    bars = load_causal_bars(historical_database, run["start_time"], run["end_time"])
    rows = [evaluate_shadow(trace, bars.get(trace.get("symbol"), []), run["end_time"]) for trace in traces]
    return {"run_id": run["run_id"], "start_time": run["start_time"], "end_time": run["end_time"], "rows": rows, "summary": summarize(rows)}
