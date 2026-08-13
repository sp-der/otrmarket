from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock

from src.strategies.models import Candle, StrategySetup

DB_PATH = Path("data/otrmarket.db")
_db_lock = Lock()


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
            spread_bps REAL
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
        """
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
                price, bid, ask, mid, spread, spread_bps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (received_at, exchange_time, source, symbol, price, bid, ask, mid, spread, spread_bps),
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
