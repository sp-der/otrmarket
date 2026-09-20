from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.execution.paper import PaperExecutor
from src.research.lab_v01 import closed_gold_trades, run_scope_summary
from src.research.run_scope import current_run_id, rotate_run_id
from src.storage import database
from src.strategies.models import Displacement, FairValueGap, StrategySetup


def _gc_setup(setup_id: str, minute: int):
    t = datetime(2026, 9, 19, 14, minute, tzinfo=timezone.utc)
    fvg = FairValueGap("GC", "5m", "bullish", 3500.0, 3500.0, t, t, t)
    displacement = Displacement("GC", "5m", "bullish", t, 3500.0, 3500.0, 2, 2)
    return StrategySetup(
        setup_id, "GC", "5m", "bullish", t, fvg, "liquidity_sweep", {},
        displacement, fvg, 3500.0, 3495.0, 3510.0, 2.0,
    )


def _create_and_close_gc_trade(connection, setup_id: str, minute: int, *, result: str = "WIN") -> None:
    setup = _gc_setup(setup_id, minute)
    database.save_setup(connection, setup)
    executor = PaperExecutor()
    position = executor.register_setup(setup, risk_dollars=500.0)
    database.upsert_paper_trade(connection, position, setup.created_at.isoformat())

    executor.on_price("GC", setup.entry_price, setup.created_at)
    database.upsert_paper_trade(connection, position, setup.created_at.isoformat())

    exit_price = setup.target_price if result == "WIN" else setup.stop_price
    executor.on_price("GC", exit_price, setup.created_at)
    database.upsert_paper_trade(connection, position, setup.created_at.isoformat())


class ResearchRunScopingTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = database.DB_PATH
        self.tempdir = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tempdir.name) / "otrmarket_run_scope_test.db"

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_two_runs_in_db_default_scopes_to_current_run_only(self):
        connection = database.get_connection()
        run_one = current_run_id(connection)
        _create_and_close_gc_trade(connection, "gc-run1-a", 0)
        _create_and_close_gc_trade(connection, "gc-run1-b", 1)

        run_two = rotate_run_id(connection)
        self.assertNotEqual(run_one, run_two)
        _create_and_close_gc_trade(connection, "gc-run2-a", 2)

        current_only = closed_gold_trades(connection, limit=50)
        connection.close()

        self.assertEqual({trade["setup_id"] for trade in current_only}, {"gc-run2-a"})
        self.assertTrue(all(trade["run_id"] == run_two for trade in current_only))

    def test_explicit_historical_query_can_access_prior_run(self):
        connection = database.get_connection()
        run_one = current_run_id(connection)
        _create_and_close_gc_trade(connection, "gc-hist-a", 0)

        rotate_run_id(connection)
        _create_and_close_gc_trade(connection, "gc-hist-b", 1)

        historical = closed_gold_trades(connection, limit=50, run_id=run_one)
        connection.close()

        self.assertEqual({trade["setup_id"] for trade in historical}, {"gc-hist-a"})

    def test_mixed_run_pooling_is_never_implicit(self):
        connection = database.get_connection()
        _create_and_close_gc_trade(connection, "gc-pool-a", 0)
        rotate_run_id(connection)
        _create_and_close_gc_trade(connection, "gc-pool-b", 1)

        default_scoped = closed_gold_trades(connection, limit=50)
        explicit_all = closed_gold_trades(connection, limit=50, run_id=None)
        connection.close()

        self.assertEqual(len(default_scoped), 1)
        self.assertEqual(len(explicit_all), 2)

    def test_run_scope_summary_reports_current_and_available_runs(self):
        connection = database.get_connection()
        run_one = current_run_id(connection)
        _create_and_close_gc_trade(connection, "gc-sum-a", 0)
        _create_and_close_gc_trade(connection, "gc-sum-b", 1)

        run_two = rotate_run_id(connection)
        _create_and_close_gc_trade(connection, "gc-sum-c", 2)

        summary = run_scope_summary(connection)
        connection.close()

        self.assertEqual(summary["current_run_id"], run_two)
        self.assertEqual(summary["current_run_sample_count"], 1)
        by_run = {row["run_id"]: row for row in summary["available_runs"]}
        self.assertEqual(by_run[run_one]["setup_count"], 2)
        self.assertEqual(by_run[run_two]["setup_count"], 1)
        self.assertTrue(by_run[run_two]["is_current"])
        self.assertFalse(by_run[run_one]["is_current"])
        self.assertEqual(summary["operation_version"], "Operation 8.1")

    def test_current_run_survives_process_restart(self):
        connection = database.get_connection()
        run_id = current_run_id(connection)
        connection.close()

        # A fresh connection simulates a process restart; engine_state is
        # durable so the same run id must come back, not a freshly minted one.
        reconnected = database.get_connection()
        restarted_run_id = current_run_id(reconnected)
        reconnected.close()

        self.assertEqual(run_id, restarted_run_id)


if __name__ == "__main__":
    unittest.main()
