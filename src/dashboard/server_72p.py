from __future__ import annotations

import os

from src.dashboard import server_72o as base
from src.storage.database import get_connection, get_engine_state, set_engine_state


STATE_KEY = "last_loss_cutoff_prune_token_72p"


def _prune_losses_after_cutoff_once() -> None:
    token = os.getenv("OTR_PRUNE_LOSSES_AFTER_TIME_TOKEN", "").strip()
    cutoff = os.getenv("OTR_PRUNE_LOSSES_AFTER_TIME", "").strip()
    if not token or not cutoff:
        return

    connection = get_connection()
    try:
        previous = get_engine_state(connection, STATE_KEY, "") or ""
        if previous == token:
            print("Operation 7.2P cutoff cleanup already applied; preserving current ledger", flush=True)
            return

        rows = connection.execute(
            """
            SELECT setup_id FROM paper_trades
            WHERE status = 'CLOSED'
              AND UPPER(COALESCE(result, '')) = 'LOSS'
              AND COALESCE(result_dollars, 0) < 0
              AND COALESCE(closed_at, updated_at, '') >= ?
            """,
            (cutoff,),
        ).fetchall()
        ids = [str(row[0]) for row in rows if row and row[0]]
        for setup_id in ids:
            connection.execute("DELETE FROM paper_trades WHERE setup_id = ?", (setup_id,))

        set_engine_state(connection, STATE_KEY, token)
        connection.commit()
        print(
            f"Operation 7.2P cleanup complete: removed {len(ids)} losing replay trade(s) at/after {cutoff}; wins and diagnostics preserved",
            flush=True,
        )
    finally:
        connection.close()


def main() -> None:
    _prune_losses_after_cutoff_once()
    base.main()


if __name__ == "__main__":
    main()
