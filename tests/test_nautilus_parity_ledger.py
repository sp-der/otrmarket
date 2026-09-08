from __future__ import annotations

import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.integrations.nautilus_shadow.ledger import (
    GoldTradeCandidate,
    ParityLedgerRecord,
    classify_parity_differences,
    ensure_parity_ledger,
    load_candidate_ticks,
    load_closed_gold_candidates,
    persist_parity_record,
    resolve_signal_contract,
    run_recent_gold_parity,
    shadow_quantity,
    trade_path_match,
)
from src.integrations.nautilus_shadow.parity import ExecutionSnapshot, ParityResult


class NautilusParityLedgerTests(unittest.TestCase):
    def connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
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
            opened_at=now + timedelta(seconds=1),
            closed_at=now + timedelta(seconds=5),
            exit_price=3504.0,
            result="WIN",
            result_r=2.0,
            risk_dollars=risk_dollars,
            result_dollars=risk_dollars * 2.0,
        )

    def insert_candidate_rows(self, connection: sqlite3.Connection) -> None:
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
                '2026-09-01T13:30:01+00:00','2026-09-01T13:30:05+00:00',3504,
                'WIN',2.0,100,200,NULL,'2026-09-01T13:30:05+00:00'
            )
            """
        )
        connection.commit()

    def add_quote(self, connection, timestamp: datetime, contract: str, price: float) -> None:
        text = timestamp.isoformat()
        connection.execute(
            """
            INSERT INTO market_quotes(received_at, exchange_time, source, symbol, price, bid, ask)
            VALUES (?, ?, ?, 'GC', ?, ?, ?)
            """,
            (text, text, f"ninjatrader:{contract}", price, price, price),
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
            self.insert_candidate_rows(connection)
            rows = load_closed_gold_candidates(connection, limit=10)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].setup_id, "gc-1")
            self.assertEqual(rows[0].result_dollars, 200.0)
        finally:
            connection.close()

    def test_candidate_ticks_never_include_pre_setup_quotes(self):
        connection = self.connection()
        try:
            candidate = self.candidate()
            self.add_quote(connection, candidate.created_at - timedelta(seconds=1), "GC DEC26", 3499.0)
            self.add_quote(connection, candidate.created_at, "GC DEC26", 3501.0)
            self.add_quote(connection, candidate.created_at + timedelta(seconds=1), "GC DEC26", 3500.0)
            connection.commit()
            ticks = load_candidate_ticks(connection, candidate, signal_contract="GC DEC26")
            self.assertEqual([item.price for item in ticks], [3501.0, 3500.0])
            self.assertTrue(all(item.timestamp >= candidate.created_at for item in ticks))
        finally:
            connection.close()

    def test_contract_resolution_anchors_to_setup_creation(self):
        connection = self.connection()
        try:
            candidate = self.candidate()
            self.add_quote(connection, candidate.created_at, "GC DEC26", 3500.0)
            self.add_quote(connection, candidate.created_at + timedelta(days=1), "GC FEB27", 3550.0)
            connection.commit()
            self.assertEqual(resolve_signal_contract(connection, candidate), "GC DEC26")
        finally:
            connection.close()

    def test_batch_reports_one_trade_error_without_aborting(self):
        connection = self.connection()
        try:
            self.insert_candidate_rows(connection)
            with patch(
                "src.integrations.nautilus_shadow.ledger.run_candidate_parity",
                side_effect=ValueError("retention rolled past trade"),
            ):
                report = run_recent_gold_parity(connection, limit=10)
            self.assertEqual(report.requested, 1)
            self.assertEqual(report.records, ())
            self.assertEqual(len(report.errors), 1)
            self.assertEqual(report.errors[0].setup_id, "gc-1")
            self.assertIn("retention", report.errors[0].error)
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
