#!/usr/bin/env python3
"""Phase 5 plumbing demo. Incomplete retained data is never strategy evidence."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.execution.account import reference_account_profile
from src.research.experiments import (
    BASELINE_PENDING_LIFETIMES, CANDIDATE_A_PENDING_LIFETIMES,
    ExperimentEngine, ExperimentSpec, ExperimentStore, PairedReplayExecutor,
)
from src.research.experiments.engine import CandidateSpec
from src.research.replay.runner import ReplayRunner
from src.research.replay.runs import ReplayRunStore, RunManifest


COMMIT = "9ac13de0590edb2ffca4416dda86cb01c260099f"


def main() -> None:
    parser = argparse.ArgumentParser(description="Pending Lifetime Research v1 plumbing demo")
    parser.add_argument("--database", default="data/phase5-pending-demo.db")
    parser.add_argument("--runs-dir", default="data/phase5-pending-demo-runs")
    parser.add_argument("--historical-db", default="data/otr_historical.db")
    args = parser.parse_args()

    run_store = ReplayRunStore(args.database, args.runs_dir)
    run_store.initialize()
    account = reference_account_profile({"profile_verification": "RESEARCH_REFERENCE_PROFILE"})
    baseline = RunManifest(
        run_id="pending-lifetime-v1-baseline", git_commit=COMMIT,
        capture_id="retained-operation70-phase1", markets=("ES", "GC", "NQ"),
        contracts=("MNQ SEP26", "ES SEP26", "GC DEC26"),
        start_time="2026-08-13T04:00:00+00:00", end_time="2026-08-13T07:00:00+00:00",
        enabled_timeframes=("1m", "5m", "15m", "30m", "1h"),
        configuration={"OTR_EXECUTION_TIMEFRAME": "ALL"}, account_profile=account,
        replay_mode="CANDLE_APPROXIMATE", fill_model="SLIPPAGE_MODEL", ambiguity_policy="STOP_FIRST",
        execution_config={"slippage_ticks": 1, "round_turn_commission": 2.5, "round_turn_fees": .5},
        pending_lifetime_bars=BASELINE_PENDING_LIFETIMES,
    )
    result = PairedReplayExecutor(ReplayRunner(run_store, ROOT)).run(
        experiment_id="pending-lifetime-research-v1", candidate_id="candidate-a",
        baseline_manifest=baseline, candidate_run_id="pending-lifetime-v1-candidate-a",
        candidate_lifetimes=CANDIDATE_A_PENDING_LIFETIMES,
        historical_database=args.historical_db, data_quality_status="INCOMPLETE",
    )

    store = ExperimentStore(args.database)
    store.initialize()
    engine = ExperimentEngine(store)
    baseline_configuration = {**baseline.configuration, "pending_lifetime_bars": BASELINE_PENDING_LIFETIMES}
    definition = engine.define(
        ExperimentSpec(
            experiment_id="pending-lifetime-research-v1", experiment_name="Pending Lifetime Research v1",
            hypothesis="Operation 7.0 may expire higher-timeframe pending setups before structurally valid retracements occur.",
            baseline_run_id=baseline.run_id, git_commit=COMMIT, data_capture_id=baseline.capture_id,
            markets=baseline.markets, contracts=baseline.contracts, start_time=baseline.start_time, end_time=baseline.end_time,
            replay_mode=baseline.replay_mode, fill_model=baseline.fill_model, ambiguity_policy=baseline.ambiguity_policy,
            account_profile=account, execution_config=baseline.execution_config,
            baseline_configuration=baseline_configuration, status="COMPLETE",
        ),
        [CandidateSpec(
            candidate_id="candidate-a", candidate_name="Candidate A",
            run_id="pending-lifetime-v1-candidate-a",
            configuration={**baseline.configuration, "pending_lifetime_bars": CANDIDATE_A_PENDING_LIFETIMES},
        )],
    )
    comparison_id = engine.persist_comparison(result)
    print("label: NOT VALID FOR STRATEGY EVALUATION")
    print(f"experiment_id: {definition['definition']['experiment_id']}")
    print(f"comparison_id: {comparison_id}")
    print(f"verdict: {result['verdict']['verdict']}")
    print(f"definition_digest: {definition['definition']['definition_digest']}")
    print(f"comparison_digest: {result['digests']['comparison']}")
    for timeframe, row in result["timeframe_analysis"].items():
        print(f"{timeframe}: {row['baseline_lifetime']} -> {row['candidate_lifetime']} bars; kept_alive={row['baseline_expired_setups_kept_alive']}; later_fills={row['later_fills']}")


if __name__ == "__main__":
    main()
