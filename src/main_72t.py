from __future__ import annotations

import asyncio
import os

from src import main_72s as op72s
from src.execution.live.config import ExecutionConfig
from src.research.live_training72t import backfill_4h_candles_72t, install_training_capture_72t
from src.strategies import candles as candle_module
from src.strategies import execution_quality
from src.strategies import market_intelligence


runtime = op72s.runtime
_original_evaluate_strategy_72t = runtime.evaluate_strategy
_original_load_recent_candles_72t = runtime.load_recent_candles


def _install_4h_context_72t() -> None:
    """Add 4H Gold macro context without creating a 4H execution lane."""
    candle_module.TIMEFRAME_SECONDS["4h"] = 14_400
    if "4h" not in runtime.candles.timeframes:
        runtime.candles.timeframes = tuple(runtime.candles.timeframes) + ("4h",)

    # Market-map research can see the 4H structure. Existing execution grades
    # are intentionally not hard-blocked by it yet; 4H begins as context/evidence.
    if "4h" not in market_intelligence.CONTEXT_TIMEFRAMES:
        market_intelligence.CONTEXT_TIMEFRAMES = tuple(market_intelligence.CONTEXT_TIMEFRAMES) + ("4h",)
    execution_quality.BAR_SECONDS["4h"] = 14_400

    def load_with_4h(connection, symbols, timeframes, limit_per_series=500):
        requested = tuple(dict.fromkeys(tuple(timeframes) + ("4h",)))
        return _original_load_recent_candles_72t(
            connection,
            symbols=symbols,
            timeframes=requested,
            limit_per_series=limit_per_series,
        )

    def evaluate_without_4h_execution(connection, symbol: str, timeframe: str):
        if str(timeframe).lower() == "4h":
            return None
        return _original_evaluate_strategy_72t(connection, symbol, timeframe)

    runtime.load_recent_candles = load_with_4h
    runtime.evaluate_strategy = evaluate_without_4h_execution


def main() -> None:
    op72s.op72r.op72q.op72.op71._patch_runtime_manifest_71()
    op72s.op72r.op72q.op72._patch_runtime_manifest_72()

    # Keep 7.2S's database-authoritative VERIFY ledger first. Training capture
    # uses the same stable run marker but writes to durable research tables that
    # are deliberately not part of a replay scoreboard wipe.
    op72s._install_verify_trade_tag_trigger_72s()
    derived = backfill_4h_candles_72t()
    _install_4h_context_72t()
    capture = install_training_capture_72t()

    config = ExecutionConfig.from_env()
    runtime.console.log(
        "Operation 7.2T active: 7.2S ledger + 7.2R Gold momentum + 7.2Q 1m firewall retained; "
        "4H candles are macro direction/research context only and cannot execute trades; "
        "Strategy Lab training corpus captures decisions, actual outcomes, counterfactuals, missed moves and shadow evidence; "
        f"4h_backfill_rows={derived}; training_run={capture.get('run_id') or os.getenv('OTR_VERIFY_RUN_ID', 'none')}; "
        f"broker gateway mode={config.mode.value}."
    )
    op72s.op72r.op72q.op72.op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")


if __name__ == "__main__":
    main()
