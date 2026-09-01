from __future__ import annotations

import asyncio
import os

from src import main_72o as op72o
from src.execution.live.config import ExecutionConfig
from src.strategies.gold_verify_guard72q import assess_gold_1m_verify


runtime = op72o.runtime
op72 = op72o.op72
_previous_quality_gate_72q = op72.op71.op70.op59.op58._adaptive_quality_gate
_original_upsert_paper_trade_72q = runtime.upsert_paper_trade


def _adaptive_quality_gate_72q(connection, setup, histories=None):
    allowed, reason = _previous_quality_gate_72q(connection, setup, histories)
    if not allowed:
        return False, reason

    if histories is None:
        histories = runtime.histories_snapshot()
    firewall_allowed, firewall_reason, details = assess_gold_1m_verify(
        setup,
        histories,
        os.getenv("OTR_TRADING_MODE", ""),
    )
    if not firewall_allowed:
        setup.metadata.setdefault("gold_verify_guard_72q", {}).update(details)
        return False, firewall_reason

    if details:
        setup.metadata.setdefault("gold_verify_guard_72q", {}).update(details)
    return True, f"{reason} {firewall_reason}"


def _tag_verify_trade_72q(connection, position, updated_at) -> None:
    mode = os.getenv("OTR_TRADING_MODE", "").strip().upper()
    run_id = os.getenv("OTR_VERIFY_RUN_ID", "").strip()
    if mode not in {"VERIFY", "VERIFICATION", "TEST"} or not run_id:
        return

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS verify_run_trades (
            run_id TEXT NOT NULL,
            setup_id TEXT NOT NULL,
            build TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY (run_id, setup_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_verify_run_trades_run ON verify_run_trades(run_id)"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO verify_run_trades(run_id, setup_id, build, first_seen_at)
        VALUES (?, ?, '7.2Q', ?)
        """,
        (run_id, position.setup.setup_id, str(updated_at)),
    )
    connection.commit()


def _upsert_paper_trade_72q(connection, position, updated_at):
    _original_upsert_paper_trade_72q(connection, position, updated_at)
    _tag_verify_trade_72q(connection, position, updated_at)


# Final wrappers consumed by normal candle evaluation and intrabar promotion.
op72.op71.op70.op59.op58._adaptive_quality_gate = _adaptive_quality_gate_72q
runtime.upsert_paper_trade = _upsert_paper_trade_72q


if __name__ == "__main__":
    op72.op71._patch_runtime_manifest_71()
    op72._patch_runtime_manifest_72()
    config = ExecutionConfig.from_env()
    runtime.console.log(
        "Operation 7.2Q active: Gold 1m quality firewall requires the original 5m ICT contract after all salvage/recovery wrappers; "
        "continuation re-arms must still align with current 5m structure; dedicated 7.2 MSS reversals remain intact; "
        f"VERIFY fixed-risk sizing preserved; verify_run_id={os.getenv('OTR_VERIFY_RUN_ID', 'none')}; broker gateway mode={config.mode.value}."
    )
    op72.op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
