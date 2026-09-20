"""Orchestration for Operation 8.2 Confluence Intelligence -- SHADOW ONLY.

capture_snapshot() is called once per GC setup (any status: blocked,
rejected, or chosen) right after OTR already persists it, so the pre-trade
snapshot captures exactly what OTR knew at that moment -- no more, no less.
update_outcome() is called after a paper trade resolves and only ever
touches the separate post-trade outcome columns.

Neither function raises: a failure here must never block strategy_setups or
paper_trades persistence, and never can, because nothing on the execution
path imports this module.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import features as features_mod
from . import memory as memory_mod
from . import scoring as scoring_mod
from . import store


def _hold_seconds(opened_at, closed_at) -> float | None:
    if opened_at is None or closed_at is None:
        return None
    try:
        return max(0.0, (closed_at - opened_at).total_seconds())
    except TypeError:
        return None


def capture_snapshot(connection: sqlite3.Connection, setup, *, run_id: str | None) -> dict[str, Any] | None:
    """Build and persist one pre-trade snapshot for a GC setup.

    Safe to call multiple times for the same setup_id (e.g. a setup that is
    save_setup()'d twice on a rejection path) -- store.save_snapshot() is
    idempotent and keeps the first snapshot.
    """
    if str(getattr(setup, "symbol", "")).upper() != "GC":
        return None
    try:
        extracted = features_mod.extract_features(setup)
        score = scoring_mod.score_setup(setup, extracted)
        meta = getattr(setup, "metadata", None) or {}
        similar = memory_mod.find_similar_setups(connection, setup, extracted)
        record = {
            "setup_id": str(setup.setup_id),
            "run_id": run_id,
            "symbol": str(setup.symbol),
            "timeframe": str(setup.timeframe),
            "direction": str(setup.direction),
            "feature_version": extracted["feature_version"],
            "features": extracted,
            "otr_status": str(getattr(setup, "status", "") or ""),
            "otr_grade": score_grade_hint(meta, score),
            "requested_risk_dollars": (meta.get("evaluation_guard") or {}).get("risk_dollars"),
            "actual_risk_dollars": None,
            "model_status": "INSUFFICIENT_DATA",
            "similar_setup_status": similar["status"],
            **score,
        }
        store.save_snapshot(connection, record)
        return record
    except Exception:
        # Shadow research must never break strategy_setups persistence.
        return None


def score_grade_hint(meta: dict, score: dict) -> str:
    context = meta.get("a_plus_context", {}) if isinstance(meta, dict) else {}
    grade = context.get("quality_grade") if isinstance(context, dict) else None
    return str(grade or "UNKNOWN").upper()


def update_outcome(connection: sqlite3.Connection, position) -> None:
    """Persist a resolved paper trade's outcome onto its (immutable)
    pre-trade snapshot, if one exists. Never rewrites the pre-trade fields.
    """
    setup = getattr(position, "setup", None)
    if setup is None or str(getattr(setup, "symbol", "")).upper() != "GC":
        return
    if str(getattr(position, "status", "")) != "CLOSED":
        return
    try:
        store.record_outcome(
            connection,
            str(setup.setup_id),
            result=position.result,
            result_r=position.result_r,
            result_dollars=position.result_dollars,
            mfe_r=position.mfe_r,
            mae_r=position.mae_r,
            hold_seconds=_hold_seconds(position.opened_at, position.closed_at),
        )
        if position.actual_risk_dollars is not None:
            connection.execute(
                f"UPDATE {store.FEATURE_TABLE} SET actual_risk_dollars=? WHERE setup_id=?",
                (position.actual_risk_dollars, str(setup.setup_id)),
            )
            connection.commit()
    except Exception:
        # Post-trade research enrichment must never break trade persistence.
        pass


def snapshot_with_dashboard_context(connection: sqlite3.Connection, setup_id: str) -> dict[str, Any] | None:
    """One snapshot enriched with OTR's own decision and execution
    certification -- joined at read time rather than duplicated into
    confluence_snapshots_v82, matching src.research.lab_v01's existing
    parity-join pattern.
    """
    snapshot = store.get_snapshot(connection, setup_id)
    if snapshot is None:
        return None
    trade_row = connection.execute(
        """
        SELECT status, result, result_r, result_dollars, quantity, accounting_version
        FROM paper_trades WHERE setup_id=?
        """,
        (setup_id,),
    ).fetchone()
    if trade_row:
        snapshot["otr_trade_status"] = trade_row[0]
        snapshot["otr_result"] = trade_row[1]
        snapshot["otr_result_r"] = trade_row[2]
        snapshot["otr_result_dollars"] = trade_row[3]
        snapshot["otr_quantity"] = trade_row[4]
        snapshot["otr_accounting_version"] = trade_row[5]

    parity_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nautilus_shadow_parity'"
    ).fetchone()
    if parity_exists:
        parity_row = connection.execute(
            """
            SELECT matched_trade_path, matched_full FROM nautilus_shadow_parity
            WHERE setup_id=? ORDER BY observed_at DESC LIMIT 1
            """,
            (setup_id,),
        ).fetchone()
        if parity_row:
            snapshot["nautilus_matched_trade_path"] = bool(parity_row[0])
            snapshot["nautilus_matched_full"] = bool(parity_row[1])
        else:
            snapshot["nautilus_matched_trade_path"] = None
            snapshot["nautilus_matched_full"] = None
    return snapshot


def recent_snapshots(connection: sqlite3.Connection, *, run_id: str | None, limit: int = 25) -> list[dict[str, Any]]:
    rows = store.list_snapshots(connection, run_id=run_id, limit=limit)
    return [snapshot_with_dashboard_context(connection, row["setup_id"]) or row for row in rows]
