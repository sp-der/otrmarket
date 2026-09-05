from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from src.research.conversion_funnel81 import conversion_funnel81


ROOT = Path(__file__).resolve().parents[1]


def trace(stages, final_status):
    return json.dumps({"stages": stages, "final_status": final_status})


class ConversionFunnel81Tests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(
            """
            CREATE TABLE decision_traces_80(
                setup_id TEXT PRIMARY KEY,
                symbol TEXT,
                timeframe TEXT,
                strategy TEXT,
                direction TEXT,
                source TEXT,
                final_status TEXT,
                trace_json TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE paper_trades(
                setup_id TEXT PRIMARY KEY,
                symbol TEXT,
                timeframe TEXT,
                direction TEXT,
                status TEXT,
                entry_price REAL,
                stop_price REAL,
                target_price REAL,
                opened_at TEXT,
                closed_at TEXT,
                exit_price REAL,
                result TEXT,
                result_r REAL,
                risk_dollars REAL,
                result_dollars REAL,
                guard_reason TEXT,
                updated_at TEXT
            );
            """
        )

        rows = [
            (
                "qblock", "GC", "5m", "ICT_CONFLUENCE", "bullish", "CANDLE_CLOSE",
                "QUALITY_BLOCKED",
                trace([
                    {"stage": "SESSION", "outcome": "PASSED", "reason": "session ok"},
                    {"stage": "QUALITY", "outcome": "BLOCKED", "reason": "A setup offers only 1.18R; require 1.30R"},
                ], "QUALITY_BLOCKED"),
                "2026-08-04T13:00:00+00:00", "2026-08-04T13:00:00+00:00",
            ),
            (
                "arbiter", "GC", "15m", "ICT_CONFLUENCE", "bullish", "CANDLE_CLOSE",
                "ARBITER_BLOCKED",
                trace([
                    {"stage": "QUALITY", "outcome": "PASSED", "reason": "A quality passed"},
                    {"stage": "ARBITER", "outcome": "BLOCKED", "reason": "stronger candidate selected"},
                ], "ARBITER_BLOCKED"),
                "2026-08-04T13:01:00+00:00", "2026-08-04T13:01:00+00:00",
            ),
            (
                "missed", "GC", "5m", "MSS_REVERSAL", "bearish", "CANDLE_CLOSE",
                "PENDING",
                trace([
                    {"stage": "QUALITY", "outcome": "PASSED", "reason": "A+ quality passed"},
                    {"stage": "ARBITER", "outcome": "SELECTED", "reason": "best candidate"},
                    {"stage": "EXECUTION_HANDOFF", "outcome": "ACCEPTED", "reason": "registered pending"},
                ], "PENDING"),
                "2026-08-04T13:02:00+00:00", "2026-08-04T13:02:00+00:00",
            ),
            (
                "winner", "GC", "1m", "ICT_CONFLUENCE", "bullish", "INTRABAR",
                "PENDING",
                trace([
                    {"stage": "QUALITY", "outcome": "PASSED", "reason": "A+ quality passed"},
                    {"stage": "ARBITER", "outcome": "SELECTED", "reason": "best candidate"},
                    {"stage": "EXECUTION_HANDOFF", "outcome": "ACCEPTED", "reason": "registered pending"},
                ], "PENDING"),
                "2026-08-04T13:03:00+00:00", "2026-08-04T13:03:00+00:00",
            ),
        ]
        self.con.executemany(
            "INSERT INTO decision_traces_80 VALUES (?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "missed", "GC", "5m", "bearish", "INVALIDATED", 100, 105, 94,
                None, "2026-08-04T13:06:00+00:00", 98, "MISSED_EXTENDED", None,
                500, 0, "test", "2026-08-04T13:06:00+00:00",
            ),
        )
        self.con.execute(
            "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "winner", "GC", "1m", "bullish", "CLOSED", 100, 95, 107,
                "2026-08-04T13:04:00+00:00", "2026-08-04T13:10:00+00:00", 107,
                "WIN", 1.4, 750, 1050, "test", "2026-08-04T13:10:00+00:00",
            ),
        )
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def test_funnel_tracks_candidate_to_fill_chain_and_terminal_dropoffs(self):
        data = conversion_funnel81(self.con)
        funnel = data["funnel"]
        self.assertEqual(data["scope"]["name"], "NEW_YORK")
        self.assertEqual(funnel["detected"], 4)
        self.assertEqual(funnel["qualified"], 3)
        self.assertEqual(funnel["selected"], 2)
        self.assertEqual(funnel["registered"], 2)
        self.assertEqual(funnel["filled"], 1)
        self.assertEqual(funnel["closed"], 1)
        self.assertEqual(funnel["wins"], 1)
        self.assertEqual(funnel["losses"], 0)
        self.assertEqual(data["conversion"]["selected_to_registered_pct"], 100.0)
        self.assertEqual(data["conversion"]["registered_to_fill_pct"], 50.0)
        self.assertEqual(data["dropoffs"]["rr_blocked"], 1)
        self.assertEqual(data["dropoffs"]["arbiter_blocked"], 1)
        self.assertEqual(data["dropoffs"]["missed_extended"], 1)

    def test_overview_renderer_consumes_operation81_conversion_endpoint(self):
        script = (ROOT / "src" / "dashboard" / "static" / "decision-telemetry.js").read_text(encoding="utf-8")
        self.assertIn("/market/api/otr81/conversion", script)
        self.assertIn("Detected", script)
        self.assertIn("Qualified", script)
        self.assertIn("Selected", script)
        self.assertIn("Registered", script)
        self.assertIn("Filled", script)
        self.assertIn("WHERE OPPORTUNITIES DIED", script)

    def test_server81_registers_conversion_route(self):
        from src.dashboard import server_81
        from src.dashboard import app as dashboard

        server_81._install_conversion_api_81()
        paths = {getattr(route, "path", None) for route in dashboard.app.routes}
        self.assertIn("/market/api/otr81/conversion", paths)


if __name__ == "__main__":
    unittest.main()
