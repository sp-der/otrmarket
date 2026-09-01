from __future__ import annotations

from src.dashboard import server_72p as base
from src.storage.database import get_connection


def _audit_recent_closed() -> None:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT setup_id, symbol, timeframe, direction, entry_price, exit_price,
                   result, result_r, result_dollars, opened_at, closed_at, updated_at
            FROM paper_trades
            WHERE status='CLOSED'
            ORDER BY COALESCE(closed_at, updated_at, '') DESC, rowid DESC
            LIMIT 25
            """
        ).fetchall()
        print(f"LEDGER AUDIT 7.2 recent_closed={len(rows)}", flush=True)
        for row in rows:
            print("LEDGER ROW 7.2 " + repr(row), flush=True)
    finally:
        connection.close()


def main() -> None:
    _audit_recent_closed()
    base.main()


if __name__ == "__main__":
    main()
