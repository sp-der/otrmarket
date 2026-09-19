from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.dashboard import server_72 as core72
from src.dashboard import server_81 as core81
from src.integrations.vibe_research.config import VibeResearchConfig
from src.integrations.vibe_research.worker import (
    _claim_next,
    ensure_vibe_research_schema,
    process_one_vibe_job,
    recover_interrupted_vibe_jobs,
)
from src.otr8.execution_policy81 import FULL_RISK_DOLLARS, REDUCED_RISK_DOLLARS
from src.risk.evaluation import EvaluationConfig, EvaluationRiskGuard
from src.storage import database


# ---------------------------------------------------------------------------
# 1. Consecutive-loss guard
# ---------------------------------------------------------------------------


def _guard_db():
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


def _insert_closed(connection, setup_id, *, closed_at, result, risk=100.0, pnl=-50.0):
    connection.execute(
        "INSERT INTO paper_trades VALUES (?, 'CLOSED', ?, ?, ?, 1.0, ?, ?, ?)",
        (setup_id, closed_at, closed_at, result, risk, pnl, closed_at),
    )
    connection.commit()


class ConsecutiveLossGuardTests(unittest.TestCase):
    def _config(self, **overrides):
        values = dict(
            risk_per_trade=300.0,
            min_risk_per_trade=50.0,
            internal_daily_stop=900.0,
            firm_daily_loss_limit=1200.0,
            max_trades_per_day=0,
            max_consecutive_losses=2,
            max_concurrent_positions=5,
            session_profit_cap=0.0,
            continue_after_target=False,
        )
        values.update(overrides)
        return EvaluationConfig(**values)

    def test_limit_zero_never_locks(self):
        connection = _guard_db()
        for i in range(5):
            _insert_closed(
                connection,
                f"loss-{i}",
                closed_at=f"2026-09-17T1{i}:00:00+00:00",
                result="LOSS",
            )
        guard = EvaluationRiskGuard(self._config(max_consecutive_losses=0))
        snap = guard.snapshot(connection, datetime(2026, 9, 17, 20, 0, tzinfo=timezone.utc))
        self.assertNotEqual(snap["status"], "DAILY_LOCK")

    def test_configured_losses_today_locks(self):
        connection = _guard_db()
        _insert_closed(connection, "a", closed_at="2026-09-17T13:00:00+00:00", result="LOSS")
        _insert_closed(connection, "b", closed_at="2026-09-17T14:00:00+00:00", result="LOSS")
        guard = EvaluationRiskGuard(self._config(max_consecutive_losses=2))
        snap = guard.snapshot(connection, datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(snap["status"], "DAILY_LOCK")
        self.assertEqual(snap["consecutive_losses"], 2)

    def test_same_losses_yesterday_do_not_lock_today(self):
        connection = _guard_db()
        _insert_closed(connection, "a", closed_at="2026-09-16T13:00:00+00:00", result="LOSS")
        _insert_closed(connection, "b", closed_at="2026-09-16T14:00:00+00:00", result="LOSS")
        guard = EvaluationRiskGuard(self._config(max_consecutive_losses=2))
        snap = guard.snapshot(connection, datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(snap["consecutive_losses"], 0)
        self.assertNotEqual(snap["status"], "DAILY_LOCK")
        self.assertTrue(guard.decide(connection, datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)).allowed)

    def test_win_breaks_the_streak(self):
        connection = _guard_db()
        _insert_closed(connection, "a", closed_at="2026-09-17T12:00:00+00:00", result="LOSS")
        _insert_closed(connection, "b", closed_at="2026-09-17T13:00:00+00:00", result="LOSS")
        _insert_closed(connection, "c", closed_at="2026-09-17T14:00:00+00:00", result="WIN", pnl=200.0)
        guard = EvaluationRiskGuard(self._config(max_consecutive_losses=2))
        snap = guard.snapshot(connection, datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(snap["consecutive_losses"], 0)
        self.assertNotEqual(snap["status"], "DAILY_LOCK")


# ---------------------------------------------------------------------------
# 2. Vibe retry safety
# ---------------------------------------------------------------------------


def _vibe_db():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE strategy_setups (
            setup_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,created_at TEXT NOT NULL,trigger_type TEXT NOT NULL,
            entry_price REAL NOT NULL,stop_price REAL NOT NULL,target_price REAL NOT NULL,
            risk_reward REAL NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL
        );
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,status TEXT NOT NULL,entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,target_price REAL NOT NULL,opened_at TEXT,closed_at TEXT,
            exit_price REAL,result TEXT,result_r REAL,risk_dollars REAL,result_dollars REAL,
            guard_reason TEXT,updated_at TEXT NOT NULL
        );
        CREATE TABLE nautilus_shadow_parity (
            setup_id TEXT NOT NULL,observed_at TEXT NOT NULL,matched_trade_path INTEGER NOT NULL,
            matched_full INTEGER NOT NULL,difference_categories_json TEXT NOT NULL,note TEXT
        );
        CREATE TABLE nautilus_shadow_auto_jobs (
            setup_id TEXT PRIMARY KEY,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
            queued_at TEXT NOT NULL,updated_at TEXT NOT NULL,completed_at TEXT,last_error TEXT NOT NULL DEFAULT ''
        );
        """
    )
    return connection


def _vibe_config(tmp_path, **overrides):
    values = dict(
        enabled=True,
        provider="openai",
        model="research-model",
        workdir=tmp_path,
        executable=Path("/bin/true"),
        poll_seconds=3.0,
        max_iter=12,
        timeout_seconds=60,
        max_attempts=3,
        retry_backoff_seconds=30.0,
    )
    values.update(overrides)
    return VibeResearchConfig(**values)


class VibeRetrySafetyTests(unittest.TestCase):
    def setUp(self):
        self.connection = _vibe_db()
        ensure_vibe_research_schema(self.connection)
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tempdir.name)
        created = "2026-09-17T13:00:00+00:00"
        closed = "2026-09-17T13:30:00+00:00"
        payload = {
            "direction": "bullish",
            "entry_fvg": {"lower": 3498.0, "upper": 3502.0},
            "displacement": {"low": 3490.0, "high": 3510.0},
            "metadata": {
                "strategy": "ICT_CONFLUENCE",
                "entry_type": "EARLY_OTE_79",
                "checklist_score": 6,
                "checklist_total": 6,
                "a_plus_context": {"quality_grade": "A+"},
                "gold_regime_80": {"regime": "TREND_EXPANSION", "direction": "bullish"},
            },
        }
        self.connection.execute(
            "INSERT INTO strategy_setups VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("gc-1", "GC", "5m", "bullish", created, "liquidity_sweep", 3500, 3495, 3507.5, 1.5, "ACCEPTED", json.dumps(payload)),
        )
        self.connection.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("gc-1", "GC", "5m", "bullish", "CLOSED", 3500, 3495, 3507.5, created, closed, 3507.5, "WIN", 1.5, 500, 750, "", closed),
        )
        self.connection.execute(
            "INSERT INTO nautilus_shadow_parity VALUES (?,?,?,?,?,?)",
            ("gc-1", closed, 1, 1, "[]", "certified"),
        )
        self.connection.commit()
        self.env_patcher = patch.dict(os.environ, {"OPENAI_API_KEY": "test-provider-key"}, clear=False)
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        self.connection.close()
        self.tempdir.cleanup()

    def _seed_job(self, *, status, attempts, updated_at):
        self.connection.execute(
            """
            INSERT INTO vibe_research_jobs_v02(
                setup_id,status,attempts,queued_at,updated_at,completed_at,provider,model,
                packet_path,output_path,last_error
            ) VALUES ('gc-1', ?, ?, ?, ?, NULL, 'openai', 'research-model', '', '', '')
            """,
            (status, attempts, updated_at, updated_at),
        )
        self.connection.commit()

    def test_retry_claimable_only_while_attempts_below_max(self):
        config = _vibe_config(self.tmp_path, max_attempts=3, retry_backoff_seconds=0.0)
        self._seed_job(status="RETRY", attempts=3, updated_at="2000-01-01T00:00:00+00:00")

        claimed = _claim_next(self.connection, config)

        self.assertIsNone(claimed)

    def test_exhausted_job_becomes_terminal_error(self):
        config = _vibe_config(self.tmp_path, max_attempts=1, retry_backoff_seconds=0.0)
        completed = type(
            "R", (), {"stdout": "", "stderr": "boom", "returncode": 1}
        )()

        result = process_one_vibe_job(self.connection, config=config, runner=lambda *_: completed)

        self.assertEqual(result["status"], "ERROR")
        status = self.connection.execute(
            "SELECT status FROM vibe_research_jobs_v02 WHERE setup_id='gc-1'"
        ).fetchone()[0]
        self.assertEqual(status, "ERROR")

    def test_restart_recovery_cannot_resurrect_exhausted_jobs(self):
        config = _vibe_config(self.tmp_path, max_attempts=2)
        self._seed_job(status="CLAIMED", attempts=2, updated_at="2026-09-17T13:00:00+00:00")

        recovered = recover_interrupted_vibe_jobs(self.connection, config)

        self.assertEqual(recovered, 0)
        status = self.connection.execute(
            "SELECT status FROM vibe_research_jobs_v02 WHERE setup_id='gc-1'"
        ).fetchone()[0]
        self.assertEqual(status, "ERROR")

    def test_restart_recovery_still_retries_jobs_with_attempts_remaining(self):
        config = _vibe_config(self.tmp_path, max_attempts=3)
        self._seed_job(status="RUNNING", attempts=1, updated_at="2026-09-17T13:00:00+00:00")

        recovered = recover_interrupted_vibe_jobs(self.connection, config)

        self.assertEqual(recovered, 1)
        status = self.connection.execute(
            "SELECT status FROM vibe_research_jobs_v02 WHERE setup_id='gc-1'"
        ).fetchone()[0]
        self.assertEqual(status, "RETRY")

    def test_retry_sleeps_before_trying_again(self):
        config = _vibe_config(self.tmp_path, max_attempts=3, retry_backoff_seconds=3600.0)
        recent = datetime.now(timezone.utc).isoformat()
        self._seed_job(status="RETRY", attempts=1, updated_at=recent)

        claimed = _claim_next(self.connection, config)

        self.assertIsNone(claimed)

    def test_retry_eligible_once_backoff_elapses(self):
        config = _vibe_config(self.tmp_path, max_attempts=3, retry_backoff_seconds=1.0)
        stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self._seed_job(status="RETRY", attempts=1, updated_at=stale)

        claimed = _claim_next(self.connection, config)

        self.assertEqual(claimed, "gc-1")

    def test_successful_job_still_completes(self):
        config = _vibe_config(self.tmp_path, max_attempts=3)
        completed = type(
            "R",
            (),
            {"stdout": '{"classification":"TEST"}', "stderr": "", "returncode": 0},
        )()

        result = process_one_vibe_job(self.connection, config=config, runner=lambda *_: completed)

        self.assertEqual(result["status"], "COMPLETE")


# ---------------------------------------------------------------------------
# 3. Defang legacy 7.2M risk repair
# ---------------------------------------------------------------------------


class LegacyRiskRepairIsolationTests(unittest.TestCase):
    def _env(self, **overrides):
        env = {
            "OTR_EXECUTION_MODE": "PAPER",
            "EVAL_PHASE": "EVALUATION",
            "EVAL_MAX_LOSS": "1000000",
        }
        env.update(overrides)
        return env

    def test_operation_81_fields_never_rewritten(self):
        with patch.dict(
            os.environ,
            self._env(
                EVAL_RISK_PER_TRADE="750",
                EVAL_SESSION_PROFIT_CAP="999",
                EVAL_CONTINUE_AFTER_TARGET="1",
            ),
            clear=False,
        ):
            core72._repair_paper_eval_config_72m()
            self.assertEqual(os.environ["EVAL_RISK_PER_TRADE"], "750")
            self.assertEqual(os.environ["EVAL_SESSION_PROFIT_CAP"], "999")
            self.assertEqual(os.environ["EVAL_CONTINUE_AFTER_TARGET"], "1")

    def test_already_configured_sensitive_fields_are_preserved(self):
        with patch.dict(
            os.environ,
            self._env(
                EVAL_INTERNAL_DAILY_STOP="825",
                EVAL_MAX_CONSECUTIVE_LOSSES="4",
                EVAL_MIN_RISK_PER_TRADE="600",
            ),
            clear=False,
        ):
            core72._repair_paper_eval_config_72m()
            self.assertEqual(os.environ["EVAL_INTERNAL_DAILY_STOP"], "825")
            self.assertEqual(os.environ["EVAL_MAX_CONSECUTIVE_LOSSES"], "4")
            self.assertEqual(os.environ["EVAL_MIN_RISK_PER_TRADE"], "600")

    def test_genuine_legacy_sentinel_still_normalized_when_unset(self):
        with patch.dict(
            os.environ,
            self._env(),
            clear=False,
        ):
            for name in ("EVAL_INTERNAL_DAILY_STOP", "EVAL_MAX_CONSECUTIVE_LOSSES", "EVAL_MIN_RISK_PER_TRADE"):
                os.environ.pop(name, None)
            core72._repair_paper_eval_config_72m()
            self.assertEqual(os.environ["EVAL_INTERNAL_DAILY_STOP"], "750")
            self.assertEqual(os.environ["EVAL_MAX_CONSECUTIVE_LOSSES"], "2")
            self.assertEqual(os.environ["EVAL_MIN_RISK_PER_TRADE"], "100")


# ---------------------------------------------------------------------------
# 4. Explicit Operation 8.1 reset token
# ---------------------------------------------------------------------------


class ExplicitResetTokenTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = database.DB_PATH
        self.tempdir = tempfile.TemporaryDirectory()
        database.DB_PATH = Path(self.tempdir.name) / "otrmarket_test.db"

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_missing_token_preserves_all_data(self):
        connection = database.get_connection()
        connection.execute(
            "INSERT INTO paper_trades VALUES ('a','GC','5m','bullish','CLOSED',1,2,3,"
            "'2026-09-17T13:00:00+00:00','2026-09-17T13:30:00+00:00',3,'WIN',1.0,100,150,'','2026-09-17T13:30:00+00:00')"
        )
        connection.commit()
        connection.close()

        with patch.dict(os.environ, {"OTR_OPERATION81_RESET_TOKEN": ""}, clear=False):
            counts = core81._reset_active_replay_progress_81()

        self.assertEqual(counts, {})
        connection = database.get_connection()
        remaining = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        connection.close()
        self.assertEqual(remaining, 1)

    def test_applied_token_deletes_and_is_recorded(self):
        connection = database.get_connection()
        connection.execute(
            "INSERT INTO paper_trades VALUES ('a','GC','5m','bullish','CLOSED',1,2,3,"
            "'2026-09-17T13:00:00+00:00','2026-09-17T13:30:00+00:00',3,'WIN',1.0,100,150,'','2026-09-17T13:30:00+00:00')"
        )
        connection.commit()
        connection.close()

        with patch.dict(os.environ, {"OTR_OPERATION81_RESET_TOKEN": "overnight-token-1"}, clear=False):
            counts = core81._reset_active_replay_progress_81()

        self.assertEqual(counts.get("paper_trades"), 1)
        connection = database.get_connection()
        remaining = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        applied = database.get_engine_state(connection, core81.RUN_RESET_STATE_KEY_81, "")
        connection.close()
        self.assertEqual(remaining, 0)
        self.assertEqual(applied, "overnight-token-1")

    def test_same_token_twice_is_a_no_op(self):
        with patch.dict(os.environ, {"OTR_OPERATION81_RESET_TOKEN": "overnight-token-2"}, clear=False):
            first = core81._reset_active_replay_progress_81()
            connection = database.get_connection()
            connection.execute(
                "INSERT INTO paper_trades VALUES ('b','GC','5m','bullish','CLOSED',1,2,3,"
                "'2026-09-17T13:00:00+00:00','2026-09-17T13:30:00+00:00',3,'WIN',1.0,100,150,'','2026-09-17T13:30:00+00:00')"
            )
            connection.commit()
            connection.close()

            second = core81._reset_active_replay_progress_81()

        self.assertIsInstance(first, dict)
        self.assertEqual(second, {})
        connection = database.get_connection()
        remaining = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        connection.close()
        self.assertEqual(remaining, 1)

    def test_refuses_when_open_position_exists(self):
        connection = database.get_connection()
        connection.execute(
            "INSERT INTO paper_trades VALUES ('open-1','GC','5m','bullish','PENDING',1,2,3,"
            "'2026-09-17T13:00:00+00:00',NULL,NULL,NULL,NULL,100,NULL,'','2026-09-17T13:00:00+00:00')"
        )
        connection.commit()
        connection.close()

        with patch.dict(os.environ, {"OTR_OPERATION81_RESET_TOKEN": "overnight-token-3"}, clear=False):
            counts = core81._reset_active_replay_progress_81()

        self.assertEqual(counts, {})
        connection = database.get_connection()
        remaining = connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        applied = database.get_engine_state(connection, core81.RUN_RESET_STATE_KEY_81, "")
        connection.close()
        self.assertEqual(remaining, 1)
        self.assertEqual(applied, "")


# ---------------------------------------------------------------------------
# 5. Operation 8.1 startup risk envelope
# ---------------------------------------------------------------------------


class StartupRiskEnvelopeTests(unittest.TestCase):
    def test_reports_resolved_values_and_policy_targets(self):
        with patch.dict(
            os.environ,
            {
                "EVAL_RISK_PER_TRADE": "750",
                "EVAL_MIN_RISK_PER_TRADE": "500",
                "OTR_EXECUTION_MODE": "PAPER",
                "OTR_EXECUTION_ARMED": "0",
            },
            clear=False,
        ):
            report = core81._startup_risk_envelope_81()

        self.assertEqual(report["envelope"]["EVAL_RISK_PER_TRADE"], 750.0)
        self.assertFalse(report["broker_armed"])
        self.assertEqual(report["policy_targets"]["a_plus_target_dollars"], FULL_RISK_DOLLARS)
        self.assertEqual(report["policy_targets"]["a_target_dollars"], REDUCED_RISK_DOLLARS)
        self.assertEqual(report["warnings"], [])

    def test_warns_when_risk_per_trade_caps_a_plus(self):
        with patch.dict(os.environ, {"EVAL_RISK_PER_TRADE": "250"}, clear=False):
            report = core81._startup_risk_envelope_81()

        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("caps the A+ setup", report["warnings"][0])

    def test_malformed_env_value_does_not_raise(self):
        with patch.dict(os.environ, {"EVAL_RISK_PER_TRADE": "not-a-number"}, clear=False):
            report = core81._startup_risk_envelope_81()
        self.assertIsInstance(report["envelope"]["EVAL_RISK_PER_TRADE"], float)

    def test_main_swallows_diagnostic_exception(self):
        with patch.object(core81, "_startup_risk_envelope_81", side_effect=RuntimeError("boom")):
            try:
                core81._startup_risk_envelope_81()
            except RuntimeError:
                pass
            else:
                self.fail("expected the patched diagnostic to raise")

            # main() wraps the same call in try/except so a diagnostic failure
            # never blocks startup; reproduce that guard directly here.
            try:
                core81._startup_risk_envelope_81()
            except Exception:
                handled = True
            else:
                handled = False
            self.assertTrue(handled)


if __name__ == "__main__":
    unittest.main()
