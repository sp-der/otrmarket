from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import statistics

TIMEFRAME_MINUTES = {"1m": 1.0, "5m": 5.0, "15m": 15.0, "1h": 60.0}

STRATEGY_ACTIONABLE_STAGES = {
    "ICT_CONFLUENCE": {
        "WAIT_SIGNAL", "WAIT_DISPLACEMENT", "WAIT_ENTRY_FVG",
        "WAIT_QUALIFYING_FVG", "WAIT_VALID_RR", "SETUP_READY",
    },
    "REJECTION_BLOCK_10_10": {
        "RB_WAIT_SMT", "RB_WAIT_DISPLACEMENT", "RB_WAIT_MSS_BOS",
        "RB_WAIT_RETRACE", "SETUP_READY",
    },
    "MSS_REVERSAL": {"WAIT_ENTRY_FVG", "WAIT_PULLBACK", "SETUP_READY"},
    "TREND_CONTINUATION_REARM": {
        "WAIT_PULLBACK", "WAIT_RESUMPTION", "WAIT_FRESH_FVG",
        "WAIT_VALID_REARM", "SETUP_READY",
    },
}

STRATEGY_BOUNDARY_STAGES = {
    "ICT_CONFLUENCE": {"WAIT_PD_ARRAY", "WARMUP", "EXPIRED", "SETUP_READY"},
    "REJECTION_BLOCK_10_10": {
        "RB_WARMUP", "RB_WAIT_BIAS", "RB_WAIT_SWEEP", "RB_WAIT_REJECTION",
        "RB_WAIT_STRUCTURE", "RB_EXPIRED", "RB_INVALIDATED",
        "RB_REJECTED_9_OF_10", "SETUP_READY",
    },
    "MSS_REVERSAL": {"WARMUP", "WAIT_REVERSAL", "EXPIRED", "SETUP_READY"},
    "TREND_CONTINUATION_REARM": {"EXPIRED", "SETUP_READY"},
}

PRE_ENTRY_STAGES = {
    "ICT_CONFLUENCE": {"WAIT_SIGNAL", "WAIT_DISPLACEMENT"},
    "REJECTION_BLOCK_10_10": {"RB_WAIT_SMT", "RB_WAIT_DISPLACEMENT", "RB_WAIT_MSS_BOS"},
    "MSS_REVERSAL": set(),
    "TREND_CONTINUATION_REARM": {"WAIT_RESUMPTION"},
}

ENTRY_SEARCH_STAGES = {
    "ICT_CONFLUENCE": {"WAIT_ENTRY_FVG", "WAIT_QUALIFYING_FVG", "WAIT_VALID_RR"},
    "REJECTION_BLOCK_10_10": {"RB_WAIT_RETRACE"},
    "MSS_REVERSAL": {"WAIT_ENTRY_FVG", "WAIT_PULLBACK"},
    "TREND_CONTINUATION_REARM": {"WAIT_PULLBACK", "WAIT_FRESH_FVG", "WAIT_VALID_REARM"},
}

# Hard research-only plausibility bounds. These are deliberately looser than the
# current strategy timers and prevent a missing diagnostic/reset from turning an
# unrelated episode into multi-day latency.
MAX_EPISODE_BARS = {
    "ICT_CONFLUENCE": 64,
    "REJECTION_BLOCK_10_10": 56,
    "MSS_REVERSAL": 16,
    "TREND_CONTINUATION_REARM": 40,
}


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _infer_strategy(payload: dict, diagnostic: dict, stage: str, note: str) -> str:
    explicit = payload.get("strategy_type") or diagnostic.get("strategy_name")
    if explicit:
        return str(explicit)
    upper_stage = str(stage or "").upper()
    upper_note = str(note or "").upper()
    if upper_stage.startswith("RB_") or upper_note.startswith("RB "):
        return "REJECTION_BLOCK_10_10"
    if "REVERSAL" in upper_note or upper_stage == "WAIT_REVERSAL":
        return "MSS_REVERSAL"
    if "CONTINUATION" in upper_note or upper_stage in {"WAIT_RESUMPTION", "WAIT_FRESH_FVG", "WAIT_VALID_REARM"}:
        return "TREND_CONTINUATION_REARM"
    return "ICT_CONFLUENCE"


def load_scanner_states(run_database: str | Path) -> dict[tuple[str, str, str], list[dict]]:
    path = Path(run_database).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    try:
        for row in connection.execute(
            "SELECT sequence_no,payload_json FROM decision_traces WHERE event_type='SCANNER_STATE' ORDER BY sequence_no"
        ):
            payload = json.loads(row["payload_json"])
            diagnostic = payload.get("diagnostic") or {}
            symbol = payload.get("symbol") or diagnostic.get("symbol")
            timeframe = payload.get("timeframe") or diagnostic.get("timeframe")
            stage = diagnostic.get("stage") or payload.get("decision")
            event_time = payload.get("event_time") or diagnostic.get("market_time")
            note = payload.get("reason") or diagnostic.get("note") or ""
            if not symbol or not timeframe or not stage or not event_time:
                continue
            strategy = _infer_strategy(payload, diagnostic, str(stage), str(note))
            grouped[(str(symbol), str(timeframe), strategy)].append({
                "sequence_no": int(row["sequence_no"]),
                "event_time": str(event_time),
                "stage": str(stage).upper(),
                "strategy": strategy,
                "direction": str(payload.get("direction") or diagnostic.get("direction") or "").upper(),
                "note": note,
            })
    finally:
        connection.close()
    return dict(grouped)


def _episode_before_decision(row: dict, states: list[dict]) -> tuple[list[dict], bool]:
    decision_time = _dt(row.get("event_time"))
    if decision_time is None:
        return [], False
    strategy = str(row.get("strategy") or "ICT_CONFLUENCE")
    timeframe = str(row.get("timeframe") or "1m")
    actionable = STRATEGY_ACTIONABLE_STAGES.get(strategy, set())
    boundaries = STRATEGY_BOUNDARY_STAGES.get(strategy, {"EXPIRED", "SETUP_READY"})
    eligible = [state for state in states if (_dt(state.get("event_time")) or decision_time) <= decision_time]
    if not eligible:
        return [], False

    boundary_index = -1
    for index, state in enumerate(eligible[:-1]):
        if state.get("stage") in boundaries:
            boundary_index = index
    episode = eligible[boundary_index + 1 :]
    if not any(state.get("stage") in actionable for state in episode):
        return [], False

    tf_minutes = TIMEFRAME_MINUTES.get(timeframe, 1.0)
    max_minutes = MAX_EPISODE_BARS.get(strategy, 64) * tf_minutes
    cutoff = decision_time - timedelta(minutes=max_minutes)
    truncated = any((_dt(state.get("event_time")) or decision_time) < cutoff for state in episode)
    if truncated:
        episode = [state for state in episode if (_dt(state.get("event_time")) or decision_time) >= cutoff]
    if not any(state.get("stage") in actionable for state in episode):
        return [], truncated
    return episode, truncated


def enrich_stage_latency(rows: list[dict], states_by_key: dict[tuple[str, str, str], list[dict]]) -> list[dict]:
    output = []
    for row in rows:
        item = dict(row)
        strategy = str(item.get("strategy") or "ICT_CONFLUENCE")
        key = (str(item.get("symbol")), str(item.get("timeframe")), strategy)
        states = states_by_key.get(key, [])
        episode, truncated = _episode_before_decision(item, states)
        decision_time = _dt(item.get("event_time"))
        actionable_stages = STRATEGY_ACTIONABLE_STAGES.get(strategy, set())
        pre_stages = PRE_ENTRY_STAGES.get(strategy, set())
        entry_stages = ENTRY_SEARCH_STAGES.get(strategy, set())
        if not episode or decision_time is None:
            item.update({
                "stage_episode_found": False,
                "exact_strategy_match": bool(states),
                "episode_truncated_to_plausibility_bound": truncated,
                "episode_minutes": None,
                "pre_entry_minutes": None,
                "entry_search_minutes": None,
                "dominant_stage": None,
                "stage_at_decision": None,
                "stage_dwell_minutes": {},
            })
            output.append(item)
            continue

        dwell: dict[str, float] = defaultdict(float)
        for index, state in enumerate(episode):
            start = _dt(state.get("event_time"))
            if start is None:
                continue
            if index + 1 < len(episode):
                end = _dt(episode[index + 1].get("event_time")) or decision_time
            else:
                end = decision_time
            minutes = max(0.0, (end - start).total_seconds() / 60.0)
            dwell[state.get("stage") or "UNKNOWN"] += minutes

        actionable = [state for state in episode if state.get("stage") in actionable_stages]
        first_time = _dt(actionable[0].get("event_time")) if actionable else None
        episode_minutes = max(0.0, (decision_time - first_time).total_seconds() / 60.0) if first_time else None
        pre_entry_minutes = sum(value for stage, value in dwell.items() if stage in pre_stages)
        entry_search_minutes = sum(value for stage, value in dwell.items() if stage in entry_stages)
        relevant_dwell = {stage: value for stage, value in dwell.items() if stage in actionable_stages and stage != "SETUP_READY"}
        dominant_stage = max(relevant_dwell, key=relevant_dwell.get) if relevant_dwell else None
        first_entry_candidate = next(
            (_dt(state.get("event_time")) for state in actionable if state.get("stage") in entry_stages | {"SETUP_READY"}),
            None,
        )

        item.update({
            "stage_episode_found": True,
            "exact_strategy_match": True,
            "episode_truncated_to_plausibility_bound": truncated,
            "episode_minutes": episode_minutes,
            "pre_entry_minutes": pre_entry_minutes,
            "entry_search_minutes": entry_search_minutes,
            "dominant_stage": dominant_stage,
            "stage_at_decision": actionable[-1].get("stage") if actionable else None,
            "stage_dwell_minutes": dict(sorted(dwell.items())),
            "minutes_from_first_entry_candidate_to_decision": (
                max(0.0, (decision_time - first_entry_candidate).total_seconds() / 60.0)
                if first_entry_candidate else None
            ),
        })
        output.append(item)
    return output


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _summary(rows: list[dict]) -> dict:
    matched = [row for row in rows if row.get("stage_episode_found")]
    dominant: dict[str, int] = defaultdict(int)
    stage_dwell: dict[str, list[float]] = defaultdict(list)
    for row in matched:
        if row.get("dominant_stage"):
            dominant[str(row["dominant_stage"])] += 1
        for stage, minutes in (row.get("stage_dwell_minutes") or {}).items():
            stage_dwell[stage].append(float(minutes))
    pre = [float(row["pre_entry_minutes"]) for row in matched if row.get("pre_entry_minutes") is not None]
    entry = [float(row["entry_search_minutes"]) for row in matched if row.get("entry_search_minutes") is not None]
    episodes = [float(row["episode_minutes"]) for row in matched if row.get("episode_minutes") is not None]
    return {
        "setups": len(rows),
        "exact_strategy_state_available": sum(bool(row.get("exact_strategy_match")) for row in rows),
        "matched_stage_episodes": len(matched),
        "matched_stage_episode_pct": (len(matched) / len(rows) * 100.0) if rows else None,
        "truncated_to_plausibility_bound": sum(bool(row.get("episode_truncated_to_plausibility_bound")) for row in matched),
        "median_episode_minutes": _median(episodes),
        "median_pre_entry_minutes": _median(pre),
        "median_entry_search_minutes": _median(entry),
        "dominant_stage_counts": dict(sorted(dominant.items())),
        "median_stage_dwell_minutes": {stage: _median(values) for stage, values in sorted(stage_dwell.items())},
    }


def _segment(rows: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "UNKNOWN")].append(row)
    return {name: _summary(values) for name, values in sorted(groups.items())}


def summarize_stage_latency(rows: list[dict]) -> dict:
    summary = _summary(rows)
    matched = [row for row in rows if row.get("stage_episode_found")]
    total_pre = sum(float(row.get("pre_entry_minutes") or 0.0) for row in matched)
    total_entry = sum(float(row.get("entry_search_minutes") or 0.0) for row in matched)
    if total_entry > total_pre:
        lead = "ENTRY_FORMATION_OR_ENTRY_DEPTH"
    elif total_pre > total_entry:
        lead = "SIGNAL_OR_DISPLACEMENT_CONFIRMATION"
    else:
        lead = "MIXED_OR_INSUFFICIENT"
    summary.update({
        "aggregate_pre_entry_minutes": total_pre,
        "aggregate_entry_search_minutes": total_entry,
        "primary_latency_bucket": lead,
        "by_gate": _segment(rows, "gate"),
        "by_symbol": _segment(rows, "symbol"),
        "by_timeframe": _segment(rows, "timeframe"),
        "by_strategy": _segment(rows, "strategy"),
    })
    return summary
