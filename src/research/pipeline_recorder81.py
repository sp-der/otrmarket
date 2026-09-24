from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from src.research.run_scope import current_run_id


TABLE = "training_evaluations_81"


def ensure_pipeline_recorder81(connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS training_evaluations_81 (
            run_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            source TEXT NOT NULL,
            build TEXT NOT NULL,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            handled_count INTEGER NOT NULL DEFAULT 0,
            final_status TEXT NOT NULL,
            direction TEXT,
            candidate_json TEXT NOT NULL DEFAULT '[]',
            diagnostic_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (run_id, symbol, timeframe, evaluated_at, source)
        );
        CREATE INDEX IF NOT EXISTS idx_training_evaluations_81_run_time
        ON training_evaluations_81(run_id, symbol, evaluated_at);
        """
    )
    connection.commit()


def _event_time(histories, symbol: str, timeframe: str) -> str:
    candles = []
    if isinstance(histories, dict):
        candles = histories.get((symbol, timeframe), []) or []
    if candles:
        value = getattr(candles[-1], "close_time", None) or getattr(candles[-1], "open_time", None)
        if value is not None:
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc).isoformat()
            return str(value)
    raise ValueError("Decision recording requires an observed market event timestamp")


def _candidate_payload(setup) -> dict[str, Any]:
    metadata = getattr(setup, "metadata", {}) or {}
    context = metadata.get("a_plus_context", {}) if isinstance(metadata, dict) else {}
    if not isinstance(context, dict):
        context = {}
    return {
        "setup_id": str(getattr(setup, "setup_id", "") or ""),
        "strategy": str(metadata.get("strategy") or "ICT_CONFLUENCE"),
        "direction": str(getattr(setup, "direction", "") or ""),
        "status": str(getattr(setup, "status", "") or ""),
        "risk_reward": float(getattr(setup, "risk_reward", 0.0) or 0.0),
        "grade": context.get("quality_grade"),
        "entry_type": metadata.get("entry_type"),
        "candidate_source": metadata.get("candidate_source_80"),
    }


def record_pipeline_evaluation81(
    connection,
    runtime,
    symbol: str,
    timeframe: str,
    histories,
    candidates,
    handled,
    *,
    source: str = "CANDLE_CLOSE",
) -> None:
    """Record every prospective 8.1 evaluation, including NO_CANDIDATE.

    This is observability only. It never creates a StrategySetup, changes a
    quality threshold, mutates risk, or places an order.
    """
    if str(symbol).upper() != "GC":
        return
    ensure_pipeline_recorder81(connection)
    from src.research.outcomes81 import update_outcomes81
    event_stamp = _event_time(histories, symbol, timeframe)
    update_outcomes81(connection, symbol, timeframe, histories, datetime.fromisoformat(event_stamp))
    run_id = current_run_id(connection)
    from src.research.developing81 import observe_developing81
    observe_developing81(connection, candidates or [], symbol, timeframe, histories, datetime.fromisoformat(event_stamp))
    candidate_rows = [_candidate_payload(setup) for setup in (candidates or [])]
    handled_rows = list(handled or [])

    if handled_rows:
        final_status = str(getattr(handled_rows[-1], "status", "") or "HANDLED")
    elif candidate_rows:
        final_status = "CANDIDATE_UNRESOLVED"
    else:
        final_status = "NO_CANDIDATE"

    direction = ""
    if candidate_rows:
        direction = str(candidate_rows[0].get("direction") or "")
    diagnostic: Any = {}
    try:
        diagnostic = runtime.strategy.diagnostic(symbol, timeframe) or {}
    except Exception:
        diagnostic = {}
    if not direction and isinstance(diagnostic, dict):
        direction = str(diagnostic.get("direction") or diagnostic.get("bias") or "")

    if isinstance(diagnostic, dict):
        diagnostic = dict(diagnostic)
        diagnostic["collection"] = getattr(runtime.strategy, "candidate_collection81", {})

    connection.execute(
        """
        INSERT INTO training_evaluations_81(
            run_id,symbol,timeframe,evaluated_at,source,build,candidate_count,
            handled_count,final_status,direction,candidate_json,diagnostic_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id,symbol,timeframe,evaluated_at,source) DO NOTHING
        """,
        (
            run_id,
            str(symbol).upper(),
            str(timeframe),
            _event_time(histories, symbol, timeframe),
            str(source),
            "8.1",
            len(candidate_rows),
            len(handled_rows),
            final_status,
            direction,
            json.dumps(candidate_rows, sort_keys=True, default=str),
            json.dumps(diagnostic, sort_keys=True, default=str),
        ),
    )
    connection.commit()


def make_evaluation_recorder81(runtime):
    def recorder(connection, symbol, timeframe, histories, candidates, handled, *, source="CANDLE_CLOSE"):
        return record_pipeline_evaluation81(
            connection,
            runtime,
            symbol,
            timeframe,
            histories,
            candidates,
            handled,
            source=source,
        )

    return recorder
