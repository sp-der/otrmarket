from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SYMBOL_LABELS = {
    "BTC-USD": "Bitcoin",
    "NQ": "Nasdaq Futures",
    "ES": "S&P 500 Futures",
    "GC": "Gold Futures",
}


@dataclass
class DashboardRepository:
    db_path: Path

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database not found: {self.db_path}")
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _parse_time(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _table_exists(self, connection: sqlite3.Connection, name: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return bool(row)

    def _market_row(self, connection: sqlite3.Connection, symbol: str) -> dict[str, Any]:
        latest = connection.execute(
            """
            SELECT received_at, source, symbol, price, bid, ask, mid, spread, spread_bps
            FROM market_quotes
            WHERE symbol = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()

        if latest is None:
            return {
                "symbol": symbol,
                "name": SYMBOL_LABELS.get(symbol, symbol),
                "price": None,
                "bid": None,
                "ask": None,
                "spread": None,
                "spread_bps": None,
                "source": None,
                "received_at": None,
                "age_seconds": None,
                "return_1m": None,
                "return_5m": None,
                "quote_count": 0,
            }

        latest_time = self._parse_time(latest["received_at"])
        latest_price = latest["price"] if latest["price"] is not None else latest["mid"]

        def window_return(seconds: int) -> float | None:
            if latest_time is None or latest_price in (None, 0):
                return None
            target = (latest_time - timedelta(seconds=seconds)).isoformat()
            previous = connection.execute(
                """
                SELECT price, mid
                FROM market_quotes
                WHERE symbol = ? AND received_at <= ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (symbol, target),
            ).fetchone()
            if previous is None:
                return None
            old_price = previous["price"] if previous["price"] is not None else previous["mid"]
            if old_price in (None, 0):
                return None
            return ((latest_price - old_price) / old_price) * 100.0

        quote_count = connection.execute(
            "SELECT COUNT(*) FROM market_quotes WHERE symbol = ?",
            (symbol,),
        ).fetchone()[0]

        age_seconds = None
        if latest_time is not None:
            if latest_time.tzinfo is None:
                latest_time = latest_time.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (self._now() - latest_time).total_seconds())

        return {
            "symbol": symbol,
            "name": SYMBOL_LABELS.get(symbol, symbol),
            "price": latest_price,
            "bid": latest["bid"],
            "ask": latest["ask"],
            "spread": latest["spread"],
            "spread_bps": latest["spread_bps"],
            "source": latest["source"],
            "received_at": latest["received_at"],
            "age_seconds": age_seconds,
            "return_1m": window_return(60),
            "return_5m": window_return(300),
            "quote_count": quote_count,
        }

    def market_snapshot(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(connection, "market_quotes"):
            return []
        return [self._market_row(connection, symbol) for symbol in ("NQ", "ES", "GC", "BTC-USD")]

    def trade_stats(self, connection: sqlite3.Connection) -> dict[str, Any]:
        defaults = {
            "closed": 0,
            "wins": 0,
            "losses": 0,
            "invalidated": 0,
            "pending": 0,
            "open": 0,
            "win_rate": None,
            "total_r": 0.0,
            "avg_r": None,
            "profit_factor": None,
            "max_drawdown_r": 0.0,
            "today_r": 0.0,
        }
        if not self._table_exists(connection, "paper_trades"):
            return defaults

        rows = connection.execute(
            """
            SELECT setup_id, status, result, result_r, opened_at, closed_at
            FROM paper_trades
            ORDER BY COALESCE(closed_at, opened_at, updated_at) ASC
            """
        ).fetchall()

        closed_results = [row for row in rows if row["status"] == "CLOSED" and row["result_r"] is not None]
        wins = sum(1 for row in closed_results if row["result"] == "WIN")
        losses = sum(1 for row in closed_results if row["result"] == "LOSS")
        invalidated = sum(1 for row in rows if row["status"] == "INVALIDATED")
        pending = sum(1 for row in rows if row["status"] == "PENDING")
        open_count = sum(1 for row in rows if row["status"] == "OPEN")
        total_r = sum(float(row["result_r"] or 0.0) for row in closed_results)
        positive_r = sum(float(row["result_r"]) for row in closed_results if float(row["result_r"]) > 0)
        negative_r = abs(sum(float(row["result_r"]) for row in closed_results if float(row["result_r"]) < 0))

        running = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for row in closed_results:
            running += float(row["result_r"] or 0.0)
            peak = max(peak, running)
            max_drawdown = max(max_drawdown, peak - running)

        now_date = self._now().date()
        today_r = 0.0
        for row in closed_results:
            closed_at = self._parse_time(row["closed_at"])
            if closed_at is not None and closed_at.date() == now_date:
                today_r += float(row["result_r"] or 0.0)

        return {
            "closed": len(closed_results),
            "wins": wins,
            "losses": losses,
            "invalidated": invalidated,
            "pending": pending,
            "open": open_count,
            "win_rate": (wins / len(closed_results) * 100.0) if closed_results else None,
            "total_r": total_r,
            "avg_r": (total_r / len(closed_results)) if closed_results else None,
            "profit_factor": (positive_r / negative_r) if negative_r > 0 else (None if positive_r == 0 else positive_r),
            "max_drawdown_r": max_drawdown,
            "today_r": today_r,
        }

    def recent_trades(self, connection: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
        if not self._table_exists(connection, "paper_trades"):
            return []
        rows = connection.execute(
            """
            SELECT setup_id, symbol, timeframe, direction, status,
                   entry_price, stop_price, target_price, opened_at, closed_at,
                   exit_price, result, result_r, updated_at
            FROM paper_trades
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_setups(self, connection: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
        if not self._table_exists(connection, "strategy_setups"):
            return []
        rows = connection.execute(
            """
            SELECT setup_id, symbol, timeframe, direction, created_at, trigger_type,
                   entry_price, stop_price, target_price, risk_reward, status, payload_json
            FROM strategy_setups
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            try:
                payload = json.loads(item.pop("payload_json"))
            except (TypeError, json.JSONDecodeError):
                payload = {}
            item["metadata"] = payload.get("metadata", {})
            item["trigger_details"] = payload.get("trigger_details", {})
            result.append(item)
        return result

    def equity_curve(self, connection: sqlite3.Connection, limit: int = 500) -> list[dict[str, Any]]:
        if not self._table_exists(connection, "paper_trades"):
            return []
        rows = connection.execute(
            """
            SELECT setup_id, closed_at, result_r
            FROM paper_trades
            WHERE status = 'CLOSED' AND result_r IS NOT NULL
            ORDER BY closed_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        cumulative = 0.0
        points = [{"index": 0, "time": None, "equity_r": 0.0}]
        for index, row in enumerate(rows, start=1):
            cumulative += float(row["result_r"] or 0.0)
            points.append(
                {
                    "index": index,
                    "time": row["closed_at"],
                    "equity_r": cumulative,
                    "setup_id": row["setup_id"],
                }
            )
        return points

    def candle_summary(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(connection, "candles"):
            return []
        rows = connection.execute(
            """
            SELECT symbol, timeframe, COUNT(*) AS count, MAX(close_time) AS latest
            FROM candles
            GROUP BY symbol, timeframe
            ORDER BY symbol, timeframe
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def setup_counts(self, connection: sqlite3.Connection) -> dict[str, int]:
        if not self._table_exists(connection, "strategy_setups"):
            return {"total": 0}
        rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM strategy_setups GROUP BY status"
        ).fetchall()
        output = {"total": sum(row["count"] for row in rows)}
        for row in rows:
            output[str(row["status"]).lower()] = row["count"]
        return output

    def snapshot(self) -> dict[str, Any]:
        generated_at = self._now().isoformat()
        if not self.db_path.exists():
            return {
                "generated_at": generated_at,
                "database": {"ok": False, "path": str(self.db_path), "size_bytes": 0},
                "markets": [],
                "stats": self.trade_stats_empty(),
                "trades": [],
                "setups": [],
                "equity_curve": [],
                "candles": [],
                "setup_counts": {"total": 0},
            }

        with self._connect() as connection:
            return {
                "generated_at": generated_at,
                "database": {
                    "ok": True,
                    "path": str(self.db_path),
                    "size_bytes": self.db_path.stat().st_size,
                },
                "markets": self.market_snapshot(connection),
                "stats": self.trade_stats(connection),
                "trades": self.recent_trades(connection),
                "setups": self.recent_setups(connection),
                "equity_curve": self.equity_curve(connection),
                "candles": self.candle_summary(connection),
                "setup_counts": self.setup_counts(connection),
            }

    @staticmethod
    def trade_stats_empty() -> dict[str, Any]:
        return {
            "closed": 0,
            "wins": 0,
            "losses": 0,
            "invalidated": 0,
            "pending": 0,
            "open": 0,
            "win_rate": None,
            "total_r": 0.0,
            "avg_r": None,
            "profit_factor": None,
            "max_drawdown_r": 0.0,
            "today_r": 0.0,
        }
