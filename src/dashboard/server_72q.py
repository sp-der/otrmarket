from __future__ import annotations

from datetime import datetime, timezone
import os

from src.dashboard import server_72n as base
from src.storage.database import get_connection, get_engine_state, set_engine_state


VERIFY_RISK_MULTIPLIER_VARS = (
    "OTR_CORE_RISK_MULTIPLIER",
    "OTR_BTC_OFFHOURS_RISK_MULTIPLIER",
    "OTR_SUNDAY_GLOBEX_RISK_MULTIPLIER",
    "OTR_ASIA_RISK_MULTIPLIER",
    "OTR_LONDON_RISK_MULTIPLIER",
    "OTR_PREMARKET_RISK_MULTIPLIER",
    "OTR_AFTERNOON_RISK_MULTIPLIER",
    "OTR_LATE_RISK_MULTIPLIER",
)

VERIFY_WIPE_STATE_KEY_72Q = "last_verify_wipe_token_72q"
VERIFY_WIPE_TABLES_72Q = (
    "paper_trades",
    "strategy_setups",
    "strategy_diagnostics",
    "trade_intelligence",
    "shadow_trades",
    "counterfactual_setups",
    "verify_run_trades",
)


def _normalize_verify_environment_72q() -> None:
    if not base._verification_enabled_72n():
        return
    for name in VERIFY_RISK_MULTIPLIER_VARS:
        os.environ[name] = "1.0"


def _table_exists_72q(connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _wipe_verify_test_state_72q() -> dict[str, int]:
    """Hard-reset replay/test artifacts only when explicitly requested.

    Normal Operation 7.2 eval resets are intentionally non-destructive. A VERIFY
    replay is different: when OTR_VERIFY_WIPE_TOKEN changes, start a genuinely
    clean experiment by removing prior trade/setup/scanner/intelligence/shadow
    artifacts and run tags. Raw market quotes, completed candles, quote counters,
    broker execution audit, and market_lessons are preserved.

    The token is persisted so ordinary process restarts cannot wipe a run twice.
    This path is disabled outside VERIFY/VERIFICATION/TEST mode.
    """
    if not base._verification_enabled_72n():
        return {}

    token = os.getenv("OTR_VERIFY_WIPE_TOKEN", "").strip()
    if not token:
        return {}

    connection = get_connection()
    try:
        previous = get_engine_state(connection, VERIFY_WIPE_STATE_KEY_72Q, "") or ""
        if previous == token:
            print(
                "VERIFY 7.2Q hard reset token already applied; preserving current replay run",
                flush=True,
            )
            # Prevent the older eval-reset path from interacting with this clean
            # VERIFY run in the same process.
            os.environ["OTR_RESET_EVAL_TOKEN"] = ""
            return {}

        counts: dict[str, int] = {}
        for table in VERIFY_WIPE_TABLES_72Q:
            if not _table_exists_72q(connection, table):
                continue
            counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            connection.execute(f"DELETE FROM {table}")

        # Operation 7.2G's non-destructive reset keeps a list of prior setup IDs.
        # Once the ledger itself is deliberately wiped, that exclusion list must
        # also return to an empty state so the next experiment is internally clean.
        set_engine_state(connection, "eval_reset_excluded_setup_ids_72", "[]")
        set_engine_state(connection, VERIFY_WIPE_STATE_KEY_72Q, token)
        connection.commit()

        os.environ["OTR_RESET_EVAL_TOKEN"] = ""
        print(
            "VERIFY 7.2Q HARD RESET complete: "
            + ", ".join(f"{table}={count}" for table, count in counts.items())
            + "; preserved market_quotes, candles, quote counters, execution audit, and market_lessons.",
            flush=True,
        )
        return counts
    finally:
        connection.close()


def _install_verify_run_id_72q() -> str:
    """Give this VERIFY deployment a stable run ID inherited by the engine.

    Railway exposes a deployment ID that remains stable across process restarts
    of the same deployment. Local/dev runs fall back to a UTC launch stamp.
    """
    if not base._verification_enabled_72n():
        return ""
    existing = os.getenv("OTR_VERIFY_RUN_ID", "").strip()
    if existing:
        return existing
    deployment = os.getenv("RAILWAY_DEPLOYMENT_ID", "").strip()
    token = deployment[:12] if deployment else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"7.2Q-{token}"
    os.environ["OTR_VERIFY_RUN_ID"] = run_id
    return run_id


def main() -> None:
    _normalize_verify_environment_72q()
    _wipe_verify_test_state_72q()
    run_id = _install_verify_run_id_72q()
    base.base.promoted_engine_module = lambda: "src.main_72q"
    print(
        "Operation 7.2Q supervisor: clean VERIFY runtime, no loss-pruning hooks; "
        f"engine=src.main_72q verify_run_id={run_id or 'inactive'}",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
