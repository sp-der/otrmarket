from __future__ import annotations

from src.dashboard.queries import DashboardRepository as BaseDashboardRepository


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
