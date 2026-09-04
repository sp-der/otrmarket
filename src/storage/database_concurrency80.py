from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from threading import Lock


_install_lock = Lock()
_initialized_paths: set[str] = set()
_original_get_connection = None
_original_prune_market_quotes = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    exchange_time TEXT,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price REAL,
    bid REAL,
    ask REAL,
    mid REAL,
    spread REAL,
    spread_bps REAL,
    ingested_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_quotes_symbol_time
ON market_quotes(symbol, received_at);

CREATE TABLE IF NOT EXISTS quote_counters (
    symbol TEXT PRIMARY KEY,
    total_quotes INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    ticks INTEGER NOT NULL DEFAULT 0,
    UNIQUE(symbol, timeframe, open_time)
);
CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_time
ON candles(symbol, timeframe, open_time);

CREATE TABLE IF NOT EXISTS strategy_setups (
    setup_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    created_at TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL NOT NULL,
    risk_reward REAL NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    setup_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_price REAL NOT NULL,
    target_price REAL NOT NULL,
    opened_at TEXT,
    closed_at TEXT,
    exit_price REAL,
    result TEXT,
    result_r REAL,
    risk_dollars REAL,
    result_dollars REAL,
    guard_reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS engine_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_diagnostics (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    market_time TEXT NOT NULL,
    stage TEXT NOT NULL,
    direction TEXT,
    pd_array INTEGER NOT NULL DEFAULT 0,
    signal INTEGER NOT NULL DEFAULT 0,
    displacement INTEGER NOT NULL DEFAULT 0,
    entry_fvg INTEGER NOT NULL DEFAULT 0,
    retracement INTEGER NOT NULL DEFAULT 0,
    rr INTEGER NOT NULL DEFAULT 0,
    trigger_type TEXT,
    note TEXT,
    setup_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(symbol, timeframe)
);
"""


def _busy_timeout_ms() -> int:
    try:
        return max(1_000, int(os.getenv("OTR_SQLITE_BUSY_TIMEOUT_MS", "15000")))
    except ValueError:
        return 15_000


def _connect(path: Path) -> sqlite3.Connection:
    timeout_ms = _busy_timeout_ms()
    connection = sqlite3.connect(
        path,
        check_same_thread=False,
        timeout=timeout_ms / 1000.0,
    )
    connection.execute(f"PRAGMA busy_timeout={timeout_ms}")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})").fetchall())


def _initialize_database(database_module) -> None:
    path = Path(database_module.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    if key in _initialized_paths:
        return

    with _install_lock:
        if key in _initialized_paths:
            return
        connection = _connect(path)
        try:
            # WAL is a database-level setting. Set it during one-time startup
            # initialization, never on every dashboard/API request.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA_SQL)

            if not _column_exists(connection, "market_quotes", "ingested_at"):
                connection.execute("ALTER TABLE market_quotes ADD COLUMN ingested_at TEXT")
            if connection.execute(
                "SELECT 1 FROM market_quotes WHERE ingested_at IS NULL LIMIT 1"
            ).fetchone():
                connection.execute(
                    "UPDATE market_quotes SET ingested_at = received_at WHERE ingested_at IS NULL"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_market_quotes_symbol_ingest "
                "ON market_quotes(symbol, ingested_at)"
            )

            for column, ddl in (
                ("risk_dollars", "REAL"),
                ("result_dollars", "REAL"),
                ("guard_reason", "TEXT"),
            ):
                if not _column_exists(connection, "paper_trades", column):
                    connection.execute(f"ALTER TABLE paper_trades ADD COLUMN {column} {ddl}")

            existing_counter_rows = connection.execute(
                "SELECT COUNT(*) FROM quote_counters"
            ).fetchone()[0]
            if existing_counter_rows == 0:
                now = database_module._utc_iso()
                for symbol, count in connection.execute(
                    "SELECT symbol, COUNT(*) FROM market_quotes GROUP BY symbol"
                ).fetchall():
                    connection.execute(
                        "INSERT OR REPLACE INTO quote_counters(symbol, total_quotes, updated_at) "
                        "VALUES (?, ?, ?)",
                        (symbol, int(count or 0), now),
                    )
            connection.commit()
            _initialized_paths.add(key)
        finally:
            connection.close()


def _make_get_connection(database_module):
    def get_connection():
        _initialize_database(database_module)
        # Normal request/engine connections are deliberately lightweight.
        # In WAL mode a reader should not need a schema/write lock just to open.
        return _connect(Path(database_module.DB_PATH))

    get_connection.__name__ = "get_connection"
    get_connection.__module__ = __name__
    return get_connection


def _make_prune_market_quotes(database_module):
    def prune_market_quotes(connection: sqlite3.Connection) -> dict[str, int]:
        deleted: dict[str, int] = {}
        processed_raw = database_module.get_engine_state(
            connection, "last_ninjatrader_quote_id", "0"
        ) or "0"
        try:
            processed_id = int(processed_raw)
        except ValueError:
            processed_id = 0

        for symbol in ("NQ", "ES", "GC", "BTC-USD"):
            keep = database_module._retention_limit(symbol)
            cutoff_row = connection.execute(
                """
                SELECT id FROM market_quotes
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT 1 OFFSET ?
                """,
                (symbol, keep - 1),
            ).fetchone()
            if cutoff_row is None:
                deleted[symbol] = 0
                continue

            cutoff_id = int(cutoff_row[0])
            if symbol in ("NQ", "ES", "GC"):
                if processed_id <= 0:
                    deleted[symbol] = 0
                    continue
                cursor = connection.execute(
                    "DELETE FROM market_quotes WHERE symbol = ? AND id < ? AND id <= ?",
                    (symbol, cutoff_id, processed_id),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM market_quotes WHERE symbol = ? AND id < ?",
                    (symbol, cutoff_id),
                )
            deleted[symbol] = max(0, int(cursor.rowcount or 0))

        connection.commit()
        # Do not force wal_checkpoint(TRUNCATE) while the live chart, dashboard,
        # engine and bridge are active. SQLite's WAL autocheckpoint can progress
        # without demanding an exclusive checkpoint lock here.
        return deleted

    prune_market_quotes.__name__ = "prune_market_quotes"
    prune_market_quotes.__module__ = __name__
    return prune_market_quotes


def install() -> None:
    global _original_get_connection, _original_prune_market_quotes
    import src.storage.database as database_module

    if getattr(database_module, "_otr80_concurrency_installed", False):
        return

    _original_get_connection = database_module.get_connection
    _original_prune_market_quotes = database_module.prune_market_quotes
    database_module.get_connection = _make_get_connection(database_module)
    database_module.prune_market_quotes = _make_prune_market_quotes(database_module)
    database_module._otr80_concurrency_installed = True


__all__ = ["install"]
