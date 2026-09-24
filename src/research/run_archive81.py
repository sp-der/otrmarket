from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3

from src.research.run_scope import current_run_id


ARCHIVE_TABLES_81 = (
    "paper_trades",
    "strategy_setups",
    "decision_traces_80",
    "training_decisions_72t",
    "training_trades_72t",
    "training_trade_metrics_72t",
    "training_counterfactuals_72t",
    "training_shadow_72t",
    "training_evaluations_81",
    "confluence_snapshots_v82",
)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def ensure_run_archive81(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS otr_run_archives_81 (
            archive_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            label TEXT NOT NULL,
            archived_at TEXT NOT NULL,
            trade_count INTEGER NOT NULL DEFAULT 0,
            setup_count INTEGER NOT NULL DEFAULT 0,
            closed_count INTEGER NOT NULL DEFAULT 0,
            wins INTEGER NOT NULL DEFAULT 0,
            losses INTEGER NOT NULL DEFAULT 0,
            net_pnl REAL NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_otr_run_archives_81_time
        ON otr_run_archives_81(archived_at DESC);

        CREATE TABLE IF NOT EXISTS otr_run_archive_rows_81 (
            archive_id TEXT NOT NULL,
            row_type TEXT NOT NULL,
            row_key TEXT NOT NULL,
            row_json TEXT NOT NULL,
            PRIMARY KEY (archive_id, row_type, row_key),
            FOREIGN KEY (archive_id) REFERENCES otr_run_archives_81(archive_id)
        );
        CREATE INDEX IF NOT EXISTS idx_otr_run_archive_rows_81_type
        ON otr_run_archive_rows_81(archive_id, row_type);
        """
    )
    connection.commit()


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_snapshot(connection: sqlite3.Connection, table: str) -> list[dict]:
    columns = _columns(connection, table)
    if not columns:
        return []
    rows = connection.execute(f"SELECT * FROM {table}").fetchall()
    return [dict(zip(columns, row)) for row in rows]


def _summary(connection: sqlite3.Connection) -> dict:
    trade_count = 0
    closed_count = 0
    wins = 0
    losses = 0
    net_pnl = 0.0
    setup_count = 0

    if _table_exists(connection, "paper_trades"):
        row = connection.execute(
            """
            SELECT
              COUNT(*),
              COALESCE(SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN status='CLOSED' AND result='WIN' THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN status='CLOSED' AND result='LOSS' THEN 1 ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN status='CLOSED' THEN COALESCE(result_dollars,0) ELSE 0 END),0)
            FROM paper_trades
            """
        ).fetchone()
        trade_count = int(row[0] or 0)
        closed_count = int(row[1] or 0)
        wins = int(row[2] or 0)
        losses = int(row[3] or 0)
        net_pnl = float(row[4] or 0.0)

    if _table_exists(connection, "strategy_setups"):
        setup_count = int(connection.execute("SELECT COUNT(*) FROM strategy_setups").fetchone()[0] or 0)

    return {
        "trade_count": trade_count,
        "setup_count": setup_count,
        "closed_count": closed_count,
        "wins": wins,
        "losses": losses,
        "net_pnl": round(net_pnl, 2),
    }


def archive_active_run81(
    connection: sqlite3.Connection,
    *,
    archive_key: str,
    label: str | None = None,
) -> dict:
    """Snapshot the disposable 8.1 ledger before a reset.

    The archive uses generic JSON rows so future schema additions do not make an
    old milestone unreadable. archive_key is hashed into an idempotent archive
    id, so a restart between archive and reset cannot create duplicate copies.
    """
    ensure_run_archive81(connection)
    run_id = current_run_id(connection)
    key_hash = hashlib.sha256(f"{run_id}|{archive_key}".encode("utf-8")).hexdigest()[:18]
    archive_id = f"archive-81-{key_hash}"
    archived_at = datetime.now(timezone.utc).isoformat()
    summary = _summary(connection)
    table_counts: dict[str, int] = {}

    connection.execute(
        """
        INSERT INTO otr_run_archives_81(
          archive_id,run_id,label,archived_at,trade_count,setup_count,closed_count,
          wins,losses,net_pnl,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(archive_id) DO UPDATE SET
          run_id=excluded.run_id,label=excluded.label,archived_at=excluded.archived_at,
          trade_count=excluded.trade_count,setup_count=excluded.setup_count,
          closed_count=excluded.closed_count,wins=excluded.wins,losses=excluded.losses,
          net_pnl=excluded.net_pnl,metadata_json=excluded.metadata_json
        """,
        (
            archive_id,
            run_id,
            str(label or f"Operation 8.1 milestone {run_id}"),
            archived_at,
            summary["trade_count"],
            summary["setup_count"],
            summary["closed_count"],
            summary["wins"],
            summary["losses"],
            summary["net_pnl"],
            "{}",
        ),
    )

    for table in ARCHIVE_TABLES_81:
        if not _table_exists(connection, table):
            continue
        rows = _table_snapshot(connection, table)
        table_counts[table] = len(rows)
        for index, row in enumerate(rows):
            row_key = (
                row.get("setup_id")
                or row.get("evaluation_id")
                or row.get("id")
                or row.get("lesson_id")
                or f"row-{index}"
            )
            # Some tables may legitimately repeat setup ids across run ids.
            if row.get("run_id"):
                row_key = f"{row.get('run_id')}:{row_key}"
            connection.execute(
                """
                INSERT INTO otr_run_archive_rows_81(archive_id,row_type,row_key,row_json)
                VALUES (?,?,?,?)
                ON CONFLICT(archive_id,row_type,row_key) DO UPDATE SET
                  row_json=excluded.row_json
                """,
                (
                    archive_id,
                    table,
                    str(row_key),
                    json.dumps(row, sort_keys=True, default=str),
                ),
            )

    metadata = {
        "operation": "8.1",
        "tables": table_counts,
        "archive_format": "JSON_ROWS_V1",
    }
    connection.execute(
        "UPDATE otr_run_archives_81 SET metadata_json=? WHERE archive_id=?",
        (json.dumps(metadata, sort_keys=True), archive_id),
    )
    connection.commit()
    return {
        "archive_id": archive_id,
        "run_id": run_id,
        "label": str(label or f"Operation 8.1 milestone {run_id}"),
        "archived_at": archived_at,
        **summary,
        "tables": table_counts,
    }


def list_run_archives81(connection: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    ensure_run_archive81(connection)
    rows = connection.execute(
        """
        SELECT archive_id,run_id,label,archived_at,trade_count,setup_count,closed_count,
               wins,losses,net_pnl,metadata_json
        FROM otr_run_archives_81
        ORDER BY archived_at DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 200)),),
    ).fetchall()
    output = []
    for row in rows:
        try:
            metadata = json.loads(row[10] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        output.append(
            {
                "archive_id": row[0],
                "run_id": row[1],
                "label": row[2],
                "archived_at": row[3],
                "trade_count": int(row[4] or 0),
                "setup_count": int(row[5] or 0),
                "closed_count": int(row[6] or 0),
                "wins": int(row[7] or 0),
                "losses": int(row[8] or 0),
                "net_pnl": float(row[9] or 0.0),
                "metadata": metadata,
            }
        )
    return output


def archived_trades81(
    connection: sqlite3.Connection,
    archive_id: str,
    *,
    limit: int = 500,
) -> list[dict]:
    ensure_run_archive81(connection)
    rows = connection.execute(
        """
        SELECT row_json FROM otr_run_archive_rows_81
        WHERE archive_id=? AND row_type='paper_trades'
        LIMIT ?
        """,
        (archive_id, max(1, min(int(limit), 5000))),
    ).fetchall()
    output = []
    for row in rows:
        try:
            output.append(json.loads(row[0]))
        except (TypeError, json.JSONDecodeError):
            continue
    return output
