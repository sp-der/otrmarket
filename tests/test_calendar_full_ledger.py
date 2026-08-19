from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from src.dashboard.queries_59 import DashboardRepository


ROOT = Path(__file__).resolve().parents[1]


class FullLedgerCalendarTests(unittest.TestCase):
    def test_daily_realized_pnl_keeps_rows_beyond_recent_trade_window(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE paper_trades (
                setup_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                result_r REAL,
                result_dollars REAL,
                closed_at TEXT
            )
            """
        )

        rows = []
        for index in range(30):
            rows.append(
                (
                    f"aug18-win-{index}",
                    "GC",
                    "CLOSED",
                    "WIN",
                    1.0,
                    10.0,
                    f"2026-08-18T{12 + (index % 8):02d}:{index % 60:02d}:00+00:00",
                )
            )
        rows.append(
            (
                "aug18-loss",
                "GC",
                "CLOSED",
                "LOSS",
                -1.0,
                -5.0,
                "2026-08-18T20:30:00+00:00",
            )
        )
        for index in range(4):
            rows.append(
                (
                    f"aug19-win-{index}",
                    "GC",
                    "CLOSED",
                    "WIN",
                    1.0,
                    20.0,
                    f"2026-08-19T12:{index:02d}:00+00:00",
                )
            )
        rows.append(("open-row", "GC", "OPEN", None, None, None, None))

        connection.executemany(
            "INSERT INTO paper_trades VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()

        repository = DashboardRepository(Path("unused.db"))
        daily = repository.daily_realized_pnl(connection)
        by_date = {row["date"]: row for row in daily}

        self.assertEqual(by_date["2026-08-18"]["closed"], 31)
        self.assertEqual(by_date["2026-08-18"]["wins"], 30)
        self.assertEqual(by_date["2026-08-18"]["losses"], 1)
        self.assertAlmostEqual(by_date["2026-08-18"]["pnl"], 295.0)
        self.assertEqual(by_date["2026-08-19"]["closed"], 4)
        self.assertAlmostEqual(by_date["2026-08-19"]["pnl"], 80.0)

    def test_calendar_prefers_full_ledger_snapshot_field(self):
        source = (ROOT / "src/dashboard/static/trading-days.js").read_text(encoding="utf-8")
        self.assertIn("snapshot?.daily_realized_pnl", source)
        self.assertIn("groupDailyRealized(fullLedger)", source)
        self.assertIn("Array.isArray(fullLedger)", source)
        self.assertIn("groupTrades(snapshot?.trades || [])", source)

    def test_calendar_assets_are_cache_busted_and_mobile_safe(self):
        index = (ROOT / "src/dashboard/static/index.html").read_text(encoding="utf-8")
        css = (ROOT / "src/dashboard/static/trading-days.css").read_text(encoding="utf-8")

        self.assertIn("trading-days.css?v=6.6.2", index)
        self.assertIn("trading-days.js?v=6.6.2", index)
        self.assertIn("@media (max-width: 560px)", css)
        self.assertIn("@media (max-width: 390px)", css)
        self.assertIn("white-space: nowrap", css)
        self.assertIn("font-variant-numeric: tabular-nums", css)
        self.assertIn("font-size: clamp(7px, 2.05vw, 9px)", css)


if __name__ == "__main__":
    unittest.main()
