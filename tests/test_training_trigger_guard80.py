from __future__ import annotations

import sqlite3
import unittest

from src.storage.training_trigger_guard80 import harden_training_trade_triggers_80


class TrainingTriggerGuard80Tests(unittest.TestCase):
    def _connection(self):
        con = sqlite3.connect(":memory:")
        con.executescript(
            """
            CREATE TABLE verify_active_run_72s (
                slot INTEGER PRIMARY KEY,
                run_id TEXT,
                build TEXT,
                activated_at TEXT
            );
            INSERT INTO verify_active_run_72s VALUES (1,'VERIFY-test','8.0','now');

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
        return con

    def test_purges_unknown_legacy_trigger_and_installs_canonical_pair(self):
        con = self._connection()
        con.executescript(
            """
            CREATE TRIGGER old_training_capture_legacy
            AFTER UPDATE ON paper_trades
            BEGIN
              INSERT INTO training_trades_72t(
                run_id,setup_id,build,symbol,timeframe,direction,status,updated_at
              )
              SELECT run_id,NEW.setup_id,'old',NEW.symbol,NEW.timeframe,NEW.direction,
                     NEW.status,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1;
            END;
            """
        )

        summary = harden_training_trade_triggers_80(con)
        names = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        self.assertGreaterEqual(summary["dropped"], 1)
        self.assertEqual(summary["installed"], 2)
        self.assertNotIn("old_training_capture_legacy", names)
        self.assertEqual(
            names,
            {"training_trade_insert_72t", "training_trade_update_72t"},
        )
        con.close()

    def test_repeated_updates_are_idempotent_after_legacy_cleanup(self):
        con = self._connection()
        harden_training_trade_triggers_80(con)
        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "t1", "GC", "5m", "bullish", "PENDING", None, None, None,
                None, 250.0, None, "2026-08-03T12:00:00+00:00",
            ),
        )
        for status in ("OPEN", "OPEN", "CLOSED", "CLOSED"):
            con.execute(
                "UPDATE paper_trades SET status=?,updated_at=? WHERE setup_id='t1'",
                (status, f"2026-08-03T12:0{1 if status == 'OPEN' else 5}:00+00:00"),
            )

        rows = con.execute(
            "SELECT run_id,setup_id,build,status FROM training_trades_72t"
        ).fetchall()
        self.assertEqual(rows, [("VERIFY-test", "t1", "8.0", "CLOSED")])
        con.close()

    def test_outer_paper_upsert_cannot_override_training_conflict_policy(self):
        """Mirror production upsert_paper_trade, which exposed SQLite policy inheritance."""
        con = self._connection()
        harden_training_trade_triggers_80(con)

        con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "t1", "GC", "5m", "bullish", "PENDING", None, None, None,
                None, 250.0, None, "2026-08-03T12:00:00+00:00",
            ),
        )

        # Production persists the same setup with INSERT ... ON CONFLICT DO UPDATE.
        # A trigger using INSERT OR IGNORE fails here because SQLite lets the outer
        # conflict policy win. The explicit trigger UPSERT must remain idempotent.
        for status in ("OPEN", "CLOSED"):
            con.execute(
                """
                INSERT INTO paper_trades(
                    setup_id,symbol,timeframe,direction,status,opened_at,closed_at,
                    result,result_r,risk_dollars,result_dollars,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(setup_id) DO UPDATE SET
                    status=excluded.status,
                    opened_at=excluded.opened_at,
                    closed_at=excluded.closed_at,
                    result=excluded.result,
                    result_r=excluded.result_r,
                    risk_dollars=excluded.risk_dollars,
                    result_dollars=excluded.result_dollars,
                    updated_at=excluded.updated_at
                """,
                (
                    "t1", "GC", "5m", "bullish", status,
                    "2026-08-03T12:01:00+00:00",
                    "2026-08-03T12:05:00+00:00" if status == "CLOSED" else None,
                    "WIN" if status == "CLOSED" else None,
                    2.0 if status == "CLOSED" else None,
                    250.0,
                    500.0 if status == "CLOSED" else None,
                    "2026-08-03T12:05:00+00:00",
                ),
            )

        rows = con.execute(
            "SELECT run_id,setup_id,build,status,result,result_r,result_dollars "
            "FROM training_trades_72t"
        ).fetchall()
        self.assertEqual(
            rows,
            [("VERIFY-test", "t1", "8.0", "CLOSED", "WIN", 2.0, 500.0)],
        )
        con.close()


if __name__ == "__main__":
    unittest.main()
