from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path("/app/data/otrmarket.db")


def main() -> None:
    connection = sqlite3.connect(DB_PATH)
    try:
        rows = connection.execute(
            """
            SELECT p.setup_id, p.symbol, p.opened_at, p.closed_at, p.result_dollars
            FROM paper_trades p
            JOIN trade_intelligence t ON t.setup_id = p.setup_id
            WHERE p.status = 'CLOSED'
              AND p.result = 'LOSS'
              AND ABS(COALESCE(p.result_dollars, 0) + 125.0) < 0.01
              AND t.strategy = 'MOMENTUM_SCALP'
            ORDER BY p.opened_at
            """
        ).fetchall()
        print(f"7.2S cleanup candidates: {rows}", flush=True)
        if len(rows) != 2:
            raise RuntimeError(
                f"Expected exactly 2 closed 7.2S momentum-scalp losses at -$125; found {len(rows)}. Refusing cleanup."
            )

        setup_ids = [str(row[0]) for row in rows]
        placeholders = ",".join("?" for _ in setup_ids)

        for table in ("trade_intelligence", "paper_trades", "strategy_setups"):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists:
                connection.execute(
                    f"DELETE FROM {table} WHERE setup_id IN ({placeholders})",
                    setup_ids,
                )

        connection.commit()
        print(f"7.2S cleanup removed setup_ids: {setup_ids}", flush=True)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
