from __future__ import annotations

import os
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.integrations.nautilus_shadow.auto_certify import (
    _auto_enabled,
    auto_certifier_snapshot,
    process_one_auto_certification,
)


SETUP_SCHEMA = """
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


class NautilusAutoCertifyTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(SETUP_SCHEMA)
        self.connection.execute(
            """
            INSERT INTO strategy_setups(
                setup_id,symbol,timeframe,direction,created_at,trigger_type,
                entry_price,stop_price,target_price,risk_reward,status,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "auto-1",
                "GC",
                "5m",
                "bullish",
                "2026-09-07T13:30:00+00:00",
                "liquidity_sweep",
                3500.0,
                3498.0,
                3504.0,
                2.0,
                "ACCEPTED",
                "{}",
            ),
        )
        self.connection.execute(
            """
            INSERT INTO paper_trades(
                setup_id,symbol,timeframe,direction,status,entry_price,stop_price,target_price,
                opened_at,closed_at,exit_price,result,result_r,risk_dollars,result_dollars,
                guard_reason,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "auto-1",
                "GC",
                "5m",
                "bullish",
                "CLOSED",
                3500.0,
                3498.0,
                3504.0,
                "2026-09-07T13:30:01+00:00",
                "2026-09-07T13:31:00+00:00",
                3504.0,
                "WIN",
                2.0,
                500.0,
                1000.0,
                "",
                "2026-09-07T13:31:00+00:00",
            ),
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()

    def _trade_snapshot(self):
        setup = self.connection.execute(
            "SELECT entry_price,stop_price,target_price,risk_reward,status,payload_json FROM strategy_setups WHERE setup_id='auto-1'"
        ).fetchone()
        trade = self.connection.execute(
            "SELECT status,entry_price,stop_price,target_price,result,result_r,risk_dollars,result_dollars FROM paper_trades WHERE setup_id='auto-1'"
        ).fetchone()
        return setup, trade

    def test_success_is_recorded_without_touching_strategy_or_trade_rows(self):
        before = self._trade_snapshot()

        def fake_runner(connection, candidate):
            self.assertEqual(candidate.setup_id, "auto-1")
            return SimpleNamespace(matched_trade_path=True, matched_full=False)

        result = process_one_auto_certification(self.connection, runner=fake_runner)
        self.assertEqual(result["status"], "CERTIFIED")
        self.assertTrue(result["matched_trade_path"])
        self.assertFalse(result["matched_full"])
        self.assertEqual(self._trade_snapshot(), before)

        job = self.connection.execute(
            "SELECT status,attempts,completed_at,last_error FROM nautilus_shadow_auto_jobs WHERE setup_id='auto-1'"
        ).fetchone()
        self.assertEqual(job[0], "CERTIFIED")
        self.assertEqual(job[1], 1)
        self.assertIsNotNone(job[2])
        self.assertEqual(job[3], "")

        # A completed job is never claimed again even if the scanner loops.
        self.assertIsNone(process_one_auto_certification(self.connection, runner=fake_runner))

    def test_retention_failure_retries_three_times_then_quarantines_old_trade(self):
        before = self._trade_snapshot()

        def stale_runner(connection, candidate):
            raise ValueError(
                f"Not enough retained post-setup Gold ticks for {candidate.setup_id}; "
                "the raw quote retention window may have rolled past this trade"
            )

        first = process_one_auto_certification(self.connection, runner=stale_runner)
        second = process_one_auto_certification(self.connection, runner=stale_runner)
        third = process_one_auto_certification(self.connection, runner=stale_runner)
        fourth = process_one_auto_certification(self.connection, runner=stale_runner)

        self.assertEqual(first["status"], "WAITING_TICKS")
        self.assertEqual(second["status"], "WAITING_TICKS")
        self.assertEqual(third["status"], "UNAVAILABLE_RETENTION")
        self.assertIsNone(fourth)
        self.assertEqual(self._trade_snapshot(), before)

        job = self.connection.execute(
            "SELECT status,attempts,completed_at FROM nautilus_shadow_auto_jobs WHERE setup_id='auto-1'"
        ).fetchone()
        self.assertEqual(job[0], "UNAVAILABLE_RETENTION")
        self.assertEqual(job[1], 3)
        self.assertIsNotNone(job[2])

    def test_snapshot_reports_job_counts(self):
        process_one_auto_certification(
            self.connection,
            runner=lambda connection, candidate: SimpleNamespace(
                matched_trade_path=True,
                matched_full=True,
            ),
        )
        snapshot = auto_certifier_snapshot(self.connection)
        self.assertEqual(snapshot["counts"].get("CERTIFIED"), 1)
        self.assertEqual(snapshot["max_attempts"], 3)
        self.assertEqual(snapshot["recent"][0]["setup_id"], "auto-1")

    def test_default_auto_start_is_railway_only_but_can_be_overridden(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_auto_enabled())
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}, clear=True):
            self.assertTrue(_auto_enabled())
        with patch.dict(
            os.environ,
            {"RAILWAY_ENVIRONMENT": "production", "OTR_NAUTILUS_AUTO_CERTIFY": "0"},
            clear=True,
        ):
            self.assertFalse(_auto_enabled())
        with patch.dict(os.environ, {"OTR_NAUTILUS_AUTO_CERTIFY": "1"}, clear=True):
            self.assertTrue(_auto_enabled())


if __name__ == "__main__":
    unittest.main()
