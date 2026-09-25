from __future__ import annotations

import importlib
import pathlib
import sqlite3
import tempfile
import unittest


def fresh_database(tmpdir: str):
    import src.storage.database as database
    import src.storage.database_concurrency80 as concurrency

    database = importlib.reload(database)
    database.DB_PATH = pathlib.Path(tmpdir) / "otrmarket.db"
    if hasattr(database, "_otr80_concurrency_installed"):
        delattr(database, "_otr80_concurrency_installed")
    concurrency._initialized_paths.clear()
    return database


def view_names(connection):
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        )
    }


class RunScopingViewTests(unittest.TestCase):
    def test_both_initializers_create_active_views(self):
        for production in (False, True):
            with self.subTest(production=production), tempfile.TemporaryDirectory() as tmp:
                database = fresh_database(tmp)
                if production:
                    from src.storage.database_concurrency80 import install
                    install()
                connection = database.get_connection()
                try:
                    self.assertEqual(
                        view_names(connection),
                        {"active_paper_trades", "active_strategy_setups"},
                    )
                    connection.execute(
                        "SELECT COALESCE(MAX(rowid),0) FROM active_paper_trades"
                    ).fetchone()
                finally:
                    connection.close()

    def test_plain_and_production_schema_match(self):
        schemas = []
        for production in (False, True):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            database = fresh_database(tmp.name)
            if production:
                from src.storage.database_concurrency80 import install
                install()
            connection = database.get_connection()
            self.addCleanup(connection.close)
            rows = connection.execute(
                "SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            schema = {(kind, name) for kind, name in rows}
            columns = {
                name: tuple(
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({name})")
                )
                for kind, name in rows
                if kind == "table"
            }
            schemas.append((schema, columns))
        self.assertEqual(schemas[0], schemas[1])


class RunRotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = fresh_database(self.tmp.name)
        from src.storage.database_concurrency80 import install
        install()
        self.connection = self.database.get_connection()
        self.addCleanup(self.connection.close)

    def add_trade(self, setup_id: str, dollars: float, result: str = "WIN"):
        stamp = "2026-09-18T14:30:00+00:00"
        self.connection.execute(
            """INSERT INTO strategy_setups(
                setup_id,symbol,timeframe,direction,created_at,trigger_type,
                entry_price,stop_price,target_price,risk_reward,status,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                setup_id, "GC", "5m", "bullish", stamp, "fvg",
                2650.0, 2645.0, 2660.0, 2.0, "CLOSED", "{}",
            ),
        )
        self.connection.execute(
            """INSERT INTO paper_trades(
                setup_id,symbol,timeframe,direction,status,entry_price,stop_price,
                target_price,opened_at,closed_at,exit_price,result,result_r,
                risk_dollars,result_dollars,guard_reason,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                setup_id, "GC", "5m", "bullish", "CLOSED",
                2650.0, 2645.0, 2660.0, stamp, stamp, 2660.0,
                result, 2.0, 500.0, dollars, "", stamp,
            ),
        )
        self.connection.commit()

    def test_rotation_zeros_active_ledger_and_preserves_baseline(self):
        from src.research.run_archive81 import start_fresh_run81
        from src.research.run_scope import current_run_id

        baseline_run = current_run_id(self.connection)
        self.add_trade("b1", 7000.0)
        self.add_trade("b2", 6000.0)
        self.add_trade("b3", -500.0, "LOSS")

        result = start_fresh_run81(
            self.connection,
            baseline_label="Full Week Baseline - Pre Candidate Funnel V2",
            new_label="Candidate Funnel V2 - Same Week Validation",
        )
        self.assertNotEqual(result["run_id"], baseline_run)
        self.assertEqual(result["archive"]["net_pnl"], 12500.0)

        active = self.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(result_dollars),0) "
            "FROM active_paper_trades"
        ).fetchone()
        self.assertEqual((int(active[0]), float(active[1])), (0, 0.0))

        base = self.connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(result_dollars),0) FROM paper_trades"
        ).fetchone()
        self.assertEqual((int(base[0]), float(base[1])), (3, 12500.0))

    def test_risk_guard_resets_after_rotation(self):
        from datetime import datetime, timezone
        from src.research.run_archive81 import start_fresh_run81
        from src.risk.evaluation import EvaluationConfig, EvaluationRiskGuard

        self.add_trade("b1", 13000.0)
        guard = EvaluationRiskGuard(
            EvaluationConfig(enabled=True, starting_balance=50_000.0)
        )
        ref = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)
        self.assertEqual(guard.snapshot(self.connection, ref)["realized_pnl"], 13000.0)

        start_fresh_run81(
            self.connection,
            baseline_label="Full Week Baseline - Pre Candidate Funnel V2",
            new_label="Candidate Funnel V2 - Same Week Validation",
        )
        after = guard.snapshot(self.connection, ref)
        self.assertEqual(after["realized_pnl"], 0.0)
        self.assertEqual(after["balance"], 50_000.0)


class SnapshotImmutabilityTests(unittest.TestCase):
    def test_hardened_training_trigger_is_installed_after_legacy_trigger(self):
        source = pathlib.Path("src/main_80.py").read_text(encoding="utf-8")
        self.assertLess(
            source.index("_install_idempotent_training_trade_triggers_72t()"),
            source.index("harden_training_trade_triggers_80("),
        )

    def test_hardened_decision_trigger_does_not_replace_frozen_fields(self):
        from src.storage.training_trigger_guard80 import harden_training_trade_triggers_80

        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(
            """
            CREATE TABLE training_active_run_72t (
                slot INTEGER PRIMARY KEY, run_id TEXT, build TEXT
            );
            INSERT INTO training_active_run_72t VALUES (1,'run-81-test','8.1');
            CREATE TABLE strategy_setups (
                setup_id TEXT PRIMARY KEY, symbol TEXT, timeframe TEXT,
                direction TEXT, created_at TEXT, trigger_type TEXT,
                entry_price REAL, stop_price REAL, target_price REAL,
                risk_reward REAL, status TEXT, payload_json TEXT
            );
            CREATE TABLE training_decisions_72t (
                run_id TEXT NOT NULL, setup_id TEXT NOT NULL, build TEXT,
                symbol TEXT, timeframe TEXT, direction TEXT, created_at TEXT,
                trigger_type TEXT, entry_price REAL, stop_price REAL,
                target_price REAL, risk_reward REAL, status TEXT,
                payload_json TEXT, last_seen_at TEXT,
                PRIMARY KEY(run_id, setup_id)
            );
            """
        )
        harden_training_trade_triggers_80(connection)
        sql = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        )["training_decision_update_72t"].upper()
        self.assertIn("ON CONFLICT", sql)
        self.assertNotIn("INSERT OR REPLACE", sql)


if __name__ == "__main__":
    unittest.main()
