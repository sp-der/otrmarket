import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.dashboard.queries import DashboardRepository


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "test.db"
        con = sqlite3.connect(self.db_path)
        con.executescript(
            """
            CREATE TABLE market_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                received_at TEXT NOT NULL,
                exchange_time TEXT,
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL,
                bid REAL,
                ask REAL,
                mid REAL,
                spread REAL,
                spread_bps REAL
            );
            CREATE TABLE candles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                ticks INTEGER NOT NULL DEFAULT 0
            );
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
                updated_at TEXT NOT NULL
            );
            """
        )
        con.execute(
            """INSERT INTO market_quotes
            (received_at, source, symbol, price, bid, ask, mid, spread, spread_bps)
            VALUES ('2026-08-13T20:00:00+00:00','coinbase','BTC-USD',64000,63999,64001,64000,2,.3125)"""
        )
        con.execute(
            """INSERT INTO paper_trades
            (setup_id,symbol,timeframe,direction,status,entry_price,stop_price,target_price,opened_at,closed_at,exit_price,result,result_r,updated_at)
            VALUES ('a','QQQ','5m','bullish','CLOSED',100,99,102,'2026-08-13T19:00:00+00:00','2026-08-13T19:05:00+00:00',102,'WIN',2.0,'2026-08-13T19:05:00+00:00')"""
        )
        con.execute(
            """INSERT INTO paper_trades
            (setup_id,symbol,timeframe,direction,status,entry_price,stop_price,target_price,opened_at,closed_at,exit_price,result,result_r,updated_at)
            VALUES ('b','QQQ','5m','bearish','CLOSED',100,101,99,'2026-08-13T19:10:00+00:00','2026-08-13T19:15:00+00:00',101,'LOSS',-1.0,'2026-08-13T19:15:00+00:00')"""
        )
        con.execute(
            """INSERT INTO strategy_setups
            (setup_id,symbol,timeframe,direction,created_at,trigger_type,entry_price,stop_price,target_price,risk_reward,status,payload_json)
            VALUES ('a','QQQ','5m','bullish','2026-08-13T19:00:00+00:00','liquidity_sweep',100,99,102,2.0,'PENDING',?)""",
            (json.dumps({"metadata": {"test": True}, "trigger_details": {"level": 99}}),),
        )
        con.commit()
        con.close()
        self.repo = DashboardRepository(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_trade_stats(self):
        with self.repo._connect() as con:
            stats = self.repo.trade_stats(con)
        self.assertEqual(stats["closed"], 2)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertAlmostEqual(stats["total_r"], 1.0)
        self.assertAlmostEqual(stats["win_rate"], 50.0)
        self.assertAlmostEqual(stats["profit_factor"], 2.0)
        self.assertAlmostEqual(stats["max_drawdown_r"], 1.0)

    def test_equity_curve(self):
        with self.repo._connect() as con:
            curve = self.repo.equity_curve(con)
        self.assertEqual(curve[-1]["equity_r"], 1.0)
        self.assertEqual(len(curve), 3)

    def test_snapshot(self):
        snapshot = self.repo.snapshot()
        self.assertTrue(snapshot["database"]["ok"])
        self.assertEqual(snapshot["setup_counts"]["total"], 1)
        self.assertEqual(snapshot["trades"][0]["setup_id"], "b")
        btc = next(item for item in snapshot["markets"] if item["symbol"] == "BTC-USD")
        self.assertEqual(btc["price"], 64000)

    def test_execution_chart_returns_candles_overlays_trades_and_pair_sync(self):
        con = sqlite3.connect(self.db_path)
        for symbol, offset in (("NQ", 0), ("ES", -10000)):
            con.execute(
                """INSERT INTO market_quotes
                (received_at,source,symbol,price,bid,ask,mid,spread,spread_bps)
                VALUES ('2026-08-25T14:33:00+00:00','replay',?,?,?,?,?,?,?)""",
                (
                    symbol,
                    22000 + offset,
                    21999.75 + offset,
                    22000.25 + offset,
                    22000 + offset,
                    .5,
                    .23,
                ),
            )
            for minute in range(3):
                base = 22000 + offset + minute
                con.execute(
                    """INSERT INTO candles
                    (symbol,timeframe,open_time,close_time,open,high,low,close,ticks)
                    VALUES (?,?, ?,?,?, ?,?,?,?)""",
                    (
                        symbol,
                        "1m",
                        f"2026-08-25T14:3{minute}:00+00:00",
                        f"2026-08-25T14:3{minute + 1}:00+00:00",
                        base,
                        base + 2,
                        base - 1,
                        base + 1,
                        12,
                    ),
                )

        payload = {
            "trigger_type": "smt",
            "entry_fvg": {
                "direction": "bullish",
                "lower": 22000.5,
                "upper": 22001.0,
                "formed_at": "2026-08-25T14:31:00+00:00",
            },
            "metadata": {
                "strategy": "ICT_CONFLUENCE",
                "entry_type": "ORDER_BLOCK",
                "entry_candidates": [{
                    "entry_type": "ORDER_BLOCK",
                    "details": {
                        "candle_time": "2026-08-25T14:30:00+00:00",
                        "zone_overlap_low": 21999.5,
                        "zone_overlap_high": 22000.5,
                    },
                }],
            },
            "trigger_details": {"leader": "NQ", "laggard": "ES"},
        }
        con.execute(
            """INSERT INTO strategy_setups
            (setup_id,symbol,timeframe,direction,created_at,trigger_type,entry_price,
             stop_price,target_price,risk_reward,status,payload_json)
            VALUES ('chart-nq','NQ','1m','bullish','2026-08-25T14:31:00+00:00',
                    'smt',22001,21999,22005,2.0,'PENDING',?)""",
            (json.dumps(payload),),
        )
        con.execute(
            """INSERT INTO paper_trades
            (setup_id,symbol,timeframe,direction,status,entry_price,stop_price,
             target_price,opened_at,closed_at,exit_price,result,result_r,updated_at)
            VALUES ('chart-nq','NQ','1m','bullish','CLOSED',22001,21999,22005,
                    '2026-08-25T14:31:00+00:00','2026-08-25T14:32:00+00:00',
                    22005,'WIN',2.0,'2026-08-25T14:32:00+00:00')"""
        )
        con.commit()
        con.close()

        chart = self.repo.execution_chart("NQ", "1m", 50)

        candle_times = [row["open_time"] for row in chart["candles"]]
        self.assertEqual(candle_times, sorted(candle_times))
        self.assertTrue(chart["pair"]["synchronized"])
        self.assertEqual(chart["pair"]["symbol"], "ES")
        self.assertEqual(chart["setups"][0]["overlay"]["entry_type"], "ORDER_BLOCK")
        self.assertEqual(chart["setups"][0]["overlay"]["order_block"]["low"], 21999.5)
        self.assertIsNotNone(chart["setups"][0]["overlay"]["smt"])
        self.assertEqual(chart["trades"][0]["exit_price"], 22005)
        self.assertEqual(chart["trades"][0]["display_result_dollars"], 500.0)

        from starlette.requests import Request
        from src.dashboard import app as dashboard_app

        request = Request({"type": "http", "headers": []})
        with patch.object(dashboard_app, "repository", self.repo):
            api_chart = asyncio.run(
                dashboard_app.execution_chart(request, "nq", "1m", 50)
            )
        self.assertEqual(api_chart["symbol"], "NQ")
        self.assertEqual(len(api_chart["candles"]), 3)

    def test_execution_chart_surface_is_loaded_by_dashboard(self):
        static_dir = Path(__file__).parents[1] / "src" / "dashboard" / "static"
        html = (static_dir / "index.html").read_text(encoding="utf-8")
        script = (static_dir / "execution-chart.js").read_text(encoding="utf-8")
        css = (static_dir / "execution-chart.css").read_text(encoding="utf-8")
        app_source = (Path(__file__).parents[1] / "src" / "dashboard" / "app.py").read_text(encoding="utf-8")

        self.assertIn('data-view="chart"', html)
        self.assertIn('id="primaryExecutionCanvas"', html)
        self.assertIn('id="pairExecutionCanvas"', html)
        self.assertIn("execution-chart.js?v=7.1", html)
        self.assertLess(html.index("RECENT ACTIVITY"), html.index("PROP ACCOUNT TRAINING"))
        self.assertLess(html.index("PROP ACCOUNT TRAINING"), html.index('id="chartView"'))
        self.assertIn("/api/chart", app_source)
        self.assertIn("drawMarker", script)
        self.assertIn(".execution-chart-grid", css)


if __name__ == "__main__":
    unittest.main()
