from __future__ import annotations

from src.dashboard.queries import DashboardRepository as BaseDashboardRepository, TRADING_TZ
from src.risk.evaluation import EvaluationConfig


ACTIVE_SYMBOLS_SQL = "'NQ','ES','GC'"


class DashboardRepository(BaseDashboardRepository):
    """Operation 5.9 dashboard view for the active futures-only test.

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
        """Aggregate the full realized futures ledger by New York trading date.

        This intentionally does not use recent_trades(), whose bounded window is
        appropriate for the journal but not for a historical P&L calendar.
        """
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

    def snapshot(self):
        snapshot = super().snapshot()
        if not snapshot.get("database", {}).get("ok"):
            snapshot["daily_realized_pnl"] = []
            return snapshot

        with self._connect() as connection:
            snapshot["daily_realized_pnl"] = self.daily_realized_pnl(connection)
        return snapshot
