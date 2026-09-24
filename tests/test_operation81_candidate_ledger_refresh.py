from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from src.research.pipeline_recorder81 import record_pipeline_evaluation81
from src.research.run_archive81 import (
    archive_active_run81,
    archived_trades81,
    list_run_archives81,
)


def _engine_state(connection, run_id="run-81-testbaseline"):
    connection.execute(
        """
        CREATE TABLE engine_state(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "INSERT INTO engine_state VALUES (?,?,?)",
        ("operation81_research_run_id", run_id, "2026-09-24T00:00:00+00:00"),
    )
    connection.commit()


class PipelineRecorder81Tests(unittest.TestCase):
    def test_no_candidate_is_recorded_once_per_replay_candle(self):
        connection = sqlite3.connect(":memory:")
        _engine_state(connection)
        runtime = SimpleNamespace(
            strategy=SimpleNamespace(
                diagnostic=lambda *_args: {"stage": "WATCHING", "bias": "bullish"}
            )
        )
        candle = SimpleNamespace(
            close_time=datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc)
        )
        histories = {("GC", "5m"): [candle]}

        record_pipeline_evaluation81(
            connection, runtime, "GC", "5m", histories, [], [], source="CANDLE_CLOSE"
        )
        record_pipeline_evaluation81(
            connection, runtime, "GC", "5m", histories, [], [], source="CANDLE_CLOSE"
        )

        rows = connection.execute(
            """
            SELECT run_id,final_status,candidate_count,direction
            FROM training_evaluations_81
            """
        ).fetchall()
        self.assertEqual(
            rows,
            [("run-81-testbaseline", "NO_CANDIDATE", 0, "bullish")],
        )
        connection.close()

    def test_candidate_evaluation_records_candidate_metadata_without_trading_mutation(self):
        connection = sqlite3.connect(":memory:")
        _engine_state(connection)
        runtime = SimpleNamespace(
            strategy=SimpleNamespace(diagnostic=lambda *_args: {"stage": "SETUP_READY"})
        )
        candle = SimpleNamespace(
            close_time=datetime(2026, 9, 18, 15, 5, tzinfo=timezone.utc)
        )
        setup = SimpleNamespace(
            setup_id="setup-a",
            direction="bearish",
            status="QUALITY_BLOCKED",
            risk_reward=1.8,
            metadata={
                "strategy": "MSS_REVERSAL",
                "candidate_source_80": "CANDLE_CLOSE",
                "a_plus_context": {"quality_grade": "A"},
            },
        )

        record_pipeline_evaluation81(
            connection,
            runtime,
            "GC",
            "5m",
            {("GC", "5m"): [candle]},
            [setup],
            [setup],
        )

        row = connection.execute(
            """
            SELECT candidate_count,handled_count,final_status
            FROM training_evaluations_81
            """
        ).fetchone()
        self.assertEqual(row, (1, 1, "QUALITY_BLOCKED"))
        connection.close()


class RunArchive81Tests(unittest.TestCase):
    def _connection(self):
        connection = sqlite3.connect(":memory:")
        _engine_state(connection, "run-81-milestone")
        connection.executescript(
            """
            CREATE TABLE strategy_setups (
                setup_id TEXT PRIMARY KEY,
                symbol TEXT,
                status TEXT
            );
            CREATE TABLE paper_trades (
                setup_id TEXT PRIMARY KEY,
                symbol TEXT,
                status TEXT,
                result TEXT,
                result_dollars REAL
            );
            INSERT INTO strategy_setups VALUES
              ('w1','GC','CLOSED'),
              ('l1','GC','CLOSED'),
              ('p1','GC','PENDING');
            INSERT INTO paper_trades VALUES
              ('w1','GC','CLOSED','WIN',1500.0),
              ('l1','GC','CLOSED','LOSS',-500.0),
              ('p1','GC','PENDING',NULL,NULL);
            """
        )
        connection.commit()
        return connection

    def test_archive_preserves_trade_rows_and_summary(self):
        connection = self._connection()

        archive = archive_active_run81(
            connection,
            archive_key="fresh-ledger-test-v1",
            label="13k milestone",
        )

        self.assertEqual(archive["run_id"], "run-81-milestone")
        self.assertEqual(archive["trade_count"], 3)
        self.assertEqual(archive["closed_count"], 2)
        self.assertEqual(archive["wins"], 1)
        self.assertEqual(archive["losses"], 1)
        self.assertEqual(archive["net_pnl"], 1000.0)

        trades = archived_trades81(connection, archive["archive_id"])
        self.assertEqual({row["setup_id"] for row in trades}, {"w1", "l1", "p1"})
        listed = list_run_archives81(connection)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["label"], "13k milestone")
        connection.close()

    def test_archive_is_idempotent_for_same_run_and_reset_key(self):
        connection = self._connection()

        first = archive_active_run81(
            connection, archive_key="same-reset-token", label="first label"
        )
        second = archive_active_run81(
            connection, archive_key="same-reset-token", label="updated label"
        )

        self.assertEqual(first["archive_id"], second["archive_id"])
        self.assertEqual(len(list_run_archives81(connection)), 1)
        archived_rows = connection.execute(
            "SELECT COUNT(*) FROM otr_run_archive_rows_81 WHERE archive_id=?",
            (first["archive_id"],),
        ).fetchone()[0]
        self.assertEqual(archived_rows, 6)
        connection.close()


if __name__ == "__main__":
    unittest.main()
