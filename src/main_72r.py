from __future__ import annotations

import asyncio
import os

from src import main_72q as op72q
from src.execution.live.config import ExecutionConfig
from src.strategies.adaptive_manager import AdaptiveStrategyEngine
from src.strategies.gold_momentum72r import GoldMomentumPullbackEngine72R


runtime = op72q.runtime
VERIFY_MODES_72R = {"VERIFY", "VERIFICATION", "TEST"}

_original_on_candle_all_72r = AdaptiveStrategyEngine.on_candle_all
_original_clear_symbol_72r = AdaptiveStrategyEngine.clear_symbol


def _momentum_engine_72r(engine: AdaptiveStrategyEngine) -> GoldMomentumPullbackEngine72R:
    momentum = getattr(engine, "_gold_momentum_pullback_72r", None)
    if momentum is None:
        momentum = GoldMomentumPullbackEngine72R()
        setattr(engine, "_gold_momentum_pullback_72r", momentum)
    return momentum


def _on_candle_all_72r(self, symbol: str, timeframe: str, histories):
    candidates = list(_original_on_candle_all_72r(self, symbol, timeframe, histories))
    if candidates:
        return candidates

    mode = os.getenv("OTR_TRADING_MODE", "").strip().upper()
    if mode not in VERIFY_MODES_72R or symbol != "GC" or timeframe not in {"5m", "15m"}:
        return candidates

    momentum = _momentum_engine_72r(self)
    setup = momentum.on_candle(symbol, timeframe, histories, mode)
    diagnostic = momentum.diagnostic(symbol, timeframe)
    if diagnostic and diagnostic.get("stage") in {"WAIT_ENTRY_FVG", "WAIT_PULLBACK", "SETUP_READY"}:
        self.diagnostics[(symbol, timeframe)] = diagnostic

    if setup is None:
        return candidates

    self.last_setup = setup
    return [setup]


def _clear_symbol_72r(self, symbol: str) -> None:
    _original_clear_symbol_72r(self, symbol)
    momentum = getattr(self, "_gold_momentum_pullback_72r", None)
    if momentum is not None:
        momentum.clear_symbol(symbol)


if not getattr(AdaptiveStrategyEngine, "_otr_72r_momentum_installed", False):
    AdaptiveStrategyEngine.on_candle_all = _on_candle_all_72r
    AdaptiveStrategyEngine.clear_symbol = _clear_symbol_72r
    AdaptiveStrategyEngine._otr_72r_momentum_installed = True


if __name__ == "__main__":
    op72q.op72.op71._patch_runtime_manifest_71()
    op72q.op72._patch_runtime_manifest_72()
    config = ExecutionConfig.from_env()
    runtime.console.log(
        "Operation 7.2R active: 7.2Q Gold 1m quality firewall remains intact; "
        "new GC 5m/15m momentum-recognition lane catches strong aligned sweep/MSS + displacement legs "
        "that miss the legacy PD-array-first trigger, then waits for a fresh FVG/OTE first pullback; "
        "no impulse chasing, normal quality/cooldown/active-position/geometry gates still mandatory; "
        f"verify_run_id={os.getenv('OTR_VERIFY_RUN_ID', 'none')}; broker gateway mode={config.mode.value}."
    )
    op72q.op72.op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
