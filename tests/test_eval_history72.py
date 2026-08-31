from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.risk import eval_history72
from src.risk.operating_mode import OperatingModeConfig, evaluate_operating_mode


SCHEMA = """
CREATE TABLE engine_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE paper_trades (
    setup_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    opened_at TEXT,
    closed_at TEXT,
    result TEXT,
    result_r REAL,
    risk_dollars REAL,
    result_dollars REAL,
    updated_at TEXT NOT NULL
);
CREATE TABLE strategy_diagnostics (
    symbol TEXT,
    timeframe TEXT,
    market_time TEXT,
    stage TEXT
);
"""


def insert_trade(connection, setup_id: str, *, status: str = "CLOSED", result_dollars: float = 0.0):
    connection.execute(
        """
        INSERT INTO paper_trades(
            setup_id, status, opened_at, closed_at, result, result_r,
            risk_dollars, result_dollars, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            setup_id,
            status,
            "2026-08-18T22:30:00+00:00",
            "2026-08-18T22:45:00+00:00" if status == "CLOSED" else None,
            "WIN" if status == "CLOSED" else None,
            3.0 if status == "CLOSED" else None,
            500.0,
            result_dollars,
            "2026-08-18T22:45:00+00:00",
        ),
    )
    connection.commit()


class EvalHistory72Tests(unittest.TestCase):
    def make_db(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        path = Path(handle.name)
        connection = sqlite3.connect(path)
        connection.executescript(SCHEMA)
        connection.commit()
        connection.close()
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_counter_reset_preserves_trade_rows_and_archives_ids(self):
        path = self.make_db()
        connection = sqlite3.connect(path)
        insert_trade(connection, "old-trade", result_dollars=1500.0)
        connection.execute(
            "INSERT INTO strategy_diagnostics(symbol,timeframe,market_time,stage) VALUES ('GC','1m','x','WAIT')"
        )
        connection.commit()
        connection.close()

        def connect():
            return sqlite3.connect(path)

        with patch.object(eval_history72, "get_connection", side_effect=connect), patch.dict(
            os.environ, {"OTR_RESET_EVAL_TOKEN": "run-two"}, clear=False
        ):
            self.assertTrue(eval_history72.apply_nondestructive_eval_reset())
            self.assertEqual(os.environ.get("OTR_RESET_EVAL_TOKEN"), "")

        connection = sqlite3.connect(path)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM strategy_diagnostics").fetchone()[0], 0)
        excluded = json.loads(
            connection.execute(
                "SELECT value FROM engine_state WHERE key = ?",
                (eval_history72.EXCLUDED_SETUP_IDS_KEY,),
            ).fetchone()[0]
        )
        self.assertEqual(excluded, ["old-trade"])
        token = connection.execute(
            "SELECT value FROM engine_state WHERE key = ?",
            (eval_history72.RESET_STATE_KEY,),
        ).fetchone()[0]
        self.assertEqual(token, "run-two")
        connection.close()

    def test_reset_refuses_open_position_without_deleting_history(self):
        path = self.make_db()
        connection = sqlite3.connect(path)
        insert_trade(connection, "still-open", status="OPEN")
        connection.close()

        def connect():
            return sqlite3.connect(path)

        with patch.object(eval_history72, "get_connection", side_effect=connect), patch.dict(
            os.environ, {"OTR_RESET_EVAL_TOKEN": "unsafe-reset"}, clear=False
        ):
            self.assertFalse(eval_history72.apply_nondestructive_eval_reset())
            self.assertEqual(os.environ.get("OTR_RESET_EVAL_TOKEN"), "")

        connection = sqlite3.connect(path)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0], 1)
        self.assertIsNone(
            connection.execute(
                "SELECT value FROM engine_state WHERE key = ?",
                (eval_history72.RESET_STATE_KEY,),
            ).fetchone()
        )
        connection.close()

    def test_prior_ids_are_excluded_but_new_trades_still_count(self):
        path = self.make_db()
        connection = sqlite3.connect(path)
        insert_trade(connection, "old-trade", result_dollars=1500.0)
        insert_trade(connection, "new-trade", result_dollars=300.0)
        connection.execute(
            "INSERT INTO engine_state(key,value,updated_at) VALUES (?,?,?)",
            (
                eval_history72.EXCLUDED_SETUP_IDS_KEY,
                json.dumps(["old-trade"]),
                "2026-08-18T23:00:00+00:00",
            ),
        )
        connection.commit()

        operating_rows = eval_history72._operating_trade_rows_72(connection)
        self.assertEqual(len(operating_rows), 1)
        self.assertEqual(operating_rows[0][3], 300.0)

        evaluation_rows = eval_history72._evaluation_rows_72(None, connection)
        self.assertEqual([row["setup_id"] for row in evaluation_rows], ["new-trade"])

        class Setup:
            created_at = datetime(2026, 8, 18, 23, 30, tzinfo=timezone.utc)
            metadata = {}

        config = OperatingModeConfig(
            mode="EVAL",
            eval_max_trades_per_day=2,
            eval_max_trades_per_session=2,
        )
        with patch("src.risk.operating_mode._trade_rows", eval_history72._operating_trade_rows_72):
            allowed, reason, details = evaluate_operating_mode(connection, Setup(), config)
        self.assertTrue(allowed, reason)
        self.assertEqual(details["trades_today"], 1)
        self.assertEqual(details["realized_today"], 300.0)
        connection.close()


if __name__ == "__main__":
    unittest.main()
