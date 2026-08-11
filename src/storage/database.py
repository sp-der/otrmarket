import sqlite3
from pathlib import Path
from threading import Lock


DB_PATH = Path("data/otrmarket.db")

_db_lock = Lock()


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
    )

    connection.execute("PRAGMA journal_mode=WAL;")

    connection.execute(
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
        )
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_quotes_symbol_time
        ON market_quotes(symbol, received_at)
        """
    )

    connection.commit()

    return connection


def save_quote(
    connection,
    received_at,
    exchange_time,
    source,
    symbol,
    price,
    bid,
    ask,
):
    mid = None
    spread = None
    spread_bps = None

    if bid is not None and ask is not None:
        mid = (bid + ask) / 2
        spread = ask - bid

        if mid:
            spread_bps = (spread / mid) * 10_000

    with _db_lock:
        connection.execute(
            """
            INSERT INTO market_quotes (
                received_at,
                exchange_time,
                source,
                symbol,
                price,
                bid,
                ask,
                mid,
                spread,
                spread_bps
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )

        connection.commit()