from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from src.integrations.nautilus_shadow.ledger import (
    ensure_parity_ledger,
    load_closed_gold_candidates,
    run_candidate_parity,
)
from src.storage.database import get_connection


MAX_ATTEMPTS = 3
DEFAULT_POLL_SECONDS = 2.0
_WORKER_NAME = "otr-nautilus-auto-certifier"
_worker_lock = threading.Lock()
_worker_stop = threading.Event()
_worker_thread: threading.Thread | None = None


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _auto_enabled() -> bool:
    explicit = os.getenv("OTR_NAUTILUS_AUTO_CERTIFY")
    if explicit is not None:
        return _truthy(explicit)
    # Railway always injects these variables. Default-on there keeps production
    # automatic without making imports/tests/local development spawn a daemon.
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))


def _poll_seconds() -> float:
    try:
        return max(0.25, float(os.getenv("OTR_NAUTILUS_AUTO_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))))
    except (TypeError, ValueError):
        return DEFAULT_POLL_SECONDS


def ensure_auto_certify_schema(connection: sqlite3.Connection) -> None:
    """Create additive job state only. Trading tables are never altered."""
    ensure_parity_ledger(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS nautilus_shadow_auto_jobs (
            setup_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            queued_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            last_error TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_nautilus_shadow_auto_jobs_status
        ON nautilus_shadow_auto_jobs(status, updated_at DESC);
        """
    )
    connection.commit()


def _candidate_by_setup_id(connection: sqlite3.Connection, setup_id: str):
    # Operation 8.1 keeps a bounded active replay scorecard. Five hundred closed
    # trades is intentionally beyond a normal certification backlog while still
    # avoiding an unbounded scan if an old database is attached.
    for candidate in load_closed_gold_candidates(connection, limit=500):
        if candidate.setup_id == str(setup_id):
            return candidate
    return None


def _claim_next(connection: sqlite3.Connection) -> tuple[str, int] | None:
    ensure_auto_certify_schema(connection)
    row = connection.execute(
        """
        SELECT p.setup_id, COALESCE(j.attempts, 0)
        FROM paper_trades p
        LEFT JOIN nautilus_shadow_parity n ON n.setup_id = p.setup_id
        LEFT JOIN nautilus_shadow_auto_jobs j ON j.setup_id = p.setup_id
        WHERE p.symbol = 'GC'
          AND p.status = 'CLOSED'
          AND p.opened_at IS NOT NULL
          AND p.closed_at IS NOT NULL
          AND n.setup_id IS NULL
          AND (
                j.setup_id IS NULL
                OR (j.status IN ('WAITING_TICKS', 'RETRY') AND j.attempts < ?)
              )
        ORDER BY COALESCE(p.closed_at, p.updated_at) DESC
        LIMIT 1
        """,
        (MAX_ATTEMPTS,),
    ).fetchone()
    if row is None:
        return None

    setup_id = str(row[0])
    attempts = int(row[1] or 0) + 1
    now = _utc_iso()
    connection.execute(
        """
        INSERT INTO nautilus_shadow_auto_jobs(
            setup_id,status,attempts,queued_at,updated_at,completed_at,last_error
        ) VALUES (?, 'RUNNING', ?, ?, ?, NULL, '')
        ON CONFLICT(setup_id) DO UPDATE SET
            status='RUNNING',
            attempts=excluded.attempts,
            updated_at=excluded.updated_at,
            completed_at=NULL,
            last_error=''
        """,
        (setup_id, attempts, now, now),
    )
    connection.commit()
    return setup_id, attempts


def _mark_job(
    connection: sqlite3.Connection,
    setup_id: str,
    status: str,
    *,
    error: str = "",
    completed: bool = False,
) -> None:
    now = _utc_iso()
    connection.execute(
        """
        UPDATE nautilus_shadow_auto_jobs
        SET status=?, updated_at=?, completed_at=?, last_error=?
        WHERE setup_id=?
        """,
        (status, now, now if completed else None, str(error or "")[:1200], str(setup_id)),
    )
    connection.commit()


def process_one_auto_certification(
    connection: sqlite3.Connection,
    *,
    runner: Callable[[sqlite3.Connection, Any], Any] | None = None,
) -> dict[str, Any] | None:
    """Certify one queued closed Gold trade, fail-open and idempotently.

    The caller owns the connection. ``runner`` exists for deterministic unit
    tests; production always uses ``run_candidate_parity``.
    """
    claimed = _claim_next(connection)
    if claimed is None:
        return None
    setup_id, attempts = claimed
    candidate = _candidate_by_setup_id(connection, setup_id)
    if candidate is None:
        _mark_job(
            connection,
            setup_id,
            "ERROR",
            error="Closed Gold candidate could not be reconstructed from persisted trade/setup rows.",
            completed=True,
        )
        return {"setup_id": setup_id, "status": "ERROR", "attempts": attempts}

    execute = runner or run_candidate_parity
    try:
        record = execute(connection, candidate)
    except ValueError as exc:
        message = str(exc)
        retention_error = "Not enough retained post-setup Gold ticks" in message
        if retention_error and attempts < MAX_ATTEMPTS:
            status = "WAITING_TICKS"
            completed = False
        elif retention_error:
            status = "UNAVAILABLE_RETENTION"
            completed = True
        elif attempts < MAX_ATTEMPTS:
            status = "RETRY"
            completed = False
        else:
            status = "ERROR"
            completed = True
        _mark_job(connection, setup_id, status, error=message, completed=completed)
        return {"setup_id": setup_id, "status": status, "attempts": attempts, "error": message}
    except Exception as exc:  # diagnostics must never interrupt OTR runtime
        message = f"{type(exc).__name__}: {exc}"
        status = "RETRY" if attempts < MAX_ATTEMPTS else "ERROR"
        _mark_job(connection, setup_id, status, error=message, completed=attempts >= MAX_ATTEMPTS)
        return {"setup_id": setup_id, "status": status, "attempts": attempts, "error": message}

    _mark_job(connection, setup_id, "CERTIFIED", completed=True)
    return {
        "setup_id": setup_id,
        "status": "CERTIFIED",
        "attempts": attempts,
        "matched_trade_path": bool(getattr(record, "matched_trade_path", False)),
        "matched_full": bool(getattr(record, "matched_full", False)),
    }


def _worker_loop() -> None:
    print(
        f"Nautilus auto-certifier started: closed GC trades will be certified every {_poll_seconds():.2f}s; fail-open, non-authoritative.",
        flush=True,
    )
    while not _worker_stop.is_set():
        connection = None
        result = None
        try:
            connection = get_connection()
            result = process_one_auto_certification(connection)
        except Exception as exc:
            print(f"Nautilus auto-certifier worker warning: {type(exc).__name__}: {exc}", flush=True)
        finally:
            if connection is not None:
                connection.close()

        if result:
            status = result.get("status")
            setup_id = result.get("setup_id")
            if status == "CERTIFIED":
                print(
                    "Nautilus auto-certifier stored "
                    f"{setup_id}: path_match={result.get('matched_trade_path')} full_match={result.get('matched_full')}",
                    flush=True,
                )
            elif status in {"ERROR", "UNAVAILABLE_RETENTION"}:
                print(
                    f"Nautilus auto-certifier {status.lower()} for {setup_id}: {result.get('error', '')}",
                    flush=True,
                )
            # A newly closed setup is ordered ahead of stale history on the next
            # claim, so current replay evidence cannot sit behind an old backlog.
            continue
        _worker_stop.wait(_poll_seconds())


def start_auto_certifier() -> bool:
    """Start one daemon worker per dashboard process. Safe to call repeatedly."""
    if not _auto_enabled():
        return False

    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return True
        _worker_stop.clear()
        _worker_thread = threading.Thread(target=_worker_loop, name=_WORKER_NAME, daemon=True)
        _worker_thread.start()
        return True


def stop_auto_certifier_for_tests(timeout: float = 2.0) -> None:
    """Test helper. Production relies on daemon shutdown with the process."""
    global _worker_thread
    _worker_stop.set()
    thread = _worker_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=max(0.0, float(timeout)))
    _worker_thread = None


def auto_certifier_snapshot(connection: sqlite3.Connection, *, recent_limit: int = 12) -> dict[str, Any]:
    ensure_auto_certify_schema(connection)
    counts = {
        str(status): int(count)
        for status, count in connection.execute(
            "SELECT status, COUNT(*) FROM nautilus_shadow_auto_jobs GROUP BY status"
        ).fetchall()
    }
    rows = connection.execute(
        """
        SELECT setup_id,status,attempts,queued_at,updated_at,completed_at,last_error
        FROM nautilus_shadow_auto_jobs
        ORDER BY updated_at DESC
        LIMIT ?
        """,
        (max(1, min(int(recent_limit), 50)),),
    ).fetchall()
    thread = _worker_thread
    return {
        "enabled": _auto_enabled(),
        "worker_running": bool(thread is not None and thread.is_alive()),
        "poll_seconds": _poll_seconds(),
        "max_attempts": MAX_ATTEMPTS,
        "counts": counts,
        "recent": [
            {
                "setup_id": row[0],
                "status": row[1],
                "attempts": int(row[2] or 0),
                "queued_at": row[3],
                "updated_at": row[4],
                "completed_at": row[5],
                "last_error": row[6] or "",
            }
            for row in rows
        ],
    }
