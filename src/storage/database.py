from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from src.strategies.models import Candle, StrategySetup

DB_PATH = Path("data/otrmarket.db")
_db_lock = Lock()


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
    connection.commit()
    return connection


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
        connection.commit()


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
                opened_at, closed_at, exit_price, result, result_r, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(setup_id) DO UPDATE SET
                status=excluded.status,
                opened_at=excluded.opened_at,
                closed_at=excluded.closed_at,
                exit_price=excluded.exit_price,
                result=excluded.result,
                result_r=excluded.result_r,
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
        connection.commit()

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
