from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timezone

from src.integrations.nautilus_shadow.ledger import (
    GoldTradeCandidate,
    ParityLedgerRecord,
    classify_parity_differences,
    ensure_parity_ledger,
    load_closed_gold_candidates,
    persist_parity_record,
    shadow_quantity,
    trade_path_match,
)
from src.integrations.nautilus_shadow.parity import ExecutionSnapshot, ParityResult


class NautilusParityLedgerTests(unittest.TestCase):
    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
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
        return connection

    def candidate(self, risk_dollars: float = 100.0) -> GoldTradeCandidate:
        now = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
        return GoldTradeCandidate(
            setup_id="gc-1",
            timeframe="5m",
            direction="bullish",
            created_at=now,
            entry_price=3500.0,
            stop_price=3498.0,
            target_price=3504.0,
            risk_reward=2.0,
            paper_status="CLOSED",
            opened_at=now,
            closed_at=now,
            exit_price=3504.0,
            result="WIN",
            result_r=2.0,
            risk_dollars=risk_dollars,
            result_dollars=risk_dollars * 2.0,
        )

    def test_ledger_schema_is_additive(self):
        connection = self.connection()
        try:
            ensure_parity_ledger(connection)
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='nautilus_shadow_parity'"
            ).fetchone()
            self.assertIsNotNone(table)
            self.assertIsNotNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_trades'"
                ).fetchone()
            )
        finally:
            connection.close()

    def test_difference_categories_distinguish_pnl_only(self):
        result = ParityResult(
            setup_id="gc-1",
            matched=False,
            differences=("result_dollars:200.0!=180.0",),
        )
        categories = classify_parity_differences(result)
        self.assertEqual(categories, ("PNL",))
        self.assertTrue(trade_path_match(categories))

        entry_result = ParityResult(
            setup_id="gc-1",
            matched=False,
            differences=("entry_price:3500.0!=3500.1", "result_dollars:200.0!=180.0"),
        )
        categories = classify_parity_differences(entry_result)
        self.assertEqual(categories, ("ENTRY", "PNL"))
        self.assertFalse(trade_path_match(categories))

    def test_shadow_quantity_uses_whole_mgc_contracts(self):
        # $20 risk per MGC at a 2-point stop. $105 budget funds five contracts.
        self.assertEqual(shadow_quantity(self.candidate(105.0)), 5)

    def test_load_candidates_filters_closed_gold_only(self):
        connection = self.connection()
        try:
            setup_values = (
                "gc-1", "GC", "5m", "bullish", "2026-09-01T13:30:00+00:00",
                "FVG", 3500.0, 3498.0, 3504.0, 2.0, "ACCEPTED", "{}",
            )
            connection.execute(
                "INSERT INTO strategy_setups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                setup_values,
            )
            connection.execute(
                """
                INSERT INTO paper_trades VALUES (
                    'gc-1','GC','5m','bullish','CLOSED',3500,3498,3504,
                    '2026-09-01T13:30:01+00:00','2026-09-01T13:31:00+00:00',3504,
                    'WIN',2.0,100,200,NULL,'2026-09-01T13:31:00+00:00'
                )
                """
            )
            rows = load_closed_gold_candidates(connection, limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].setup_id, "gc-1")
            self.assertEqual(rows[0].result_dollars, 200.0)
        finally:
            connection.close()

    def test_persist_upserts_one_row_per_setup(self):
        connection = self.connection()
        try:
            snapshot = ExecutionSnapshot("gc-1", "CLOSED", 3500.0, 3504.0, "WIN", 2.0, 200.0)
            record = ParityLedgerRecord(
                setup_id="gc-1",
                observed_at="2026-09-01T14:00:00+00:00",
                signal_contract="GC DEC26",
                execution_contract="MGC DEC26",
                quantity=5,
                paper_risk_dollars=100.0,
                shadow_risk_dollars=100.0,
                matched_trade_path=True,
                matched_full=True,
                difference_categories=(),
                difference_details=(),
                paper_snapshot=snapshot,
                shadow_snapshot=snapshot,
                shadow_orders_json="[]",
            )
            persist_parity_record(connection, record)
            persist_parity_record(connection, record)
            count = connection.execute("SELECT COUNT(*) FROM nautilus_shadow_parity").fetchone()[0]
            self.assertEqual(count, 1)
            row = connection.execute(
                "SELECT difference_categories_json, paper_snapshot_json FROM nautilus_shadow_parity"
            ).fetchone()
            self.assertEqual(json.loads(row[0]), [])
            self.assertEqual(json.loads(row[1])["result"], "WIN")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
