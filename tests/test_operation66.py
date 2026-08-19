from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.risk.evaluation import EvaluationConfig, EvaluationRiskGuard, _session_bucket


ROOT = Path(__file__).resolve().parents[1]


def _paper_db():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY,
            status TEXT,
            opened_at TEXT,
            closed_at TEXT,
            result TEXT,
            result_r REAL,
            risk_dollars REAL,
            result_dollars REAL,
            updated_at TEXT
        )
        """
    )
    return connection


class Operation66Tests(unittest.TestCase):
    def test_all_execution_timeframes_receive_intrabar_acceleration(self):
        code = textwrap.dedent(
            r'''
            from types import SimpleNamespace
            import src.main_66 as op66

            assert op66.op65.INTRABAR_TIMEFRAMES == {"1m", "5m", "15m", "1h"}

            decision = SimpleNamespace(
                risk_dollars=300.0,
                snapshot={"session_profit_remaining": 150.0},
            )
            setup = SimpleNamespace(
                risk_reward=3.0,
                metadata={"risk_multiplier": 1.0},
            )
            risk, multiplier = op66._setup_risk_66(decision, setup)
            assert risk == 50.0
            assert round(multiplier, 6) == round(50.0 / 300.0, 6)
            assert setup.metadata["fast_eval_66"]["risk_capped_to_session"] is True
            print("operation66-fast-eval-ok")
            '''
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        self.assertIn("operation66-fast-eval-ok", completed.stdout)

    def test_session_buckets_are_non_overlapping(self):
        asia = _session_bucket(datetime(2026, 8, 19, 22, 30, tzinfo=timezone.utc))  # 18:30 ET
        tokyo = _session_bucket(datetime(2026, 8, 20, 2, 30, tzinfo=timezone.utc))  # 22:30 ET
        london = _session_bucket(datetime(2026, 8, 20, 7, 0, tzinfo=timezone.utc))  # 03:00 ET
        ny = _session_bucket(datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc))  # 10:00 ET
        self.assertEqual(asia["name"], "ASIA")
        self.assertEqual(tokyo["name"], "TOKYO")
        self.assertEqual(london["name"], "LONDON")
        self.assertEqual(ny["name"], "NEW_YORK")

    def test_session_profit_cap_locks_only_current_bucket(self):
        connection = _paper_db()
        config = EvaluationConfig(
            risk_per_trade=300.0,
            min_risk_per_trade=100.0,
            internal_daily_stop=900.0,
            max_trades_per_day=8,
            max_consecutive_losses=2,
            session_profit_cap=1500.0,
            continue_after_target=False,
        )
        guard = EvaluationRiskGuard(config)

        # Two New York-session wins total exactly $1,500 while the $3,000 eval
        # target remains incomplete. The current session should bank and stop.
        rows = [
            ("a", "CLOSED", "2026-08-19T13:00:00+00:00", "2026-08-19T13:30:00+00:00", "WIN", 2.0, 300.0, 800.0, "2026-08-19T13:30:00+00:00"),
            ("b", "CLOSED", "2026-08-19T14:00:00+00:00", "2026-08-19T14:30:00+00:00", "WIN", 2.0, 300.0, 700.0, "2026-08-19T14:30:00+00:00"),
        ]
        connection.executemany(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()

        snap = guard.snapshot(connection, datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(snap["session"]["name"], "NEW_YORK")
        self.assertEqual(snap["session_pnl"], 1500.0)
        self.assertEqual(snap["session_profit_remaining"], 0.0)
        self.assertEqual(snap["status"], "SESSION_PROFIT_LOCK")
        self.assertFalse(guard.decide(connection, datetime(2026, 8, 19, 15, 0, tzinfo=timezone.utc)).allowed)

        # A later Asia bucket is fresh and eligible again because the overall
        # evaluation target has not been reached yet.
        later = guard.snapshot(connection, datetime(2026, 8, 19, 22, 30, tzinfo=timezone.utc))
        self.assertEqual(later["session"]["name"], "ASIA")
        self.assertEqual(later["session_pnl"], 0.0)
        self.assertEqual(later["status"], "ACTIVE")

    def test_runtime_manifest_names_operation_66(self):
        server = (ROOT / "src/dashboard/server.py").read_text()
        self.assertIn('"operation": "Operation 6.6"', server)
        self.assertIn('"src.main_66"', server)
        self.assertIn("1m / 5m / 15m / 1h", server)
        self.assertIn("EVAL_SESSION_PROFIT_CAP", server)


if __name__ == "__main__":
    unittest.main()
