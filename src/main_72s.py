from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from src import main_72r as op72r
from src.execution.live.config import ExecutionConfig
from src.storage.database import get_connection


runtime = op72r.runtime
VERIFY_MODES_72S = {"VERIFY", "VERIFICATION", "TEST"}


def _verification_enabled_72s() -> bool:
    return os.getenv("OTR_TRADING_MODE", "").strip().upper() in VERIFY_MODES_72S


def _install_verify_trade_tag_trigger_72s() -> None:
    """Make VERIFY run tagging a database invariant instead of a Python wrapper.

    Older operations wrapped runtime.upsert_paper_trade(), but several inherited
    execution paths retain direct references to the original persistence helper.
    Those rows reached paper_trades without reaching verify_run_trades, splitting
    Overview stats from Trade History. A SQLite trigger catches every insert or
    update regardless of which inherited path wrote the trade.
    """
    run_id = os.getenv("OTR_VERIFY_RUN_ID", "").strip()
    if not _verification_enabled_72s() or not run_id:
        return

    connection = get_connection()
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS verify_run_trades (
                run_id TEXT NOT NULL,
                setup_id TEXT NOT NULL,
                build TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                PRIMARY KEY (run_id, setup_id)
            );
            CREATE INDEX IF NOT EXISTS idx_verify_run_trades_run
            ON verify_run_trades(run_id);

            CREATE TABLE IF NOT EXISTS verify_active_run_72s (
                slot INTEGER PRIMARY KEY CHECK(slot = 1),
                run_id TEXT NOT NULL,
                build TEXT NOT NULL,
                activated_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO verify_active_run_72s(slot, run_id, build, activated_at)
            VALUES (1, ?, '7.2S', ?)
            ON CONFLICT(slot) DO UPDATE SET
                run_id=excluded.run_id,
                build=excluded.build,
                activated_at=excluded.activated_at
            """,
            (run_id, datetime.now(timezone.utc).isoformat()),
        )

        connection.executescript(
            """
            DROP TRIGGER IF EXISTS verify_tag_trade_insert_72s;
            DROP TRIGGER IF EXISTS verify_tag_trade_update_72s;

            CREATE TRIGGER verify_tag_trade_insert_72s
            AFTER INSERT ON paper_trades
            BEGIN
                INSERT OR IGNORE INTO verify_run_trades(run_id, setup_id, build, first_seen_at)
                SELECT run_id, NEW.setup_id, build, COALESCE(NEW.updated_at, datetime('now'))
                FROM verify_active_run_72s WHERE slot = 1;
            END;

            CREATE TRIGGER verify_tag_trade_update_72s
            AFTER UPDATE ON paper_trades
            BEGIN
                INSERT OR IGNORE INTO verify_run_trades(run_id, setup_id, build, first_seen_at)
                SELECT run_id, NEW.setup_id, build, COALESCE(NEW.updated_at, datetime('now'))
                FROM verify_active_run_72s WHERE slot = 1;
            END;
            """
        )

        # A changed VERIFY wipe token means paper_trades was intentionally
        # cleared before this process started. Therefore every remaining row is
        # part of the current test and can be safely backfilled into its stable
        # test run. This repairs rows written through legacy direct references.
        if os.getenv("OTR_VERIFY_WIPE_TOKEN", "").strip():
            connection.execute(
                """
                INSERT OR IGNORE INTO verify_run_trades(run_id, setup_id, build, first_seen_at)
                SELECT a.run_id, p.setup_id, a.build, COALESCE(p.updated_at, datetime('now'))
                FROM paper_trades p
                CROSS JOIN verify_active_run_72s a
                WHERE a.slot = 1
                """
            )

        connection.commit()
        tagged = connection.execute(
            "SELECT COUNT(*) FROM verify_run_trades WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        runtime.console.log(
            "Operation 7.2S ledger tagging active: database trigger owns VERIFY membership; "
            f"run_id={run_id}; tagged_rows={int(tagged[0] if tagged else 0)}."
        )
    finally:
        connection.close()


if __name__ == "__main__":
    op72r.op72q.op72.op71._patch_runtime_manifest_71()
    op72r.op72q.op72._patch_runtime_manifest_72()
    _install_verify_trade_tag_trigger_72s()
    config = ExecutionConfig.from_env()
    runtime.console.log(
        "Operation 7.2S active: 7.2R Gold momentum recognition + 7.2Q quality firewall retained; "
        "VERIFY run membership is now database-authoritative so every persistence path is logged consistently; "
        f"verify_run_id={os.getenv('OTR_VERIFY_RUN_ID', 'none')}; broker gateway mode={config.mode.value}."
    )
    op72r.op72q.op72.op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
