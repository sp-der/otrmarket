"""Additive, non-destructive persistence for Operation 8.2 snapshots.

confluence_snapshots_v82 is entirely new and owned by this package. It is
never used by any execution decision path. Pre-trade columns are written
once (INSERT ... ON CONFLICT DO NOTHING) and are immutable evidence of what
the system knew BEFORE the trade; post-trade outcome columns are updated
separately by record_outcome() once the trade resolves, and never touch the
pre-trade columns.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

FEATURE_TABLE = "confluence_snapshots_v82"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {FEATURE_TABLE} (
            setup_id TEXT PRIMARY KEY,
            run_id TEXT,
            captured_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            features_json TEXT NOT NULL,
            local_quality_score REAL,
            htf_alignment_score REAL,
            context_score REAL,
            liquidity_score REAL,
            entry_quality_score REAL,
            combined_shadow_score REAL,
            shadow_action TEXT,
            shadow_reasons_json TEXT,
            model_status TEXT,
            similar_setup_status TEXT,
            htf_bullish_count INTEGER,
            htf_bearish_count INTEGER,
            htf_mixed_count INTEGER,
            htf_alignment_direction TEXT,
            htf_conflict_level TEXT,
            execution_vs_htf_conflict INTEGER,
            otr_status TEXT,
            otr_grade TEXT,
            requested_risk_dollars REAL,
            actual_risk_dollars REAL,
            result TEXT,
            result_r REAL,
            result_dollars REAL,
            mfe_r REAL,
            mae_r REAL,
            hold_seconds REAL,
            outcome_updated_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_confluence_snapshots_v82_run
        ON {FEATURE_TABLE}(run_id, captured_at);

        CREATE INDEX IF NOT EXISTS idx_confluence_snapshots_v82_action
        ON {FEATURE_TABLE}(shadow_action);
        """
    )
    connection.commit()


def save_snapshot(connection: sqlite3.Connection, record: dict[str, Any]) -> bool:
    """Persist one pre-trade snapshot. Idempotent: a setup_id already present
    keeps its original (first) snapshot untouched -- this is immutable
    "what the system knew before the trade" evidence, never overwritten by a
    later re-save of the same setup (e.g. a second save_setup() call on the
    same object). Returns True if a new row was inserted.
    """
    ensure_schema(connection)
    cursor = connection.execute(
        f"""
        INSERT INTO {FEATURE_TABLE} (
            setup_id, run_id, captured_at, symbol, timeframe, direction,
            feature_version, features_json,
            local_quality_score, htf_alignment_score, context_score,
            liquidity_score, entry_quality_score, combined_shadow_score,
            shadow_action, shadow_reasons_json, model_status, similar_setup_status,
            htf_bullish_count, htf_bearish_count, htf_mixed_count,
            htf_alignment_direction, htf_conflict_level, execution_vs_htf_conflict,
            otr_status, otr_grade, requested_risk_dollars, actual_risk_dollars
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(setup_id) DO NOTHING
        """,
        (
            record["setup_id"],
            record.get("run_id"),
            record.get("captured_at") or _utc_iso(),
            record["symbol"],
            record["timeframe"],
            record["direction"],
            record["feature_version"],
            json.dumps(record["features"], sort_keys=True, default=str),
            record.get("local_quality_score"),
            record.get("htf_alignment_score"),
            record.get("context_score"),
            record.get("liquidity_score"),
            record.get("entry_quality_score"),
            record.get("combined_shadow_score"),
            record.get("shadow_action"),
            json.dumps(record.get("shadow_reasons") or [], sort_keys=True),
            record.get("model_status", "INSUFFICIENT_DATA"),
            record.get("similar_setup_status", "INSUFFICIENT_EVIDENCE"),
            record.get("htf_bullish_count"),
            record.get("htf_bearish_count"),
            record.get("htf_mixed_count"),
            record.get("htf_alignment_direction"),
            record.get("htf_conflict_level"),
            int(bool(record.get("execution_vs_htf_conflict"))) if record.get("execution_vs_htf_conflict") is not None else None,
            record.get("otr_status"),
            record.get("otr_grade"),
            record.get("requested_risk_dollars"),
            record.get("actual_risk_dollars"),
        ),
    )
    connection.commit()
    return bool(cursor.rowcount)


def record_outcome(
    connection: sqlite3.Connection,
    setup_id: str,
    *,
    result: str | None,
    result_r: float | None,
    result_dollars: float | None,
    mfe_r: float | None,
    mae_r: float | None,
    hold_seconds: float | None,
) -> None:
    """Update ONLY the post-trade outcome columns. Never touches
    features_json or any pre-trade score/HTF column -- those remain the
    immutable record of what the system knew before the trade.
    """
    ensure_schema(connection)
    connection.execute(
        f"""
        UPDATE {FEATURE_TABLE}
        SET result=?, result_r=?, result_dollars=?, mfe_r=?, mae_r=?, hold_seconds=?, outcome_updated_at=?
        WHERE setup_id=?
        """,
        (result, result_r, result_dollars, mfe_r, mae_r, hold_seconds, _utc_iso(), str(setup_id)),
    )
    connection.commit()


def get_snapshot(connection: sqlite3.Connection, setup_id: str) -> dict[str, Any] | None:
    ensure_schema(connection)
    cursor = connection.execute(f"SELECT * FROM {FEATURE_TABLE} WHERE setup_id=?", (str(setup_id),))
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [column[0] for column in cursor.description]
    return _shape_row(dict(zip(columns, row)))


def list_snapshots(
    connection: sqlite3.Connection,
    *,
    run_id: str | None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ensure_schema(connection)
    bounded = max(1, min(int(limit), 500))
    if run_id is None:
        cursor = connection.execute(f"SELECT * FROM {FEATURE_TABLE} ORDER BY captured_at DESC LIMIT ?", (bounded,))
    else:
        cursor = connection.execute(
            f"SELECT * FROM {FEATURE_TABLE} WHERE run_id=? ORDER BY captured_at DESC LIMIT ?",
            (run_id, bounded),
        )
    rows = cursor.fetchall()
    columns = [column[0] for column in cursor.description]
    return [_shape_row(dict(zip(columns, row))) for row in rows]


def _shape_row(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    try:
        row["features"] = json.loads(row.pop("features_json") or "{}")
    except (TypeError, ValueError):
        row["features"] = {}
        row.pop("features_json", None)
    try:
        row["shadow_reasons"] = json.loads(row.pop("shadow_reasons_json") or "[]")
    except (TypeError, ValueError):
        row["shadow_reasons"] = []
        row.pop("shadow_reasons_json", None)
    row["execution_vs_htf_conflict"] = (
        bool(row["execution_vs_htf_conflict"]) if row.get("execution_vs_htf_conflict") is not None else None
    )
    return row


def compatible_snapshots_for_training(
    connection: sqlite3.Connection,
    *,
    accounting_version: str,
) -> list[dict[str, Any]]:
    """Resolved (result is not NULL) snapshots whose paper trade used the
    given accounting_version -- the join back to paper_trades is required so
    dollar-outcome data never mixes MGC_WHOLE_CONTRACT_V1 trades with legacy
    theoretical-budget rows.
    """
    ensure_schema(connection)
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_trades'"
    ).fetchone()
    if not exists:
        return []
    cursor = connection.execute(
        f"""
        SELECT c.* FROM {FEATURE_TABLE} c
        JOIN paper_trades p ON p.setup_id = c.setup_id
        WHERE c.result IS NOT NULL AND p.accounting_version = ?
        ORDER BY c.captured_at ASC
        """,
        (accounting_version,),
    )
    rows = cursor.fetchall()
    columns = [column[0] for column in cursor.description]
    return [_shape_row(dict(zip(columns, row))) for row in rows]
