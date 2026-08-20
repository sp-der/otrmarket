from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import statistics


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _setup_map(connection: sqlite3.Connection) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in connection.execute(
        "SELECT sequence_no,payload_json FROM decision_traces "
        "WHERE event_type='SETUP_DECISION' ORDER BY sequence_no"
    ):
        payload = json.loads(row["payload_json"])
        setup_id = payload.get("setup_id")
        if not setup_id:
            continue
        payload["_sequence_no"] = int(row["sequence_no"])
        grouped[str(setup_id)].append(payload)
    return dict(grouped)


def _latest_setup_before(items: list[dict], sequence_no: int) -> dict:
    eligible = [item for item in items if int(item.get("_sequence_no", -1)) <= sequence_no]
    return dict(eligible[-1]) if eligible else {}


def collapse_order_states(states: list[dict], setup_decisions: dict[str, list[dict]] | None = None) -> list[dict]:
    setup_decisions = setup_decisions or {}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for state in states:
        setup_id = state.get("setup_id")
        if setup_id:
            grouped[str(setup_id)].append(state)

    output = []
    for setup_id, items in grouped.items():
        items = sorted(items, key=lambda item: int(item.get("_sequence_no", 0)))
        first, final = items[0], items[-1]
        first_sequence = int(first.get("_sequence_no", 0))
        setup = _latest_setup_before(setup_decisions.get(setup_id, []), first_sequence)
        cancellation = next(
            (
                dict(item.get("cancellation") or {})
                for item in reversed(items)
                if item.get("cancellation")
            ),
            {},
        )
        reason = cancellation.get("cancellation_reason")
        decisions = [str(item.get("decision") or "").upper() for item in items]
        statuses = [str(item.get("status") or "").upper() for item in items]
        filled = any(status in {"OPEN", "CLOSED"} for status in statuses) or any(
            decision in {"WIN", "LOSS"} for decision in decisions
        )
        closed = any(status == "CLOSED" for status in statuses) or any(
            decision in {"WIN", "LOSS"} for decision in decisions
        )
        metadata = setup.get("metadata") or {}
        context = setup.get("htf_context") or {}
        output.append(
            {
                "setup_id": setup_id,
                "symbol": final.get("symbol") or setup.get("symbol"),
                "timeframe": final.get("timeframe") or setup.get("timeframe"),
                "strategy": final.get("strategy_type") or setup.get("strategy_type"),
                "direction": final.get("direction") or setup.get("direction"),
                "grade": setup.get("setup_grade") or context.get("quality_grade"),
                "quality_score": setup.get("quality_score") or context.get("quality_score"),
                "entry_type": metadata.get("entry_type"),
                "registered": True,
                "filled": filled,
                "closed": closed,
                "first_status": first.get("status"),
                "final_status": final.get("status"),
                "final_decision": final.get("decision"),
                "cancelled": bool(reason),
                "cancellation_reason": reason,
                "immediate_registration_stale": reason == "STALE_AT_REGISTRATION",
                "target_progress_stale": reason == "TARGET_PROGRESS_75",
                "pending_expired": reason == "PENDING_EXPIRED",
                "stop_breached_before_entry": reason == "STOP_BREACHED_BEFORE_ENTRY",
                "bars_elapsed": cancellation.get("bars_elapsed"),
                "configured_max_bars": cancellation.get("configured_max_bars"),
                "progress_to_target": cancellation.get("progress_to_target"),
                "distance_to_entry": cancellation.get("distance_to_entry"),
                "entry": cancellation.get("entry", setup.get("planned_entry", final.get("entry"))),
                "stop": cancellation.get("stop", setup.get("stop", final.get("stop"))),
                "target": cancellation.get("target", setup.get("target", final.get("target"))),
                "trace_states": len(items),
            }
        )
    return output


def load_order_lifecycles(run_database: str | Path) -> tuple[dict, list[dict]]:
    path = Path(run_database).resolve()
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        run_row = connection.execute("SELECT * FROM backtest_runs LIMIT 1").fetchone()
        if run_row is None:
            raise ValueError(f"missing backtest_runs row: {path.name}")
        run = dict(run_row)
        if run.get("status") != "COMPLETE":
            raise ValueError(f"run is not COMPLETE: {path.name}")
        setups = _setup_map(connection)
        states = []
        for row in connection.execute(
            "SELECT sequence_no,payload_json FROM decision_traces "
            "WHERE event_type='ORDER_STATE' ORDER BY sequence_no"
        ):
            payload = json.loads(row["payload_json"])
            payload["_sequence_no"] = int(row["sequence_no"])
            states.append(payload)
    finally:
        connection.close()
    return run, collapse_order_states(states, setups)


def _summary(rows: list[dict]) -> dict:
    registered = len(rows)
    filled = sum(bool(row.get("filled")) for row in rows)
    cancelled = sum(bool(row.get("cancelled")) for row in rows)
    reasons: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.get("cancellation_reason"):
            reasons[str(row["cancellation_reason"])] += 1
    progress = [
        float(row["progress_to_target"])
        for row in rows
        if row.get("progress_to_target") is not None
    ]
    stale_progress = [
        float(row["progress_to_target"])
        for row in rows
        if row.get("immediate_registration_stale") and row.get("progress_to_target") is not None
    ]
    return {
        "registered_orders": registered,
        "filled_orders": filled,
        "filled_order_pct": (filled / registered * 100.0) if registered else None,
        "never_filled_orders": registered - filled,
        "cancelled_before_or_without_fill": cancelled,
        "immediate_registration_stale": sum(bool(row.get("immediate_registration_stale")) for row in rows),
        "target_progress_stale": sum(bool(row.get("target_progress_stale")) for row in rows),
        "pending_expired": sum(bool(row.get("pending_expired")) for row in rows),
        "stop_breached_before_entry": sum(bool(row.get("stop_breached_before_entry")) for row in rows),
        "cancellation_reasons": dict(sorted(reasons.items())),
        "median_cancellation_progress_to_target": _median(progress),
        "median_immediate_stale_progress_to_target": _median(stale_progress),
    }


def _segment(rows: list[dict], key: str) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(key) or "UNKNOWN")].append(row)
    return {name: _summary(values) for name, values in sorted(groups.items())}


def summarize_lifecycles(rows: list[dict]) -> dict:
    summary = _summary(rows)
    summary.update(
        {
            "by_strategy": _segment(rows, "strategy"),
            "by_timeframe": _segment(rows, "timeframe"),
            "by_symbol": _segment(rows, "symbol"),
            "by_grade": _segment(rows, "grade"),
            "by_entry_type": _segment(rows, "entry_type"),
        }
    )
    return summary
