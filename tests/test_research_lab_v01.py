from __future__ import annotations

import json
import sqlite3
import unittest

from src.research.lab_v01 import (
    MIN_EVIDENCE_SAMPLES,
    closed_gold_trades,
    counterfactual_candidates,
    ensure_research_lab81,
    evidence_metrics,
    refresh_research_lab81,
    research_lab_snapshot81,
)


class ResearchLabV01Tests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(
            """
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
            CREATE TABLE nautilus_shadow_parity (
                setup_id TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                matched_trade_path INTEGER NOT NULL,
                matched_full INTEGER NOT NULL,
                difference_categories_json TEXT NOT NULL,
                note TEXT
            );
            """
        )

    def tearDown(self):
        self.connection.close()

    def add_trade(
        self,
        number: int,
        *,
        result: str = "WIN",
        result_r: float = 1.5,
        result_dollars: float = 750.0,
        grade: str = "A+",
        entry_type: str = "FVG_MIDPOINT",
        regime: str = "TREND_EXPANSION",
        parity: bool = True,
    ) -> str:
        setup_id = f"gc-lab-{number:02d}"
        created = f"2026-09-0{1 + (number % 6)}T13:{number % 60:02d}:00+00:00"
        closed = f"2026-09-0{1 + (number % 6)}T13:{number % 60:02d}:30+00:00"
        payload = {
            "direction": "bullish",
            "displacement": {"low": 3490.0, "high": 3510.0},
            "entry_fvg": {"lower": 3498.0, "upper": 3502.0},
            "metadata": {
                "strategy": "ICT_CONFLUENCE",
                "entry_type": entry_type,
                "checklist_score": 6 if grade == "A+" else 5,
                "checklist_total": 6,
                "a_plus_context": {"quality_grade": grade},
                "gold_regime_80": {"regime": regime, "direction": "bullish"},
            },
        }
        self.connection.execute(
            """
            INSERT INTO strategy_setups(
                setup_id,symbol,timeframe,direction,created_at,trigger_type,
                entry_price,stop_price,target_price,risk_reward,status,payload_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                setup_id,
                "GC",
                "5m",
                "bullish",
                created,
                "liquidity_sweep",
                3500.0,
                3495.0,
                3507.5,
                1.5,
                "ACCEPTED",
                json.dumps(payload),
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
                setup_id,
                "GC",
                "5m",
                "bullish",
                "CLOSED",
                3500.0,
                3495.0,
                3507.5,
                created,
                closed,
                3507.5 if result == "WIN" else 3495.0,
                result,
                result_r,
                500.0,
                result_dollars,
                "",
                closed,
            ),
        )
        if parity:
            self.connection.execute(
                "INSERT INTO nautilus_shadow_parity VALUES (?,?,?,?,?,?)",
                (setup_id, closed, 1, 1, "[]", "test parity"),
            )
        self.connection.commit()
        return setup_id

    def test_schema_is_additive_and_hypotheses_are_seeded(self):
        self.add_trade(1)
        setup_count = self.connection.execute("SELECT COUNT(*) FROM strategy_setups").fetchone()[0]
        trade_count = self.connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]

        ensure_research_lab81(self.connection)

        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM strategy_setups").fetchone()[0], setup_count)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0], trade_count)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM research_hypotheses_v01").fetchone()[0], 3)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM research_evidence_v01").fetchone()[0], 0)

    def test_evidence_remains_insufficient_before_twenty_resolved_samples(self):
        self.add_trade(1, result="WIN", result_r=1.5, result_dollars=750.0)
        self.add_trade(2, result="LOSS", result_r=-1.0, result_dollars=-500.0)
        trades = closed_gold_trades(self.connection, limit=10)
        metrics = evidence_metrics(trades)

        self.assertEqual(metrics["samples"], 2)
        self.assertEqual(metrics["wins"], 1)
        self.assertEqual(metrics["losses"], 1)
        self.assertEqual(metrics["net_pnl"], 250.0)
        self.assertEqual(metrics["expectancy_r"], 0.25)
        self.assertEqual(metrics["evidence_status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(metrics["minimum_samples"], MIN_EVIDENCE_SAMPLES)

    def test_twenty_execution_certified_samples_can_become_evidence_ready(self):
        for number in range(20):
            if number % 2:
                self.add_trade(number, result="LOSS", result_r=-1.0, result_dollars=-500.0)
            else:
                self.add_trade(number, result="WIN", result_r=1.5, result_dollars=750.0)
        snapshot = research_lab_snapshot81(self.connection)
        baseline = snapshot["baseline"]

        self.assertEqual(baseline["samples"], 20)
        self.assertEqual(baseline["parity_samples"], 20)
        self.assertEqual(baseline["parity_path_matches"], 20)
        self.assertEqual(baseline["evidence_status"], "EVIDENCE_READY")
        self.assertFalse(snapshot["authoritative"])
        self.assertFalse(snapshot["strategy_mutation_allowed"])
        self.assertFalse(snapshot["broker_actions_allowed"])

    def test_trade_projection_exposes_nautilus_and_setup_dimensions(self):
        setup_id = self.add_trade(1, grade="A+", entry_type="EARLY_OTE_79")
        trade = closed_gold_trades(self.connection, limit=1)[0]

        self.assertEqual(trade["setup_id"], setup_id)
        self.assertEqual(trade["grade"], "A+")
        self.assertEqual(trade["setup_family"], "OTE")
        self.assertEqual(trade["regime"], "TREND_EXPANSION")
        self.assertEqual(trade["execution_certification"], "MATCH")

    def test_counterfactuals_are_geometry_only_and_never_invent_outcomes(self):
        self.add_trade(1)
        trade = closed_gold_trades(self.connection, limit=1)[0]
        candidates = counterfactual_candidates(trade)
        by_variant = {item["variant"]: item for item in candidates}

        self.assertIn("ACTUAL", by_variant)
        self.assertIn("FVG_50", by_variant)
        self.assertIn("OTE_70_5", by_variant)
        self.assertIn("OTE_79", by_variant)
        self.assertEqual(by_variant["OTE_79"]["evaluability"], "GEOMETRY_ONLY")
        self.assertNotIn("result", by_variant["OTE_79"])
        self.assertNotIn("would_win", by_variant["OTE_79"])

    def test_refresh_writes_lab_tables_without_mutating_trade_state(self):
        self.add_trade(1)
        before_setup = self.connection.execute("SELECT * FROM strategy_setups").fetchall()
        before_trade = self.connection.execute("SELECT * FROM paper_trades").fetchall()

        result = refresh_research_lab81(self.connection)

        self.assertEqual(result["strategy_mutations"], 0)
        self.assertEqual(result["broker_actions"], 0)
        self.assertEqual(self.connection.execute("SELECT * FROM strategy_setups").fetchall(), before_setup)
        self.assertEqual(self.connection.execute("SELECT * FROM paper_trades").fetchall(), before_trade)
        self.assertGreater(self.connection.execute("SELECT COUNT(*) FROM research_evidence_v01").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM research_counterfactuals_v01").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
