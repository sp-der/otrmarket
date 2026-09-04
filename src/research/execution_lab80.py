from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.execution.live.store import ensure_schema, execution_status
from src.execution.state_machine80 import resolve_transition80


def fault_contracts80() -> list[dict]:
    """Deterministic execution-fault contracts used by tests and the lab API."""
    cases = [
        ("CLAIMED", "WORKING", True, "broker can skip an acknowledgement callback"),
        ("WORKING", "FILLED", True, "normal fill"),
        ("FILLED", "WORKING", False, "late working callback must not regress a fill"),
        ("CLOSED", "FILLED", False, "terminal close must not reopen"),
        ("FILLED", "REJECTED", True, "protective-order failure after fill fails closed"),
    ]
    results = []
    for current, target, expected_apply, description in cases:
        decision = resolve_transition80(current, target)
        results.append(
            {
                "current": current,
                "target": target,
                "expected_apply": expected_apply,
                "actual_apply": decision.apply,
                "passed": decision.apply == expected_apply,
                "description": description,
                "reason": decision.reason,
            }
        )
    return results


def execution_lab_snapshot80(db_path: Path) -> dict:
    generated = datetime.now(timezone.utc).isoformat()
    contracts = fault_contracts80()
    if not db_path.exists():
        return {
            "profile": "OTR_EXECUTION_LAB_8_0",
            "generated_at": generated,
            "fault_contracts": contracts,
            "fault_contracts_passed": all(item["passed"] for item in contracts),
            "status": {},
            "transition_warnings": [],
        }

    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        ensure_schema(connection)
        status = execution_status(connection)
        warnings = []
        rows = connection.execute(
            "SELECT event_id,command_id,event_type,payload_json,received_at "
            "FROM execution_events ORDER BY received_at DESC LIMIT 200"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            transition = (payload.get("metadata") or {}).get("transition80") or {}
            if transition and not transition.get("apply") and not transition.get("duplicate"):
                warnings.append(
                    {
                        "event_id": row["event_id"],
                        "command_id": row["command_id"],
                        "event_type": row["event_type"],
                        "received_at": row["received_at"],
                        "stale": bool(transition.get("stale")),
                        "reason": transition.get("reason"),
                    }
                )
        return {
            "profile": "OTR_EXECUTION_LAB_8_0",
            "generated_at": generated,
            "fault_contracts": contracts,
            "fault_contracts_passed": all(item["passed"] for item in contracts),
            "status": status,
            "transition_warnings": warnings[:30],
        }
    finally:
        connection.close()
