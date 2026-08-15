from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.risk.evaluation import EvaluationConfig, EvaluationRiskGuard


TRADING_TZ = ZoneInfo("America/New_York")


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
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
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

    @staticmethod
    def _column_exists(connection: sqlite3.Connection, table: str, column: str) -> bool:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return any(row[1] == column for row in rows)

    def _market_row(self, connection: sqlite3.Connection, symbol: str) -> dict[str, Any]:
        ingest_expr = "ingested_at" if self._column_exists(connection, "market_quotes", "ingested_at") else "received_at AS ingested_at"
        latest = connection.execute(
            f"""
            SELECT received_at, {ingest_expr}, source, symbol, price, bid, ask, mid, spread, spread_bps
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
                "ingested_at": None,
                "age_seconds": None,
                "market_age_seconds": None,
                "mode": "WAITING",
                "return_1m": None,
                "return_5m": None,
                "quote_count": 0,
            }

        latest_time = self._parse_time(latest["received_at"])
        ingest_time = self._parse_time(latest["ingested_at"])
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
        market_age_seconds = None
        if ingest_time is not None:
            age_seconds = max(0.0, (self._now() - ingest_time).total_seconds())
        if latest_time is not None:
            market_age_seconds = max(0.0, (self._now() - latest_time).total_seconds())

        mode = "WAITING"
        if latest_time is not None and ingest_time is not None:
            mode = "REPLAY" if abs((ingest_time - latest_time).total_seconds()) > 300 else "LIVE"

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
            "ingested_at": latest["ingested_at"],
            "age_seconds": age_seconds,
            "market_age_seconds": market_age_seconds,
            "mode": mode,
            "return_1m": window_return(60),
            "return_5m": window_return(300),
            "quote_count": quote_count,
        }

    def market_snapshot(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(connection, "market_quotes"):
            return []
        return [self._market_row(connection, symbol) for symbol in ("NQ", "ES", "GC", "BTC-USD")]

    def runtime_state(self, connection: sqlite3.Connection) -> dict[str, Any]:
        markets = self.market_snapshot(connection)
        replay_markets = [m for m in markets if m.get("mode") == "REPLAY" and m.get("age_seconds") is not None and m["age_seconds"] < 20]
        live_markets = [m for m in markets if m.get("mode") == "LIVE" and m.get("age_seconds") is not None and m["age_seconds"] < 20]
        active_market_times = [self._parse_time(m.get("received_at")) for m in replay_markets]
        active_market_times = [t for t in active_market_times if t is not None]
        return {
            "mode": "REPLAY" if replay_markets else "LIVE" if live_markets else "IDLE",
            "market_time": max(active_market_times).isoformat() if active_market_times else None,
            "replay_symbols": [m["symbol"] for m in replay_markets],
            "live_symbols": [m["symbol"] for m in live_markets],
        }

    @staticmethod
    def _display_result_dollars(row: sqlite3.Row, fallback_risk: float) -> float | None:
        """Return paper P/L dollars for dashboard display.

        Operation 4.5 records exact modeled dollar P/L for new trades. Legacy
        Operation 1-4.4 rows only stored R, so the dashboard normalizes those
        historical results using the configured base evaluation risk. This is
        display/account-training P/L only; the Evaluation Guard still ignores
        legacy rows because their persisted risk_dollars remains NULL.
        """
        if row["result_dollars"] is not None:
            return float(row["result_dollars"])
        if row["result_r"] is None:
            return None
        return float(row["result_r"]) * float(fallback_risk)

    def trade_stats(self, connection: sqlite3.Connection, reference_time: datetime | None = None) -> dict[str, Any]:
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
            "total_dollars": 0.0,
            "today_dollars": 0.0,
        }
        if not self._table_exists(connection, "paper_trades"):
            return defaults

        risk_expr = "risk_dollars" if self._column_exists(connection, "paper_trades", "risk_dollars") else "NULL AS risk_dollars"
        result_dollars_expr = "result_dollars" if self._column_exists(connection, "paper_trades", "result_dollars") else "NULL AS result_dollars"
        rows = connection.execute(
            f"""
            SELECT setup_id, status, result, result_r, {risk_expr}, {result_dollars_expr}, opened_at, closed_at, updated_at
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
        fallback_risk = EvaluationConfig.from_env().risk_per_trade
        display_dollars = [self._display_result_dollars(row, fallback_risk) for row in closed_results]
        total_dollars = sum(float(value or 0.0) for value in display_dollars if value is not None)
        positive_r = sum(float(row["result_r"]) for row in closed_results if float(row["result_r"]) > 0)
        negative_r = abs(sum(float(row["result_r"]) for row in closed_results if float(row["result_r"]) < 0))

        running = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for row in closed_results:
            running += float(row["result_r"] or 0.0)
            peak = max(peak, running)
            max_drawdown = max(max_drawdown, peak - running)

        reference_time = reference_time or self._now()
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        trading_date = reference_time.astimezone(TRADING_TZ).date()
        today_r = 0.0
        today_dollars = 0.0
        for row in closed_results:
            closed_at = self._parse_time(row["closed_at"])
            if closed_at is not None and closed_at.astimezone(TRADING_TZ).date() == trading_date:
                today_r += float(row["result_r"] or 0.0)
                value = self._display_result_dollars(row, fallback_risk)
                today_dollars += float(value or 0.0)

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
            "total_dollars": total_dollars,
            "today_dollars": today_dollars,
        }

    def recent_trades(self, connection: sqlite3.Connection, limit: int = 30) -> list[dict[str, Any]]:
        if not self._table_exists(connection, "paper_trades"):
            return []
        risk_expr = "risk_dollars" if self._column_exists(connection, "paper_trades", "risk_dollars") else "NULL AS risk_dollars"
        result_dollars_expr = "result_dollars" if self._column_exists(connection, "paper_trades", "result_dollars") else "NULL AS result_dollars"
        guard_expr = "guard_reason" if self._column_exists(connection, "paper_trades", "guard_reason") else "NULL AS guard_reason"
        rows = connection.execute(
            f"""
            SELECT setup_id, symbol, timeframe, direction, status,
                   entry_price, stop_price, target_price, opened_at, closed_at,
                   exit_price, result, result_r, {risk_expr}, {result_dollars_expr}, {guard_expr}, updated_at
            FROM paper_trades
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        fallback_risk = EvaluationConfig.from_env().risk_per_trade
        output = []
        for row in rows:
            item = dict(row)
            item["display_result_dollars"] = self._display_result_dollars(row, fallback_risk)
            output.append(item)
        return output

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

    def diagnostics(self, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        if not self._table_exists(connection, "strategy_diagnostics"):
            return []
        rows = connection.execute(
            """
            SELECT symbol, timeframe, market_time, stage, direction,
                   pd_array, signal, displacement, entry_fvg, retracement, rr,
                   trigger_type, note, setup_id, updated_at
            FROM strategy_diagnostics
            ORDER BY
                CASE timeframe WHEN '1m' THEN 1 WHEN '5m' THEN 2 WHEN '15m' THEN 3 WHEN '1h' THEN 4 ELSE 9 END,
                symbol
            """
        ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            for key in ("pd_array", "signal", "displacement", "entry_fvg", "retracement", "rr"):
                item[key] = bool(item[key])
            item["score"] = sum(int(item[k]) for k in ("pd_array", "signal", "displacement", "entry_fvg", "retracement", "rr"))
            output.append(item)
        return output

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

    def evaluation_snapshot(self, connection: sqlite3.Connection, runtime: dict[str, Any]) -> dict[str, Any]:
        reference = self._parse_time(runtime.get("market_time")) or self._now()
        return EvaluationRiskGuard().snapshot(connection, reference)

    def snapshot(self) -> dict[str, Any]:
        generated_at = self._now().isoformat()
        if not self.db_path.exists():
            return {
                "generated_at": generated_at,
                "database": {"ok": False, "path": str(self.db_path), "size_bytes": 0},
                "runtime": {"mode": "IDLE", "market_time": None, "replay_symbols": [], "live_symbols": []},
                "evaluation": {},
                "markets": [],
                "diagnostics": [],
                "stats": self.trade_stats_empty(),
                "trades": [],
                "setups": [],
                "equity_curve": [],
                "candles": [],
                "setup_counts": {"total": 0},
            }

        with self._connect() as connection:
            runtime = self.runtime_state(connection)
            return {
                "generated_at": generated_at,
                "database": {
                    "ok": True,
                    "path": str(self.db_path),
                    "size_bytes": self.db_path.stat().st_size,
                },
                "runtime": runtime,
                "evaluation": self.evaluation_snapshot(connection, runtime),
                "markets": self.market_snapshot(connection),
                "diagnostics": self.diagnostics(connection),
                "stats": self.trade_stats(connection, self._parse_time(runtime.get("market_time")) or self._now()),
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
            "total_dollars": 0.0,
            "today_dollars": 0.0,
        }
