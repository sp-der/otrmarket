from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.dashboard import server_72t


class Supervisor72TTests(unittest.TestCase):
    def test_supervisor_promotes_72t_engine(self):
        self.assertEqual(server_72t._promote_engine_72t(), "src.main_72t")

    def test_4h_is_context_only(self):
        # Lazy import prevents the inherited 7.2Q quality wrapper from mutating
        # module globals before older compatibility tests get to inspect them.
        from src import main_72t
        from src.strategies import candles as candle_module

        main_72t._install_4h_context_72t()
        self.assertEqual(candle_module.TIMEFRAME_SECONDS["4h"], 14400)
        self.assertIn("4h", main_72t.runtime.candles.timeframes)
        self.assertIsNone(main_72t.runtime.evaluate_strategy(None, "GC", "4h"))

    def test_executor_blocks_second_active_gc_idea(self):
        from src import main_72t

        main_72t._install_single_symbol_execution_guard_72t()
        paper = main_72t.runtime.paper
        saved = dict(paper.positions)
        try:
            paper.positions.clear()
            existing_setup = SimpleNamespace(
                setup_id="gc-existing",
                symbol="GC",
                timeframe="5m",
                direction="bullish",
            )
            paper.positions[existing_setup.setup_id] = SimpleNamespace(
                setup=existing_setup,
                status="PENDING",
            )
            second = SimpleNamespace(
                setup_id="gc-second",
                symbol="GC",
                timeframe="1m",
                direction="bearish",
            )
            with self.assertRaisesRegex(ValueError, "ACTIVE_SYMBOL_CONFLICT_72T"):
                paper.register_setup(second, risk_dollars=250.0)
        finally:
            paper.positions.clear()
            paper.positions.update(saved)

    def test_restart_reconciliation_expires_old_pending_and_keeps_fresh(self):
        from src import main_72t

        con = sqlite3.connect(":memory:")
        con.executescript(
            """
            CREATE TABLE paper_trades (
                setup_id TEXT PRIMARY KEY,
                symbol TEXT,
                timeframe TEXT,
                direction TEXT,
                status TEXT,
                opened_at TEXT,
                closed_at TEXT,
                exit_price REAL,
                result TEXT,
                result_r REAL,
                risk_dollars REAL,
                result_dollars REAL,
                updated_at TEXT
            );
            CREATE TABLE strategy_setups (
                setup_id TEXT PRIMARY KEY,
                created_at TEXT,
                status TEXT
            );
            """
        )
        event = datetime(2026, 8, 27, 2, 30, tzinfo=timezone.utc)
        old = event - timedelta(minutes=20)
        fresh = event - timedelta(minutes=5)
        con.execute(
            "INSERT INTO strategy_setups VALUES (?,?,?)",
            ("old-1m", old.isoformat(), "PENDING"),
        )
        con.execute(
            "INSERT INTO strategy_setups VALUES (?,?,?)",
            ("fresh-5m", fresh.isoformat(), "PENDING"),
        )
        con.execute(
            "INSERT INTO paper_trades(setup_id,symbol,timeframe,direction,status,risk_dollars,updated_at) VALUES (?,?,?,?,?,?,?)",
            ("old-1m", "GC", "1m", "bearish", "PENDING", 250.0, old.isoformat()),
        )
        con.execute(
            "INSERT INTO paper_trades(setup_id,symbol,timeframe,direction,status,risk_dollars,updated_at) VALUES (?,?,?,?,?,?,?)",
            ("fresh-5m", "GC", "5m", "bullish", "PENDING", 250.0, fresh.isoformat()),
        )
        summary = main_72t._reconcile_active_connection_72t(
            con,
            event_time=event,
            current_price=4660.0,
        )
        statuses = dict(con.execute("SELECT setup_id,status FROM paper_trades").fetchall())
        self.assertEqual(statuses["old-1m"], "INVALIDATED")
        self.assertEqual(statuses["fresh-5m"], "PENDING")
        self.assertEqual(summary["expired"], 1)
        self.assertEqual(summary["surviving"], 1)
        con.close()

    def test_training_trade_trigger_is_idempotent(self):
        from src import main_72t

        con = sqlite3.connect(":memory:")
        con.executescript(
            """
            CREATE TABLE verify_active_run_72s (
                slot INTEGER PRIMARY KEY,
                run_id TEXT,
                build TEXT,
                activated_at TEXT
            );
            INSERT INTO verify_active_run_72s VALUES (1,'VERIFY-test','7.2T','now');
            CREATE TABLE paper_trades (
                setup_id TEXT PRIMARY KEY,
                symbol TEXT,
                timeframe TEXT,
                direction TEXT,
                status TEXT,
                opened_at TEXT,
                closed_at TEXT,
                result TEXT,
                result_r REAL,
                risk_dollars REAL,
                result_dollars REAL,
                updated_at TEXT
            );
            CREATE TABLE training_trades_72t (
                run_id TEXT NOT NULL,
                setup_id TEXT NOT NULL,
                build TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_at TEXT,
                closed_at TEXT,
                result TEXT,
                result_r REAL,
                risk_dollars REAL,
                result_dollars REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, setup_id)
            );
            """
        )
        main_72t._install_idempotent_training_trade_triggers_on_connection_72t(con)
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t1","GC","5m","bullish","PENDING",None,None,None,None,250.0,None,"2026-08-27T02:00:00+00:00"),
        )
        con.execute(
            "UPDATE paper_trades SET status='OPEN',opened_at=? WHERE setup_id='t1'",
            ("2026-08-27T02:01:00+00:00",),
        )
        con.execute(
            "UPDATE paper_trades SET status='CLOSED',closed_at=?,result='WIN',result_r=2.0,result_dollars=500.0 WHERE setup_id='t1'",
            ("2026-08-27T02:05:00+00:00",),
        )
        rows = con.execute(
            "SELECT run_id,setup_id,status,result,result_r,result_dollars FROM training_trades_72t"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][:4], ("VERIFY-test", "t1", "CLOSED", "WIN"))
        self.assertEqual(rows[0][4], 2.0)
        self.assertEqual(rows[0][5], 500.0)
        con.close()


if __name__ == "__main__":
    unittest.main()
