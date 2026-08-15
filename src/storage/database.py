from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from src.strategies.models import Candle, StrategySetup

DB_PATH = Path("data/otrmarket.db")
_db_lock = Lock()
_quote_rows_since_prune = 0

QUOTE_RETENTION_DEFAULTS = {
    "NQ": 50_000,
    "ES": 50_000,
    "GC": 50_000,
    "BTC-USD": 10_000,
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA synchronous=NORMAL;")

    connection.executescript(
        """
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
    )

    # Upgrade databases created by Operations 1-3 without deleting data.
    if not _column_exists(connection, "market_quotes", "ingested_at"):
        connection.execute("ALTER TABLE market_quotes ADD COLUMN ingested_at TEXT")
    connection.execute(
        "UPDATE market_quotes SET ingested_at = COALESCE(ingested_at, received_at) WHERE ingested_at IS NULL"
    )
    # Create the replay-ingest index only after legacy databases have been
    # upgraded with the ingested_at column. Operation 4 originally attempted
    # this index too early, which broke databases created by Operations 1-3.
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_market_quotes_symbol_ingest "
        "ON market_quotes(symbol, ingested_at)"
    )

    # Operation 4.5: add dollar-risk accounting to legacy paper-trade rows.
    for column, ddl in (
        ("risk_dollars", "REAL"),
        ("result_dollars", "REAL"),
        ("guard_reason", "TEXT"),
    ):
        if not _column_exists(connection, "paper_trades", column):
            connection.execute(f"ALTER TABLE paper_trades ADD COLUMN {column} {ddl}")

    # Operation 4.5.2: initialize lifetime quote counters before raw-tick
    # retention begins pruning market_quotes.
    existing_counter_rows = connection.execute("SELECT COUNT(*) FROM quote_counters").fetchone()[0]
    if existing_counter_rows == 0:
        now = _utc_iso()
        for symbol, count in connection.execute(
            "SELECT symbol, COUNT(*) FROM market_quotes GROUP BY symbol"
        ).fetchall():
            connection.execute(
                "INSERT OR REPLACE INTO quote_counters(symbol, total_quotes, updated_at) VALUES (?, ?, ?)",
                (symbol, int(count or 0), now),
            )
    connection.commit()
    return connection


def _retention_limit(symbol: str) -> int:
    env_name = "OTR_QUOTE_RETENTION_" + symbol.replace("-", "_")
    default = QUOTE_RETENTION_DEFAULTS.get(symbol, 25_000)
    try:
        return max(1_000, int(os.getenv(env_name, str(default))))
    except ValueError:
        return default


def _increment_quote_counter(connection: sqlite3.Connection, symbol: str, amount: int) -> None:
    # Legacy/unit-test schemas may call save_quotes_batch without get_connection().
    # In production quote_counters is created by get_connection().
    try:
        connection.execute(
            """
            INSERT INTO quote_counters(symbol, total_quotes, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                total_quotes = total_quotes + excluded.total_quotes,
                updated_at = excluded.updated_at
            """,
            (symbol, int(amount), _utc_iso()),
        )
    except sqlite3.OperationalError as exc:
        if "no such table: quote_counters" not in str(exc):
            raise


def prune_market_quotes(connection: sqlite3.Connection) -> dict[str, int]:
    """Bound raw tick storage without deleting unprocessed NinjaTrader ticks.

    Candles, setups and trades are persisted separately, so market_quotes only
    needs a rolling window for dashboard returns and bridge handoff. Futures
    rows are pruned only after src.main has acknowledged them via
    last_ninjatrader_quote_id. Deleted SQLite pages are reusable even when the
    file itself is not VACUUMed smaller.
    """
    deleted: dict[str, int] = {}
    processed_raw = get_engine_state(connection, "last_ninjatrader_quote_id", "0") or "0"
    try:
        processed_id = int(processed_raw)
    except ValueError:
        processed_id = 0

    for symbol in ("NQ", "ES", "GC", "BTC-USD"):
        keep = _retention_limit(symbol)
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
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass
    return deleted


def _maybe_prune_market_quotes(connection: sqlite3.Connection, inserted_rows: int) -> None:
    global _quote_rows_since_prune
    _quote_rows_since_prune += max(0, int(inserted_rows))
    threshold = max(1_000, int(os.getenv("OTR_QUOTE_PRUNE_EVERY", "5000")))
    if _quote_rows_since_prune < threshold:
        return
    prune_market_quotes(connection)
    _quote_rows_since_prune = 0


def save_quote(connection, received_at, exchange_time, source, symbol, price, bid, ask):
    mid = spread = spread_bps = None
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2
        spread = ask - bid
        if mid:
            spread_bps = (spread / mid) * 10_000

    with _db_lock:
        connection.execute(
            """
            INSERT INTO market_quotes (
                received_at, exchange_time, source, symbol,
                price, bid, ask, mid, spread, spread_bps, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                received_at,
                exchange_time,
                source,
                symbol,
                price,
                bid,
                ask,
                mid,
                spread,
                spread_bps,
                _utc_iso(),
            ),
        )
        _increment_quote_counter(connection, symbol, 1)
        connection.commit()
    _maybe_prune_market_quotes(connection, 1)


def save_candle(connection, candle: Candle):
    with _db_lock:
        connection.execute(
            """
            INSERT OR REPLACE INTO candles (
                symbol, timeframe, open_time, close_time,
                open, high, low, close, ticks
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candle.symbol,
                candle.timeframe,
                candle.open_time.isoformat(),
                candle.close_time.isoformat(),
                candle.open,
                candle.high,
                candle.low,
                candle.close,
                candle.ticks,
            ),
        )
        connection.commit()


def load_recent_candles(connection, symbols, timeframes, limit_per_series: int = 500) -> list[Candle]:
    output: list[Candle] = []
    for symbol in symbols:
        for timeframe in timeframes:
            rows = connection.execute(
                """
                SELECT symbol, timeframe, open_time, close_time, open, high, low, close, ticks
                FROM candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY open_time DESC
                LIMIT ?
                """,
                (symbol, timeframe, limit_per_series),
            ).fetchall()
            for row in reversed(rows):
                output.append(
                    Candle(
                        symbol=row[0],
                        timeframe=row[1],
                        open_time=datetime.fromisoformat(row[2].replace("Z", "+00:00")),
                        close_time=datetime.fromisoformat(row[3].replace("Z", "+00:00")),
                        open=float(row[4]),
                        high=float(row[5]),
                        low=float(row[6]),
                        close=float(row[7]),
                        ticks=int(row[8] or 0),
                    )
                )
    return output


def save_setup(connection, setup: StrategySetup):
    with _db_lock:
        connection.execute(
            """
            INSERT OR REPLACE INTO strategy_setups (
                setup_id, symbol, timeframe, direction, created_at,
                trigger_type, entry_price, stop_price, target_price,
                risk_reward, status, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                setup.setup_id,
                setup.symbol,
                setup.timeframe,
                setup.direction,
                setup.created_at.isoformat(),
                setup.trigger_type,
                setup.entry_price,
                setup.stop_price,
                setup.target_price,
                setup.risk_reward,
                setup.status,
                json.dumps(setup.to_dict(), sort_keys=True),
            ),
        )
        connection.commit()


def save_diagnostic(connection, diagnostic: dict | None):
    if not diagnostic:
        return
    with _db_lock:
        connection.execute(
            """
            INSERT INTO strategy_diagnostics (
                symbol, timeframe, market_time, stage, direction,
                pd_array, signal, displacement, entry_fvg, retracement, rr,
                trigger_type, note, setup_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET
                market_time=excluded.market_time,
                stage=excluded.stage,
                direction=excluded.direction,
                pd_array=excluded.pd_array,
                signal=excluded.signal,
                displacement=excluded.displacement,
                entry_fvg=excluded.entry_fvg,
                retracement=excluded.retracement,
                rr=excluded.rr,
                trigger_type=excluded.trigger_type,
                note=excluded.note,
                setup_id=excluded.setup_id,
                updated_at=excluded.updated_at
            """,
            (
                diagnostic["symbol"],
                diagnostic["timeframe"],
                diagnostic["market_time"],
                diagnostic["stage"],
                diagnostic.get("direction"),
                int(bool(diagnostic.get("pd_array"))),
                int(bool(diagnostic.get("signal"))),
                int(bool(diagnostic.get("displacement"))),
                int(bool(diagnostic.get("entry_fvg"))),
                int(bool(diagnostic.get("retracement"))),
                int(bool(diagnostic.get("rr"))),
                diagnostic.get("trigger_type"),
                diagnostic.get("note", ""),
                diagnostic.get("setup_id"),
                _utc_iso(),
            ),
        )
        connection.commit()


def delete_diagnostics_for_symbol(connection, symbol: str) -> None:
    """Remove live scanner state for one symbol without touching its history."""
    with _db_lock:
        connection.execute(
            "DELETE FROM strategy_diagnostics WHERE symbol = ?",
            (symbol,),
        )
        connection.commit()


def upsert_paper_trade(connection, position, updated_at):
    setup = position.setup
    with _db_lock:
        connection.execute(
            """
            INSERT INTO paper_trades (
                setup_id, symbol, timeframe, direction, status,
                entry_price, stop_price, target_price,
                opened_at, closed_at, exit_price, result, result_r,
                risk_dollars, result_dollars, guard_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(setup_id) DO UPDATE SET
                status=excluded.status,
                opened_at=excluded.opened_at,
                closed_at=excluded.closed_at,
                exit_price=excluded.exit_price,
                result=excluded.result,
                result_r=excluded.result_r,
                risk_dollars=COALESCE(excluded.risk_dollars, paper_trades.risk_dollars),
                result_dollars=excluded.result_dollars,
                guard_reason=COALESCE(excluded.guard_reason, paper_trades.guard_reason),
                updated_at=excluded.updated_at
            """,
            (
                setup.setup_id,
                setup.symbol,
                setup.timeframe,
                setup.direction,
                position.status,
                setup.entry_price,
                setup.stop_price,
                setup.target_price,
                position.opened_at.isoformat() if position.opened_at else None,
                position.closed_at.isoformat() if position.closed_at else None,
                position.exit_price,
                position.result,
                position.result_r,
                position.risk_dollars,
                position.result_dollars,
                position.guard_reason,
                updated_at,
            ),
        )
        connection.commit()


def save_quotes_batch(connection, rows):
    """Save many market quote rows in one transaction.

    Each input row is:
    (received_at, exchange_time, source, symbol, price, bid, ask)
    """
    prepared = []
    ingested_at = _utc_iso()
    for received_at, exchange_time, source, symbol, price, bid, ask in rows:
        mid = spread = spread_bps = None
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2
            spread = ask - bid
            if mid:
                spread_bps = (spread / mid) * 10_000
        prepared.append(
            (
                received_at,
                exchange_time,
                source,
                symbol,
                price,
                bid,
                ask,
                mid,
                spread,
                spread_bps,
                ingested_at,
            )
        )

    if not prepared:
        return 0

    with _db_lock:
        if _column_exists(connection, "market_quotes", "ingested_at"):
            connection.executemany(
                """
                INSERT INTO market_quotes (
                    received_at, exchange_time, source, symbol,
                    price, bid, ask, mid, spread, spread_bps, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                prepared,
            )
        else:
            # Backwards-compatible path used by legacy/unit-test schemas.
            connection.executemany(
                """
                INSERT INTO market_quotes (
                    received_at, exchange_time, source, symbol,
                    price, bid, ask, mid, spread, spread_bps
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [row[:-1] for row in prepared],
            )
        counts: dict[str, int] = {}
        for prepared_row in prepared:
            counts[prepared_row[3]] = counts.get(prepared_row[3], 0) + 1
        for symbol, amount in counts.items():
            _increment_quote_counter(connection, symbol, amount)
        connection.commit()

    _maybe_prune_market_quotes(connection, len(prepared))
    return len(prepared)


def get_engine_state(connection, key: str, default: str | None = None) -> str | None:
    row = connection.execute("SELECT value FROM engine_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_engine_state(connection, key: str, value: str) -> None:
    with _db_lock:
        connection.execute(
            """
            INSERT INTO engine_state(key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, _utc_iso()),
        )
        connection.commit()
