from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.dashboard import server_72q


WIPE_TABLES = (
    "paper_trades",
    "strategy_setups",
    "strategy_diagnostics",
    "trade_intelligence",
    "shadow_trades",
    "counterfactual_setups",
    "verify_run_trades",
)


class VerifyWipe72QTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "otrmarket.db"
        con = sqlite3.connect(self.path)
        con.execute(
            "CREATE TABLE engine_state(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        for table in WIPE_TABLES:
            con.execute(f"CREATE TABLE {table}(value TEXT)")
            con.execute(f"INSERT INTO {table}(value) VALUES ('old-run')")
        for table in ("market_quotes", "candles", "market_lessons", "quote_counters", "execution_commands"):
            con.execute(f"CREATE TABLE {table}(value TEXT)")
            con.execute(f"INSERT INTO {table}(value) VALUES ('preserve')")
        con.execute(
            "INSERT INTO engine_state(key,value,updated_at) VALUES ('eval_reset_excluded_setup_ids_72','[\"old-id\"]','now')"
        )
        con.commit()
        con.close()

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        return sqlite3.connect(self.path)

    def test_explicit_verify_wipe_clears_test_state_but_preserves_market_and_learning(self):
        env = {
            "OTR_TRADING_MODE": "VERIFY",
            "OTR_VERIFY_WIPE_TOKEN": "fresh-replay-1",
            "OTR_RESET_EVAL_TOKEN": "legacy-reset-that-must-not-run",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(
            server_72q, "get_connection", side_effect=self.connect
        ):
            counts = server_72q._wipe_verify_test_state_72q()
            self.assertEqual(set(counts), set(WIPE_TABLES))
            self.assertEqual(os.environ.get("OTR_RESET_EVAL_TOKEN"), "")

        con = self.connect()
        try:
            for table in WIPE_TABLES:
                self.assertEqual(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            for table in ("market_quotes", "candles", "market_lessons", "quote_counters", "execution_commands"):
                self.assertEqual(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 1)
            excluded = con.execute(
                "SELECT value FROM engine_state WHERE key='eval_reset_excluded_setup_ids_72'"
            ).fetchone()[0]
            token = con.execute(
                "SELECT value FROM engine_state WHERE key=?",
                (server_72q.VERIFY_WIPE_STATE_KEY_72Q,),
            ).fetchone()[0]
            self.assertEqual(excluded, "[]")
            self.assertEqual(token, "fresh-replay-1")

            # A restart of the same deployment/token must never wipe new trades.
            con.execute("INSERT INTO paper_trades(value) VALUES ('new-run')")
            con.commit()
        finally:
            con.close()

        with patch.dict(os.environ, env, clear=False), patch.object(
            server_72q, "get_connection", side_effect=self.connect
        ):
            second = server_72q._wipe_verify_test_state_72q()
            self.assertEqual(second, {})

        con = self.connect()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0], 1)
        finally:
            con.close()

    def test_wipe_is_disabled_outside_verify(self):
        with patch.dict(
            os.environ,
            {"OTR_TRADING_MODE": "EVAL", "OTR_VERIFY_WIPE_TOKEN": "should-not-run"},
            clear=False,
        ), patch.object(server_72q, "get_connection", side_effect=AssertionError("should not connect")):
            self.assertEqual(server_72q._wipe_verify_test_state_72q(), {})


if __name__ == "__main__":
    unittest.main()
