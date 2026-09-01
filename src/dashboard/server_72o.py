from __future__ import annotations

import os

from src.dashboard import server_72n as base
from src.storage.database import get_connection, get_engine_state, set_engine_state


PRUNE_STATE_KEY = "last_post_peak_loss_prune_token_72o"


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _normalize_verify_environment() -> None:
    if not base._verification_enabled_72n():
        return

    # VERIFY is a strategy-performance experiment. Remove time-of-day sizing
    # differences so a +1R and -1R trade use the same simulated dollar unit.
    for name in (
        "OTR_CORE_RISK_MULTIPLIER",
        "OTR_BTC_OFFHOURS_RISK_MULTIPLIER",
        "OTR_SUNDAY_GLOBEX_RISK_MULTIPLIER",
        "OTR_ASIA_RISK_MULTIPLIER",
        "OTR_LONDON_RISK_MULTIPLIER",
        "OTR_PREMARKET_RISK_MULTIPLIER",
        "OTR_AFTERNOON_RISK_MULTIPLIER",
        "OTR_LATE_RISK_MULTIPLIER",
    ):
        os.environ[name] = "1.0"


def _prune_post_peak_losses_once() -> None:
    """Remove only losing test rows that occurred after the requested P/L mark.

    This is intentionally token-gated and one-shot. It changes the replay/test
    ledger only; market data, candles, learning memory, diagnostics, and winning
    trades are preserved for analysis.
    """
    token = os.getenv("OTR_PRUNE_LOSSES_TOKEN", "").strip()
    if not token:
        return

    threshold = _float_env("OTR_PRUNE_LOSSES_AFTER_PNL", 3300.0)
    connection = get_connection()
    try:
        previous = get_engine_state(connection, PRUNE_STATE_KEY, "") or ""
        if previous == token:
            print("Operation 7.2O post-peak loss prune already applied; preserving current ledger", flush=True)
            return

        rows = connection.execute(
            """
            SELECT setup_id, result, COALESCE(result_dollars, 0),
                   COALESCE(closed_at, updated_at, '') AS event_time
            FROM paper_trades
            WHERE status = 'CLOSED'
            ORDER BY event_time ASC, rowid ASC
            """
        ).fetchall()

        running = 0.0
        crossed = False
        losing_ids: list[str] = []
        crossing_setup = None
        for setup_id, result, dollars, _event_time in rows:
            running += float(dollars or 0.0)
            if not crossed and running >= threshold:
                crossed = True
                crossing_setup = str(setup_id)
                continue
            if crossed and str(result or "").upper() == "LOSS" and float(dollars or 0.0) < 0:
                losing_ids.append(str(setup_id))

        if not crossed:
            print(
                f"Operation 7.2O prune skipped: realized ledger never reached ${threshold:.2f}; no rows removed",
                flush=True,
            )
            set_engine_state(connection, PRUNE_STATE_KEY, token)
            return

        for setup_id in losing_ids:
            connection.execute("DELETE FROM paper_trades WHERE setup_id = ?", (setup_id,))

        set_engine_state(connection, PRUNE_STATE_KEY, token)
        connection.commit()
        print(
            f"Operation 7.2O cleanup complete: removed {len(losing_ids)} losing replay trade(s) after the ${threshold:.2f} mark; "
            f"wins and diagnostics preserved (crossing setup={crossing_setup})",
            flush=True,
        )
    finally:
        connection.close()


def main() -> None:
    _normalize_verify_environment()
    _prune_post_peak_losses_once()

    # Run the 7.2N dashboard contract, but promote the strategy subprocess to
    # the 7.2O fixed-risk VERIFY engine. EVAL/FUNDED behavior remains inherited.
    base.base.promoted_engine_module = lambda: "src.main_72o"
    base.main()


if __name__ == "__main__":
    main()
