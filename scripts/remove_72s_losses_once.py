from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path("/app/data/otrmarket.db")
EXCLUDED_KEY = "eval_reset_excluded_setup_ids_72"


def _load_excluded(connection: sqlite3.Connection) -> set[str]:
    row = connection.execute("SELECT value FROM engine_state WHERE key = ?", (EXCLUDED_KEY,)).fetchone()
    if not row or not row[0]:
        return set()
    try:
        values = json.loads(row[0])
    except Exception:
        return set()
    return {str(v) for v in values if str(v).strip()}


def _store_excluded(connection: sqlite3.Connection, values: set[str]) -> None:
    payload = json.dumps(sorted(values))
    connection.execute(
        "INSERT INTO engine_state(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (EXCLUDED_KEY, payload),
    )


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

        if len(rows) == 0:
            print("7.2S cleanup already complete; no matching loss rows remain.", flush=True)
            return

        symbols = {str(row[1]) for row in rows}
        if len(rows) != 2 or symbols != {"ES", "NQ"}:
            raise RuntimeError(
                "Refusing cleanup: expected exactly two -$125 MOMENTUM_SCALP losses, one ES and one NQ; "
                f"found {len(rows)} rows with symbols {sorted(symbols)}."
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

        excluded = _load_excluded(connection)
        excluded.difference_update(setup_ids)
        _store_excluded(connection, excluded)
        connection.commit()

        remaining = connection.execute(
            """
            SELECT COUNT(*)
            FROM paper_trades p
            JOIN trade_intelligence t ON t.setup_id = p.setup_id
            WHERE p.status = 'CLOSED'
              AND p.result = 'LOSS'
              AND ABS(COALESCE(p.result_dollars, 0) + 125.0) < 0.01
              AND t.strategy = 'MOMENTUM_SCALP'
            """
        ).fetchone()[0]
        if remaining != 0:
            raise RuntimeError(f"Cleanup verification failed; {remaining} matching rows remain.")

        print(f"7.2S cleanup removed setup_ids: {setup_ids}", flush=True)
        print("7.2S cleanup verification: 0 matching -$125 momentum-scalp losses remain.", flush=True)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
