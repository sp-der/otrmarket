from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone

from src.integrations.nautilus_shadow.gold_replay import (
    StoredGoldTick,
    contract_family,
    contract_multiplier,
    load_latest_gold_ticks,
)


class NautilusGoldReplayTests(unittest.TestCase):
    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.execute(
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
            )
            """
        )
        return connection

    def insert(self, connection, timestamp, source, price):
        connection.execute(
            "INSERT INTO market_quotes(received_at, exchange_time, source, symbol, price, bid, ask) VALUES (?, ?, ?, 'GC', ?, ?, ?)",
            (timestamp, timestamp, source, price, price - 0.1, price + 0.1),
        )

    def test_contract_family_and_multiplier(self):
        self.assertEqual(contract_family("GC DEC26"), "GC")
        self.assertEqual(contract_multiplier("GC DEC26"), 100)
        self.assertEqual(contract_family("MGC DEC26"), "MGC")
        self.assertEqual(contract_multiplier("MGC DEC26"), 10)

    def test_contract_is_recovered_from_existing_source_field(self):
        tick = StoredGoldTick(
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            "ninjatrader:MGC DEC26",
            3500.0,
        )
        self.assertEqual(tick.contract, "MGC DEC26")

    def test_latest_contract_is_not_mixed_with_older_replay_contract(self):
        connection = self.connection()
        try:
            self.insert(connection, "2026-08-01T12:00:00+00:00", "ninjatrader:GC DEC26", 3400.0)
            self.insert(connection, "2026-09-01T12:00:00+00:00", "ninjatrader:MGC DEC26", 3500.0)
            self.insert(connection, "2026-09-01T12:00:01+00:00", "ninjatrader:MGC DEC26", 3500.1)
            connection.commit()

            ticks = load_latest_gold_ticks(connection, limit=100)
            self.assertEqual(len(ticks), 2)
            self.assertTrue(all(item.contract == "MGC DEC26" for item in ticks))
            self.assertEqual([item.price for item in ticks], [3500.0, 3500.1])
        finally:
            connection.close()

    def test_exact_contract_can_be_requested(self):
        connection = self.connection()
        try:
            self.insert(connection, "2026-08-01T12:00:00+00:00", "ninjatrader:GC DEC26", 3400.0)
            self.insert(connection, "2026-08-01T12:00:01+00:00", "ninjatrader:GC DEC26", 3400.1)
            self.insert(connection, "2026-09-01T12:00:00+00:00", "ninjatrader:MGC DEC26", 3500.0)
            connection.commit()

            ticks = load_latest_gold_ticks(connection, limit=100, contract="GC DEC26")
            self.assertEqual(len(ticks), 2)
            self.assertTrue(all(item.contract == "GC DEC26" for item in ticks))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
