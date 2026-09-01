from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path

from src.dashboard.queries import DashboardRepository
from src.dashboard import server_72n


class VerifyRun72QTests(unittest.TestCase):
    def setUp(self):
        self.old_mode = os.environ.get("OTR_TRADING_MODE")
        self.old_engine = os.environ.get("OTR_ENGINE_MODULE")
        self.old_risk = os.environ.get("OTR_VERIFY_RISK_PER_TRADE")
        os.environ["OTR_TRADING_MODE"] = "VERIFY"
        os.environ["OTR_ENGINE_MODULE"] = "src.main_72q"
        os.environ["OTR_VERIFY_RISK_PER_TRADE"] = "250"
        server_72n._reset_verify_run_state_72n()
        self.repo = DashboardRepository(Path("unused.db"))
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute(
            """
            CREATE TABLE paper_trades (
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
            )
            """
        )
        self.con.execute(
            """
            CREATE TABLE strategy_setups (
                setup_id TEXT PRIMARY KEY,
                payload_json TEXT
            )
            """
        )
        self.con.commit()

    def tearDown(self):
        self.con.close()
        server_72n._reset_verify_run_state_72n()
        for name, value in (
            ("OTR_TRADING_MODE", self.old_mode),
            ("OTR_ENGINE_MODULE", self.old_engine),
            ("OTR_VERIFY_RISK_PER_TRADE", self.old_risk),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def _insert_trade(
        self,
        setup_id: str,
        *,
        result: str,
        result_r: float,
        result_dollars: float,
        opened_at: str,
        closed_at: str,
        entry: float = 4500.0,
        stop: float = 4498.0,
        target: float = 4504.0,
        exit_price: float = 4504.0,
        strategy: str = "ICT_CONFLUENCE",
    ):
        direction = "bullish" if result_dollars >= 0 else "bearish"
        self.con.execute(
            """
            INSERT INTO paper_trades (
                setup_id, symbol, timeframe, direction, status,
                entry_price, stop_price, target_price, opened_at, closed_at,
                exit_price, result, result_r, risk_dollars, result_dollars,
                guard_reason, updated_at
            ) VALUES (?, 'GC', '1m', ?, 'CLOSED', ?, ?, ?, ?, ?, ?, ?, ?, 250, ?, NULL, ?)
            """,
            (
                setup_id,
                direction,
                entry,
                stop,
                target,
                opened_at,
                closed_at,
                exit_price,
                result,
                result_r,
                result_dollars,
                closed_at,
            ),
        )
        self.con.execute(
            "INSERT INTO strategy_setups(setup_id, payload_json) VALUES (?, ?)",
            (setup_id, '{"metadata":{"strategy":"' + strategy + '"}}'),
        )
        self.con.commit()

    def test_old_ledger_is_excluded_from_new_verify_run(self):
        for i in range(20):
            result = "WIN" if i < 6 else "LOSS"
            rr = 2.0 if result == "WIN" else -1.0
            dollars = 500.0 if result == "WIN" else -250.0
            self._insert_trade(
                f"old-{i}",
                result=result,
                result_r=rr,
                result_dollars=dollars,
                opened_at=f"2026-08-01T00:{i:02d}:00+00:00",
                closed_at=f"2026-08-01T00:{i:02d}:30+00:00",
            )

        first = server_72n._verify_run_snapshot_72n(
            self.repo,
            self.con,
            {"market_time": "2026-08-05T02:35:00+00:00"},
        )
        self.assertEqual(first["build"], "7.2Q")
        self.assertEqual(first["closed"], 0)
        self.assertEqual(first["wins"], 0)
        self.assertEqual(first["losses"], 0)
        self.assertEqual(first["total_dollars"], 0.0)

        self._insert_trade(
            "new-win",
            result="WIN",
            result_r=2.0,
            result_dollars=500.0,
            opened_at="2026-08-05T02:36:00+00:00",
            closed_at="2026-08-05T02:37:00+00:00",
            strategy="MSS_REVERSAL",
        )
        self._insert_trade(
            "new-loss",
            result="LOSS",
            result_r=-1.0,
            result_dollars=-250.0,
            opened_at="2026-08-05T02:38:00+00:00",
            closed_at="2026-08-05T02:39:00+00:00",
            strategy="ICT_CONFLUENCE",
        )

        run = server_72n._verify_run_snapshot_72n(
            self.repo,
            self.con,
            {"market_time": "2026-08-05T02:40:00+00:00"},
        )
        self.assertEqual(run["closed"], 2)
        self.assertEqual(run["wins"], 1)
        self.assertEqual(run["losses"], 1)
        self.assertEqual(run["win_rate"], 50.0)
        self.assertEqual(run["total_r"], 1.0)
        self.assertEqual(run["total_dollars"], 250.0)
        self.assertEqual(run["max_drawdown_r"], 1.0)
        self.assertEqual(run["strategy_breakdown"]["MSS_REVERSAL"], 1)
        self.assertEqual(run["strategy_breakdown"]["ICT_CONFLUENCE"], 1)

    def test_replayed_duplicate_inside_run_counts_once(self):
        server_72n._verify_run_snapshot_72n(
            self.repo,
            self.con,
            {"market_time": "2026-08-05T03:00:00+00:00"},
        )
        common = dict(
            result="WIN",
            result_r=2.0,
            result_dollars=500.0,
            opened_at="2026-08-05T03:01:00+00:00",
            closed_at="2026-08-05T03:02:00+00:00",
            entry=4510.0,
            stop=4508.0,
            target=4514.0,
            exit_price=4514.0,
        )
        self._insert_trade("copy-a", **common)
        self._insert_trade("copy-b", **common)

        run = server_72n._verify_run_snapshot_72n(
            self.repo,
            self.con,
            {"market_time": "2026-08-05T03:03:00+00:00"},
        )
        self.assertEqual(run["closed"], 1)
        self.assertEqual(run["wins"], 1)
        self.assertEqual(run["total_r"], 2.0)
        self.assertEqual(run["total_dollars"], 500.0)

    def test_verify_asset_promotes_current_run_scoreboard(self):
        text = Path("src/dashboard/static/verification-mode72.js").read_text(encoding="utf-8")
        self.assertIn("Current VERIFY Run", text)
        self.assertIn("verify_run", text)
        self.assertIn("originalRender", text)
        self.assertIn("Historical trades are not deleted", text)


if __name__ == "__main__":
    unittest.main()
