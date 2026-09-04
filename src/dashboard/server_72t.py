from __future__ import annotations

import os

from fastapi import Request

from src.dashboard import server_72s as base
from src.research.live_training72t import training_snapshot_72t
from src.storage.database import get_connection, get_engine_state, set_engine_state


FULL_WIPE_STATE_KEY_72T = "last_verify_full_wipe_token_72t"
FULL_WIPE_TABLES_72T = (
    "paper_trades",
    "strategy_setups",
    "strategy_diagnostics",
    "trade_intelligence",
    "shadow_trades",
    "counterfactual_setups",
    "verify_run_trades",
    "training_decisions_72t",
    "training_trades_72t",
    "training_trade_metrics_72t",
    "training_counterfactuals_72t",
    "training_shadow_72t",
    "market_lessons",
    "learning_feature_stats",
    "verify_active_run_72s",
)


def _promote_engine_72t() -> str:
    # 72t -> 72s -> 72r -> 72q -> 72n -> 72, which owns the promotion hook.
    base.base.base.base.base.promoted_engine_module = lambda requested=None: "src.main_72t"
    return base.base.base.base.base.promoted_engine_module()


def _table_exists_72t(connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _full_verify_wipe_72t() -> dict[str, int]:
    """One-time destructive reset for a user-requested completely fresh test.

    Market quotes/candles and infrastructure/config are deliberately preserved so
    replay warmup and the NinjaTrader bridge remain functional. All trading,
    scoring, scanner, research/training and current-run membership state is reset.
    """
    token = os.getenv("OTR_VERIFY_FULL_WIPE_TOKEN", "").strip()
    if not token:
        return {}

    connection = get_connection()
    try:
        previous = get_engine_state(connection, FULL_WIPE_STATE_KEY_72T, "") or ""
        if previous == token:
            print("VERIFY 7.2T full reset token already applied; preserving fresh state", flush=True)
            return {}

        counts: dict[str, int] = {}
        for table in FULL_WIPE_TABLES_72T:
            if not _table_exists_72t(connection, table):
                continue
            counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            connection.execute(f"DELETE FROM {table}")

        # Force 7.2S/7.2Q to construct a genuinely new run from the fresh wipe token.
        for key in (
            "verify_test_run_id_72s",
            "verify_test_wipe_token_72s",
            "last_verify_wipe_token_72q",
        ):
            connection.execute("DELETE FROM engine_state WHERE key=?", (key,))
        set_engine_state(connection, "eval_reset_excluded_setup_ids_72", "[]")
        set_engine_state(connection, FULL_WIPE_STATE_KEY_72T, token)
        connection.commit()

        print(
            "VERIFY 7.2T FULL RESET complete: "
            + ", ".join(f"{table}={count}" for table, count in counts.items())
            + "; preserved market_quotes, candles, execution audit, bridge credentials and service config.",
            flush=True,
        )
        return counts
    finally:
        connection.close()


def _install_training_api_72t() -> None:
    from src.dashboard import app as dashboard

    # Keep 4H chart/context and Strategy Lab available, but let the normal
    # dashboard render its original pre-7.2T Overview again.
    dashboard.CHART_TIMEFRAMES.add("4h")
    expected = f"{dashboard.BASE_PATH}/api/training"
    if any(getattr(route, "path", None) == expected for route in dashboard.app.routes):
        return

    async def training_lab_snapshot(request: Request):
        dashboard.require_http_auth(request)
        return training_snapshot_72t(dashboard.DB_PATH, dashboard.RESEARCH_DB_PATH)

    dashboard.app.add_api_route(
        expected,
        training_lab_snapshot,
        methods=["GET"],
        name="training_lab_snapshot_72t",
    )


def main() -> None:
    reset_counts = _full_verify_wipe_72t()
    _install_training_api_72t()
    # Do not inject dashboard-minimal72t.css/js. The base 7.2S dashboard owns
    # the visible Overview again: Total R, Win Rate, All-Time P/L, Today's P/L,
    # Markets, Equity Curve, Trade Queue, Strategy Progress and recent trades.
    base._promote_engine_72s = _promote_engine_72t
    print(
        "Operation 7.2T supervisor: classic pre-simplify Overview restored + Strategy Lab + 4H macro context; "
        f"full_reset_rows={sum(reset_counts.values())}; 7.2S VERIFY accounting preserved; engine=src.main_72t",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
