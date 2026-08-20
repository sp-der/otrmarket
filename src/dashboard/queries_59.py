from __future__ import annotations

import json
from collections import Counter

from src.dashboard.queries import DashboardRepository as BaseDashboardRepository, TRADING_TZ
from src.risk.evaluation import EvaluationConfig


ACTIVE_SYMBOLS_SQL = "'NQ','ES','GC'"
ACTIVE_SYMBOLS = ("NQ", "ES", "GC")


class DashboardRepository(BaseDashboardRepository):
    """Operation 7.0 dashboard view for the active futures-only test.

    BTC history remains stored in the persistent SQLite database for later
    research. The dashboard connection shadows symbol-bearing tables with
    read-only TEMP views, so current evaluation stats, equity, trades, setups,
    scanner diagnostics, candles, and market cards are all calculated from the
    same NQ/ES/GC universe the execution engine is using.
    """

    def _connect(self):
        connection = super()._connect()
        for table in (
            "paper_trades",
            "strategy_setups",
            "strategy_diagnostics",
            "candles",
            "market_quotes",
        ):
            exists = connection.execute(
                "SELECT 1 FROM main.sqlite_master WHERE type IN ('table','view') AND name = ?",
                (table,),
            ).fetchone()
            if not exists:
                continue
            connection.execute(
                f"CREATE TEMP VIEW {table} AS "
                f"SELECT * FROM main.{table} WHERE symbol IN ({ACTIVE_SYMBOLS_SQL})"
            )
        return connection

    def daily_realized_pnl(self, connection):
        """Aggregate the full realized futures ledger by New York trading date."""
        if not self._table_exists(connection, "paper_trades"):
            return []

        result_dollars_expr = (
            "result_dollars"
            if self._column_exists(connection, "paper_trades", "result_dollars")
            else "NULL AS result_dollars"
        )
        rows = connection.execute(
            f"""
            SELECT result, result_r, {result_dollars_expr}, closed_at
            FROM paper_trades
            WHERE status = 'CLOSED'
              AND result IN ('WIN', 'LOSS')
              AND closed_at IS NOT NULL
            ORDER BY closed_at ASC
            """
        ).fetchall()

        fallback_risk = EvaluationConfig.from_env().risk_per_trade
        days = {}
        for row in rows:
            closed_at = self._parse_time(row["closed_at"])
            if closed_at is None:
                continue
            key = closed_at.astimezone(TRADING_TZ).date().isoformat()
            bucket = days.setdefault(
                key,
                {"date": key, "pnl": 0.0, "closed": 0, "wins": 0, "losses": 0},
            )
            value = self._display_result_dollars(row, fallback_risk)
            bucket["pnl"] += float(value or 0.0)
            bucket["closed"] += 1
            bucket["wins"] += int(row["result"] == "WIN")
            bucket["losses"] += int(row["result"] == "LOSS")

        return [days[key] for key in sorted(days)]

    @staticmethod
    def _decision_bucket(status: str, reason: str) -> str:
        status = str(status or "").upper()
        lower = str(reason or "").lower()
        if status == "SESSION_BLOCKED":
            return "session"
        if status not in {"QUALITY_BLOCKED", "SESSION_BLOCKED"}:
            return "accepted"
        if "cooldown" in lower or "reset window" in lower or "post-loss reset" in lower:
            return "cooldown"
        if "recovery" in lower or "after a realized" in lower or "post-loss" in lower:
            return "post_loss"
        if "correlated exposure" in lower or "active paper idea" in lower or "risk gate" in lower:
            return "risk_exposure"
        if "r:r" in lower or "rr" in lower or "offers only" in lower:
            return "risk_reward"
        if "context" in lower or "narrative" in lower or "countertrend" in lower:
            return "context"
        if "fvg" in lower or "ote" in lower or "entry" in lower or "shallow" in lower or "chase" in lower:
            return "entry_geometry"
        if "smt" in lower or "sweep" in lower or "displacement" in lower or "trigger" in lower:
            return "confirmation"
        return "quality_other"

    def decision_telemetry(self, connection):
        """Explain where today's/replay-day candidates were accepted or rejected."""
        if not self._table_exists(connection, "strategy_setups"):
            return {"trading_day": None, "markets": []}

        rows = connection.execute(
            """
            SELECT symbol, timeframe, status, created_at, payload_json
            FROM strategy_setups
            ORDER BY created_at DESC
            LIMIT 1500
            """
        ).fetchall()
        if not rows:
            return {"trading_day": None, "markets": []}

        trading_day = None
        for row in rows:
            parsed = self._parse_time(row["created_at"])
            if parsed is not None:
                trading_day = parsed.astimezone(TRADING_TZ).date()
                break
        if trading_day is None:
            return {"trading_day": None, "markets": []}

        by_symbol = {
            symbol: {
                "symbol": symbol,
                "candidates": 0,
                "accepted": 0,
                "blocked": 0,
                "buckets": Counter(),
                "latest_reasons": [],
            }
            for symbol in ACTIVE_SYMBOLS
        }

        for row in rows:
            symbol = str(row["symbol"] or "")
            if symbol not in by_symbol:
                continue
            parsed = self._parse_time(row["created_at"])
            if parsed is None or parsed.astimezone(TRADING_TZ).date() != trading_day:
                continue

            reason = ""
            try:
                payload = json.loads(row["payload_json"] or "{}")
                reason = str(payload.get("metadata", {}).get("execution_quality_gate", {}).get("reason") or "")
            except (TypeError, ValueError):
                payload = {}

            bucket = self._decision_bucket(row["status"], reason)
            market = by_symbol[symbol]
            market["candidates"] += 1
            market["buckets"][bucket] += 1
            if bucket == "accepted":
                market["accepted"] += 1
            else:
                market["blocked"] += 1
                if reason and reason not in market["latest_reasons"] and len(market["latest_reasons"]) < 4:
                    market["latest_reasons"].append(reason)

        markets = []
        for symbol in ACTIVE_SYMBOLS:
            item = by_symbol[symbol]
            item["buckets"] = dict(item["buckets"].most_common())
            markets.append(item)

        return {"trading_day": trading_day.isoformat(), "markets": markets}

    def snapshot(self):
        snapshot = super().snapshot()
        if not snapshot.get("database", {}).get("ok"):
            snapshot["daily_realized_pnl"] = []
            snapshot["decision_telemetry"] = {"trading_day": None, "markets": []}
            return snapshot

        with self._connect() as connection:
            snapshot["daily_realized_pnl"] = self.daily_realized_pnl(connection)
            snapshot["decision_telemetry"] = self.decision_telemetry(connection)
        return snapshot
