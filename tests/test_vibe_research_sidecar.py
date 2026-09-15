from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.integrations.vibe_research.config import VibeResearchConfig, safe_vibe_environment
from src.integrations.vibe_research.worker import (
    ensure_vibe_research_schema,
    process_one_vibe_job,
    vibe_research_snapshot,
)


class VibeResearchSidecarTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
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
        self.tempdir = tempfile.TemporaryDirectory()
        self.workdir = Path(self.tempdir.name)

    def tearDown(self):
        self.connection.close()
        self.tempdir.cleanup()

    def add_closed_trade(self, setup_id: str = "gc-vibe-01") -> None:
        created = "2026-09-08T14:00:00+00:00"
        closed = "2026-09-08T14:30:00+00:00"
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
            (setup_id,"GC","5m","bullish",created,"liquidity_sweep",3500,3495,3507.5,1.5,"ACCEPTED",json.dumps(payload)),
        )
        self.connection.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (setup_id,"GC","5m","bullish","CLOSED",3500,3495,3507.5,created,closed,3507.5,"WIN",1.5,500,750,"",closed),
        )
        self.connection.execute(
            "INSERT INTO nautilus_shadow_parity VALUES (?,?,?,?,?,?)",
            (setup_id,closed,1,1,"[]","certified"),
        )
        self.connection.commit()

    def config(self, *, provider: str = "", model: str = "") -> VibeResearchConfig:
        return VibeResearchConfig(
            enabled=True,
            provider=provider,
            model=model,
            workdir=self.workdir,
            executable=Path("/bin/true"),
            poll_seconds=3.0,
            max_iter=12,
            timeout_seconds=60,
        )

    def test_schema_is_additive_and_never_mutates_trade_rows(self):
        self.add_closed_trade()
        before_setup = self.connection.execute("SELECT * FROM strategy_setups").fetchall()
        before_trade = self.connection.execute("SELECT * FROM paper_trades").fetchall()

        ensure_vibe_research_schema(self.connection)

        self.assertEqual(self.connection.execute("SELECT * FROM strategy_setups").fetchall(), before_setup)
        self.assertEqual(self.connection.execute("SELECT * FROM paper_trades").fetchall(), before_trade)

    def test_closed_trade_packet_is_captured_while_provider_is_absent(self):
        self.add_closed_trade()
        before_setup = self.connection.execute("SELECT * FROM strategy_setups").fetchall()
        before_trade = self.connection.execute("SELECT * FROM paper_trades").fetchall()

        result = process_one_vibe_job(self.connection, config=self.config())

        self.assertEqual(result["status"], "WAITING_PROVIDER")
        job = self.connection.execute(
            "SELECT status,packet_path FROM vibe_research_jobs_v02 WHERE setup_id='gc-vibe-01'"
        ).fetchone()
        self.assertEqual(job[0], "WAITING_PROVIDER")
        self.assertTrue(Path(job[1]).exists())
        self.assertEqual(self.connection.execute("SELECT * FROM strategy_setups").fetchall(), before_setup)
        self.assertEqual(self.connection.execute("SELECT * FROM paper_trades").fetchall(), before_trade)

    def test_waiting_historical_packet_is_not_auto_backfilled(self):
        self.add_closed_trade()
        first = process_one_vibe_job(self.connection, config=self.config())
        self.assertEqual(first["status"], "WAITING_PROVIDER")

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "provider-secret",
                "OTR_VIBE_BACKFILL_WAITING": "0",
            },
            clear=False,
        ):
            second = process_one_vibe_job(
                self.connection,
                config=self.config(provider="openai", model="research-model"),
            )

        self.assertIsNone(second)
        status = self.connection.execute(
            "SELECT status FROM vibe_research_jobs_v02 WHERE setup_id='gc-vibe-01'"
        ).fetchone()[0]
        self.assertEqual(status, "WAITING_PROVIDER")

    def test_vibe_process_environment_does_not_receive_otr_secrets(self):
        config = self.config(provider="openai", model="research-model")
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "provider-secret",
                "OTR_BRIDGE_KEY": "never-pass-this",
                "DASHBOARD_PASSWORD": "never-pass-this-either",
                "LANGCHAIN_USE_RESPONSES_API": "true",
                "LANGCHAIN_REASONING_EFFORT": "medium",
            },
            clear=False,
        ):
            env = safe_vibe_environment(config)

        self.assertEqual(env["OPENAI_API_KEY"], "provider-secret")
        self.assertEqual(env["LANGCHAIN_USE_RESPONSES_API"], "true")
        self.assertEqual(env["LANGCHAIN_REASONING_EFFORT"], "medium")
        self.assertNotIn("OTR_BRIDGE_KEY", env)
        self.assertNotIn("DASHBOARD_PASSWORD", env)
        self.assertEqual(env["VIBE_TRADING_ENABLE_SHELL_TOOLS"], "0")

    def test_snapshot_declares_zero_trading_authority(self):
        ensure_vibe_research_schema(self.connection)
        with patch.dict(os.environ, {"OTR_VIBE_RESEARCH": "0"}, clear=False):
            snapshot = vibe_research_snapshot(self.connection)
        self.assertFalse(snapshot["authoritative"])
        self.assertFalse(snapshot["strategy_mutation_allowed"])
        self.assertFalse(snapshot["risk_mutation_allowed"])
        self.assertFalse(snapshot["broker_access_allowed"])


if __name__ == "__main__":
    unittest.main()
