import sqlite3
import tempfile
import unittest
from pathlib import Path

import src.storage.database as database
from src.runtime.session import StrategySession
from src.strategies.confluence import ConfluenceEngine, PendingContext
from src.strategies.models import FairValueGap


class Operation41Tests(unittest.TestCase):
    def test_stage_timer_resets_after_transition(self):
        engine = ConfluenceEngine(
            context_expiry_bars=16,
            displacement_expiry_bars=8,
            entry_fvg_expiry_bars=8,
        )
        from datetime import datetime, timezone
        t = datetime(2026, 8, 13, tzinfo=timezone.utc)
        pd = FairValueGap(
            symbol="GC",
            timeframe="1m",
            direction="bullish",
            lower=100.0,
            upper=101.0,
            formed_at=t,
            candle1_time=t,
            candle3_time=t,
        )
        context = PendingContext(
            symbol="GC",
            timeframe="1m",
            direction="bullish",
            pd_array=pd,
            stage="WAIT_ENTRY_FVG",
            started_bar_count=2,
            stage_bar_count=20,
        )

        expired, elapsed, limit = engine._stage_expired(context, 27)
        self.assertFalse(expired)
        self.assertEqual(elapsed, 7)
        self.assertEqual(limit, 8)

        expired, elapsed, limit = engine._stage_expired(context, 29)
        self.assertTrue(expired)
        self.assertEqual(elapsed, 9)
        self.assertEqual(limit, 8)

    def test_replay_session_isolates_btc_only(self):
        session = StrategySession()
        self.assertTrue(session.strategy_enabled("BTC-USD"))
        self.assertFalse(session.observe("NQ", "LIVE"))
        self.assertTrue(session.strategy_enabled("BTC-USD"))

        self.assertTrue(session.observe("NQ", "REPLAY"))
        self.assertFalse(session.strategy_enabled("BTC-USD"))
        self.assertFalse(session.paper_updates_enabled("BTC-USD"))
        self.assertTrue(session.strategy_enabled("NQ"))
        self.assertTrue(session.strategy_enabled("ES"))
        self.assertTrue(session.strategy_enabled("GC"))

    def test_legacy_database_adds_ingested_at_before_index(self):
        original_path = database.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            con = sqlite3.connect(db_path)
            con.execute(
                """
                CREATE TABLE market_quotes (
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
            con.execute(
                "INSERT INTO market_quotes(received_at, source, symbol, price) VALUES (?, ?, ?, ?)",
                ("2026-08-13T04:00:00+00:00", "ninjatrader:NQ SEP26", "NQ", 30000.0),
            )
            con.commit()
            con.close()

            database.DB_PATH = db_path
            upgraded = database.get_connection()
            columns = {row[1] for row in upgraded.execute("PRAGMA table_info(market_quotes)")}
            indexes = {row[1] for row in upgraded.execute("PRAGMA index_list(market_quotes)")}
            ingested = upgraded.execute("SELECT ingested_at FROM market_quotes LIMIT 1").fetchone()[0]
            upgraded.close()
            database.DB_PATH = original_path

            self.assertIn("ingested_at", columns)
            self.assertIn("idx_market_quotes_symbol_ingest", indexes)
            self.assertEqual(ingested, "2026-08-13T04:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
