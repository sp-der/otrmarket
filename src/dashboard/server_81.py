from __future__ import annotations

from pathlib import Path

from fastapi import Request

from src.dashboard import server_80 as base
from src.research.conversion_funnel81 import conversion_funnel81
from src.storage.database import get_connection, get_engine_state, set_engine_state


RUN_RESET_STATE_KEY_81 = "operation81_run_reset_generation"
RUN_RESET_GENERATION_81 = "overnight-2026-09-05-c"
RUN_RESET_TABLES_81 = (
    "paper_trades",
    "strategy_setups",
    "strategy_diagnostics",
    "decision_traces_80",
    "verify_run_trades",
    "training_decisions_72t",
    "training_trades_72t",
    "training_trade_metrics_72t",
    "training_counterfactuals_72t",
    "training_shadow_72t",
    "verify_active_run_72s",
)


def _promote_engine_81() -> str:
    base.core72.promoted_engine_module = lambda requested=None: "src.main_81"
    return base.core72.promoted_engine_module()


def _reset_active_replay_progress_81() -> dict[str, int]:
    """One-time clean scorecard reset for the user-approved overnight 8.1 replay.

    Trading/run state is cleared so Overview, EVAL accounting, conversion telemetry,
    scanner state and the trade list begin at zero. Long-lived learning evidence is
    deliberately preserved: market_quotes, candles, counterfactual_setups,
    market_lessons, learning_feature_stats, trade_intelligence and shadow history.
    The generation marker makes this idempotent across Railway restarts.
    """
    connection = get_connection()
    try:
        previous = get_engine_state(connection, RUN_RESET_STATE_KEY_81, "") or ""
        if previous == RUN_RESET_GENERATION_81:
            print(
                "Operation 8.1 overnight replay reset already applied; preserving the current fresh run.",
                flush=True,
            )
            return {}

        counts: dict[str, int] = {}
        for table in RUN_RESET_TABLES_81:
            if not base.legacy._table_exists_72t(connection, table):
                continue
            counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            connection.execute(f"DELETE FROM {table}")

        # A completely fresh active run must not inherit old run-membership IDs.
        for key in (
            "verify_test_run_id_72s",
            "verify_test_wipe_token_72s",
            "last_verify_wipe_token_72q",
        ):
            connection.execute("DELETE FROM engine_state WHERE key=?", (key,))
        set_engine_state(connection, "eval_reset_excluded_setup_ids_72", "[]")
        set_engine_state(connection, RUN_RESET_STATE_KEY_81, RUN_RESET_GENERATION_81)
        connection.commit()

        summary = ", ".join(f"{table}={count}" for table, count in counts.items()) or "no prior run rows"
        print(
            "Operation 8.1 FRESH OVERNIGHT REPLAY RESET complete: "
            + summary
            + "; preserved candles, market quotes, counterfactual learning, market lessons, feature stats, intelligence and shadow history.",
            flush=True,
        )
        return counts
    finally:
        connection.close()


def _install_conversion_api_81() -> None:
    from src.dashboard import app as dashboard

    path = f"{dashboard.BASE_PATH}/api/otr81/conversion"
    if any(getattr(route, "path", None) == path for route in dashboard.app.routes):
        return

    async def conversion_snapshot(request: Request):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            return conversion_funnel81(connection, symbol="GC")
        finally:
            connection.close()

    dashboard.app.add_api_route(
        path,
        conversion_snapshot,
        methods=["GET"],
        name="gold_execution_conversion_81",
    )


def _install_connection_fallback_81() -> None:
    """Keep the full dashboard live behind Vercel rewrites.

    Vercel serves /gold correctly and proxies the HTTP APIs, but its current rewrite
    path does not preserve the dashboard websocket upgrade. Operation 8.1 therefore
    polls the already-working snapshot endpoint and feeds that payload into the same
    renderer app.js uses for websocket snapshots. This keeps markets, P/L, trades,
    scanner, queue, EVAL cards and connection status updating without a websocket.
    """
    path = Path(__file__).resolve().parent / "static" / "index.html"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    old = '<script src="/market/assets/connection-poll81.js?v=8.1-live1" defer></script>'
    tag = '<script src="/market/assets/connection-poll81.js?v=8.1-live2" defer></script>'
    if old in text:
        text = text.replace(old, tag)
    elif tag not in text and "</body>" in text:
        text = text.replace("</body>", f"{tag}\n</body>", 1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    # Apply the user-requested clean overnight scorecard before the inherited
    # supervisor creates the next run and starts the 8.1 strategy engine.
    reset_counts = _reset_active_replay_progress_81()

    # server_80 still owns the proven dashboard/API/UI setup. Replace only its
    # engine promotion hook so the same supervisor launches Operation 8.1, then
    # add the 8.1 candidate-to-fill conversion microscope alongside it.
    base._promote_engine_80 = _promote_engine_81
    _install_conversion_api_81()
    _install_connection_fallback_81()
    print(
        "Operation 8.1 supervisor: Operation 8.0 dashboard + Gold Execution Conversion engine; "
        "first-touch zones, registration-time entry life, dynamic R:R, $750/$500 eval sizing, "
        "detected->qualified->selected->registered->filled conversion telemetry, and Vercel-safe "
        "full snapshot rendering fallback enabled; "
        f"overnight_reset_rows={sum(reset_counts.values())}.",
        flush=True,
    )
    base.main()


if __name__ == "__main__":
    main()
