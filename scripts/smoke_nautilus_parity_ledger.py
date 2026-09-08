from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.nautilus_shadow.ledger import run_recent_gold_parity


connection = sqlite3.connect(":memory:")
try:
    connection.executescript(
        """
        CREATE TABLE market_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            exchange_time TEXT,
            source TEXT NOT NULL,
            symbol TEXT NOT NULL,
            price REAL,
            bid REAL,
            ask REAL
        );

        CREATE TABLE strategy_setups (
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

        CREATE TABLE paper_trades (
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
        """
    )

    created = "2026-09-01T13:30:00+00:00"
    opened = "2026-09-01T13:30:01+00:00"
    closed = "2026-09-01T13:30:03+00:00"
    connection.execute(
        "INSERT INTO strategy_setups VALUES (?, 'GC', '5m', 'bullish', ?, 'FVG', 3500, 3498, 3504, 2.0, 'ACCEPTED', '{}')",
        ("ledger-smoke", created),
    )
    connection.execute(
        """
        INSERT INTO paper_trades VALUES (
            'ledger-smoke','GC','5m','bullish','CLOSED',3500,3498,3504,
            ?,?,3504,'WIN',2.0,20.0,40.0,NULL,?
        )
        """,
        (opened, closed, closed),
    )

    for timestamp, price in (
        (created, 3501.0),
        (opened, 3500.0),
        ("2026-09-01T13:30:02+00:00", 3501.0),
        (closed, 3504.0),
        ("2026-09-01T13:30:04+00:00", 3504.0),
    ):
        connection.execute(
            """
            INSERT INTO market_quotes(received_at, exchange_time, source, symbol, price, bid, ask)
            VALUES (?, ?, 'ninjatrader:GC DEC26', 'GC', ?, ?, ?)
            """,
            (timestamp, timestamp, price, price, price),
        )
    connection.commit()

    report = run_recent_gold_parity(connection, limit=5)
    assert report.requested == 1, report
    assert not report.errors, report.errors
    assert len(report.records) == 1, report.records
    record = report.records[0]
    assert record.signal_contract == "GC DEC26", record
    assert record.execution_contract == "MGC DEC26", record
    assert record.quantity == 1, record
    assert record.matched_trade_path is True, record
    assert record.matched_full is True, record
    assert record.difference_categories == (), record
    stored = connection.execute(
        "SELECT matched_trade_path, matched_full FROM nautilus_shadow_parity WHERE setup_id='ledger-smoke'"
    ).fetchone()
    assert stored == (1, 1), stored

    print(
        json.dumps(
            {
                "setup_id": record.setup_id,
                "signal_contract": record.signal_contract,
                "execution_contract": record.execution_contract,
                "quantity": record.quantity,
                "matched_trade_path": record.matched_trade_path,
                "matched_full": record.matched_full,
                "paper_risk_dollars": record.paper_risk_dollars,
                "shadow_risk_dollars": record.shadow_risk_dollars,
            },
            indent=2,
            sort_keys=True,
        )
    )
finally:
    connection.close()
