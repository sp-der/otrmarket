#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.replay.runner import ReplayRunner
from src.research.replay.runs import ReplayRunStore, RunManifest


def main():
    parser = argparse.ArgumentParser(description="Research-only Operation 7.0 futures replay")
    parser.add_argument("--historical-db", default="data/otr_historical.db")
    parser.add_argument("--run-db", default="data/otr_backtests.db")
    parser.add_argument("--runs-dir", default="data/backtest-runs")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--capture-id", required=True)
    parser.add_argument("--contracts", nargs="+", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--mode", choices=("TICK_EXACT","CANDLE_APPROXIMATE"), required=True)
    parser.add_argument("--timeframes", nargs="+", default=["1m","5m","15m","30m","1h"])
    parser.add_argument("--fill-model", choices=("IDEAL_TOUCH","BID_ASK","SLIPPAGE_MODEL"), default="SLIPPAGE_MODEL")
    parser.add_argument("--slippage-ticks", type=int, default=1)
    parser.add_argument("--round-turn-commission", type=float, default=2.50)
    parser.add_argument("--round-turn-fees", type=float, default=0.50)
    parser.add_argument("--ambiguity-policy", choices=("STOP_FIRST","TARGET_FIRST","AMBIGUOUS_SKIP"), default="STOP_FIRST")
    parser.add_argument("--simulate-execution", action="store_true")
    parser.add_argument("--pending-lifetime", action="append", default=[], metavar="TF=BARS")
    args = parser.parse_args()
    pending_lifetimes={"1m":15,"5m":8,"15m":4,"1h":2}
    for item in args.pending_lifetime:
        timeframe,bars=item.split("=",1)
        if timeframe not in pending_lifetimes or int(bars)<1: raise ValueError(f"Invalid pending lifetime: {item}")
        pending_lifetimes[timeframe]=int(bars)
    roots = tuple(sorted({"NQ" if c.split()[0] in {"NQ","MNQ"} else "ES" if c.split()[0] in {"ES","MES"} else "GC" for c in args.contracts}))
    store = ReplayRunStore(args.run_db, args.runs_dir)
    store.initialize()
    manifest = RunManifest(
        run_id=args.run_id, git_commit="62ee79bf441e2da26787052d00a92544c5605b17",
        capture_id=args.capture_id, markets=roots, contracts=tuple(args.contracts),
        start_time=args.start, end_time=args.end, enabled_timeframes=tuple(args.timeframes),
        configuration={"OTR_EXECUTION_TIMEFRAME":"ALL"},
        account_profile={"profile_verification":"RESEARCH_REFERENCE_PROFILE"}, engine_version="Operation 7.0",
        replay_mode=args.mode, fill_model=args.fill_model, ambiguity_policy=args.ambiguity_policy,
        execution_config={"slippage_ticks":args.slippage_ticks,"round_turn_commission":args.round_turn_commission,
                          "round_turn_fees":args.round_turn_fees},
        pending_lifetime_bars=pending_lifetimes,
    )
    result = ReplayRunner(store, ROOT).run(manifest, args.historical_db)
    print(f"run_id: {args.run_id}")
    print(f"mode: {args.mode}")
    print(f"traces: {result['trace_count']}")
    print(f"decision_digest: {result['decision_digest']}")
    print(f"ledger: {result['ledger_path']}")
    if args.simulate_execution:
        print("label: NOT VALID FOR STRATEGY EVALUATION")
        print(f"research_trades: {len(result.get('execution_trades',[]))}")
        print(f"equity_points: {len(result.get('equity',[]))}")
        print(f"metrics: {result.get('metrics',{})}")
        print(f"execution_digest: {result.get('execution_digest')}")
        print(f"equity_digest: {result.get('equity_digest')}")


if __name__ == "__main__":
    main()
