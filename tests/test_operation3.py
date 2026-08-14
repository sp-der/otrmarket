import sqlite3
import unittest

from src.bridge.futures import normalize_bridge_symbol, source_name
from src.storage.database import save_quotes_batch


class Operation3Tests(unittest.TestCase):
    def test_futures_symbol_normalization(self):
        self.assertEqual(normalize_bridge_symbol("nq"), "NQ")
        self.assertEqual(normalize_bridge_symbol(" ES "), "ES")
        self.assertEqual(normalize_bridge_symbol("GC"), "GC")
        with self.assertRaises(ValueError):
            normalize_bridge_symbol("QQQ")

    def test_contract_source_name(self):
        self.assertEqual(source_name("NQ SEP26"), "ninjatrader:NQ SEP26")

    def test_batch_quote_insert(self):
        con = sqlite3.connect(":memory:")
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
        count = save_quotes_batch(
            con,
            [
                (
                    "2026-08-14T22:00:00+00:00",
                    "2026-08-14T22:00:00+00:00",
                    "ninjatrader:NQ SEP26",
                    "NQ",
                    30148.25,
                    30148.00,
                    30148.25,
                ),
                (
                    "2026-08-14T22:00:00.050000+00:00",
                    "2026-08-14T22:00:00.050000+00:00",
                    "ninjatrader:ES SEP26",
                    "ES",
                    7802.25,
                    7802.00,
                    7802.25,
                ),
            ],
        )
        self.assertEqual(count, 2)
        rows = con.execute("SELECT symbol, source FROM market_quotes ORDER BY id").fetchall()
        self.assertEqual(rows[0], ("NQ", "ninjatrader:NQ SEP26"))
        self.assertEqual(rows[1], ("ES", "ninjatrader:ES SEP26"))
        con.close()


if __name__ == "__main__":
    unittest.main()
