from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import statistics

STAGE_ORDER = (
    "WAIT_SIGNAL",
    "WAIT_DISPLACEMENT",
    "WAIT_ENTRY_FVG",
    "WAIT_QUALIFYING_FVG",
    "WAIT_VALID_RR",
    "SETUP_READY",
)
BOUNDARY_STAGES = {"WAIT_PD_ARRAY", "WARMUP", "EXPIRED"}
ENTRY_SEARCH_STAGES = {"WAIT_ENTRY_FVG", "WAIT_QUALIFYING_FVG", "WAIT_VALID_RR"}
PRE_ENTRY_STAGES = {"WAIT_SIGNAL", "WAIT_DISPLACEMENT"}


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def load_scanner_states(run_database: str | Path) -> dict[tuple[str, str], list[dict]]:
    path = Path(run_database).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
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
            if not symbol or not timeframe or not stage or not event_time:
                continue
            grouped[(str(symbol), str(timeframe))].append({
                "sequence_no": int(row["sequence_no"]),
                "event_time": str(event_time),
                "stage": str(stage).upper(),
                "direction": str(payload.get("direction") or diagnostic.get("direction") or "").upper(),
                "note": payload.get("reason") or diagnostic.get("note"),
            })
    finally:
        connection.close()
    return dict(grouped)


def _episode_before_decision(row: dict, states: list[dict]) -> list[dict]:
    decision_time = _dt(row.get("event_time"))
    if decision_time is None:
        return []
    eligible = [state for state in states if (_dt(state.get("event_time")) or decision_time) <= decision_time]
    if not eligible:
        return []

    # The scanner emits WAIT_PD_ARRAY/WARMUP/EXPIRED between independent ICT episodes.
    # Use the most recent boundary before the decision so unrelated earlier sequences
    # cannot inflate latency.
    boundary_index = -1
    for index, state in enumerate(eligible[:-1]):
        if state.get("stage") in BOUNDARY_STAGES:
            boundary_index = index
    episode = eligible[boundary_index + 1 :]
    if not any(state.get("stage") in STAGE_ORDER for state in episode):
        return []
    return episode


def enrich_stage_latency(rows: list[dict], states_by_key: dict[tuple[str, str], list[dict]]) -> list[dict]:
    output = []
    for row in rows:
        item = dict(row)
        states = states_by_key.get((str(item.get("symbol")), str(item.get("timeframe"))), [])
        episode = _episode_before_decision(item, states)
        decision_time = _dt(item.get("event_time"))
        if not episode or decision_time is None:
            item.update({
                "stage_episode_found": False,
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

        actionable = [state for state in episode if state.get("stage") in STAGE_ORDER]
        first_time = _dt(actionable[0].get("event_time")) if actionable else None
        episode_minutes = max(0.0, (decision_time - first_time).total_seconds() / 60.0) if first_time else None
        pre_entry_minutes = sum(value for stage, value in dwell.items() if stage in PRE_ENTRY_STAGES)
        entry_search_minutes = sum(value for stage, value in dwell.items() if stage in ENTRY_SEARCH_STAGES)
        dominant_stage = max(dwell, key=dwell.get) if dwell else None
        first_entry_candidate = next(
            (
                _dt(state.get("event_time"))
                for state in actionable
                if state.get("stage") in ENTRY_SEARCH_STAGES | {"SETUP_READY"}
            ),
            None,
        )

        item.update({
            "stage_episode_found": True,
            "episode_minutes": episode_minutes,
            "pre_entry_minutes": pre_entry_minutes,
            "entry_search_minutes": entry_search_minutes,
            "dominant_stage": dominant_stage,
            "stage_at_decision": actionable[-1].get("stage") if actionable else None,
            "stage_dwell_minutes": dict(sorted(dwell.items())),
            "minutes_from_first_entry_candidate_to_decision": (
                max(0.0, (decision_time - first_entry_candidate).total_seconds() / 60.0)
                if first_entry_candidate
                else None
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
        "matched_stage_episodes": len(matched),
        "matched_stage_episode_pct": (len(matched) / len(rows) * 100.0) if rows else None,
        "median_episode_minutes": _median(episodes),
        "median_pre_entry_minutes": _median(pre),
        "median_entry_search_minutes": _median(entry),
        "dominant_stage_counts": dict(sorted(dominant.items())),
        "median_stage_dwell_minutes": {
            stage: _median(values) for stage, values in sorted(stage_dwell.items())
        },
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
