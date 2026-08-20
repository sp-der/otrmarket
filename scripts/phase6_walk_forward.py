#!/usr/bin/env python3
"""Run the preregistered Phase 6 pending-lifetime study (research only)."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from src.research.execution.account import reference_account_profile
from src.research.experiments.engine import BASELINE_PENDING_LIFETIMES, behavior_metrics
from src.research.phase6.engine import aggregate_walk_forward_metrics,LIMITATIONS, concentration, digest, enforce_single_thesis, preregister_walk_forward, robust_metrics, robustness_verdict, segment_metrics
from src.research.phase6.store import Phase6Store
from src.research.replay.runner import ReplayRunner
from src.research.replay.runs import ReplayRunStore, RunManifest

CAPTURE="databento-glbx-20260501-20260818-v1"
CAPTURE_DIGEST="1892dfb3558ed71685bd86e26416c59fc4e92716373616b178cc7571b0d15afc"
GIT_SHA="06bc24c94ebe9f463f478c9123dde4ed9e0a3e35"
CONTRACTS=("MNQ JUN26","MNQ SEP26","ES JUN26","ES SEP26","GC JUN26","GC AUG26","GC DEC26")
CANDIDATES={
 "BASELINE_15_8_4_2":{"1m":15,"5m":8,"15m":4,"1h":2},
 "PENDING_LIFETIME_A":{"1m":18,"5m":12,"15m":8,"1h":5},
 "EXTEND_1M_ONLY":{"1m":20,"5m":8,"15m":4,"1h":2},
 "EXTEND_5M_ONLY":{"1m":15,"5m":14,"15m":4,"1h":2},
 "EXTEND_HTF_ONLY":{"1m":15,"5m":8,"15m":8,"1h":5},
}

def run_one(work: Path, candidate: str, fold: dict, role: str, historical: Path, *, execution_overrides: dict | None = None, ambiguity_policy: str = "STOP_FIRST", run_label: str | None = None):
    start,end=(fold["oos_start"],fold["oos_end"]) if role=="OOS" else (fold["is_start"],fold["is_end"])
    run_id=f"phase6-pending-lifetime-v1-{candidate.lower()}-{fold['fold_id'].lower()}-{role.lower()}"+(f"-{run_label}" if run_label else "")
    run_database=work/f"{run_id}.db"
    run_store=ReplayRunStore(run_database,work/"runs")
    account=reference_account_profile({"profile_verification":"RESEARCH_REFERENCE_PROFILE"})
    manifest=RunManifest(run_id=run_id,git_commit=GIT_SHA,capture_id=CAPTURE,markets=("NQ","ES","GC"),contracts=CONTRACTS,
      start_time=start,end_time=end,enabled_timeframes=("1m","5m","15m","1h"),
      configuration={"OTR_EXECUTION_TIMEFRAME":"ALL","phase6_limitations":LIMITATIONS,"roll_selector_version":"PREVIOUS_UTC_DAY_VOLUME_V1","random_seed":None},
      account_profile=account,replay_mode="CANDLE_APPROXIMATE",fill_model="SLIPPAGE_MODEL",ambiguity_policy=ambiguity_policy,
      execution_config={**{"slippage_ticks":1,"round_turn_commission":2.50,"round_turn_fees":.50},**(execution_overrides or {})},pending_lifetime_bars=CANDIDATES[candidate],created_at="2026-08-20T00:00:00+00:00")
    existing=False
    if run_database.exists():
        with sqlite3.connect(run_database) as c:
            existing=bool(c.execute("SELECT 1 FROM backtest_runs WHERE run_id=? AND status='COMPLETE'",(run_id,)).fetchone())
    if existing:
        with sqlite3.connect(run_database) as c:
            c.row_factory=sqlite3.Row
            trades=[json.loads(x[0]) for x in c.execute("SELECT payload_json FROM research_trades WHERE run_id=? ORDER BY signal_time,trade_id",(run_id,))]
            equity=[dict(x) for x in c.execute("SELECT * FROM equity_curve WHERE run_id=? ORDER BY sequence_no",(run_id,))]
            traces=[json.loads(x[0]) for x in c.execute("SELECT payload_json FROM decision_traces WHERE run_id=? ORDER BY sequence_no",(run_id,))]
            decision=c.execute("SELECT decision_digest FROM backtest_runs WHERE run_id=?",(run_id,)).fetchone()[0]
        result={"execution_trades":trades,"equity":equity,"traces":traces,"decision_digest":decision,
                "execution_digest":digest(trades),"equity_digest":digest(equity)}
    else:
        run_store.initialize()
        result=ReplayRunner(run_store,ROOT).run(manifest,historical)
    trades=[]
    for item in result.get("execution_trades",[]):
        row={**item,"fold_id":fold["fold_id"]}; trades.append(row)
    metrics=robust_metrics(trades,result.get("equity",[])); segments=segment_metrics(trades)
    behavior=behavior_metrics(result.get("traces",[]),trades)
    digests={"decision_digest":result["decision_digest"],"execution_digest":result["execution_digest"],
             "equity_digest":result["equity_digest"]}
    # ReplayRunner has already persisted immutable traces/trades. Avoid retaining
    # the worker's very large transport JSON in a multi-run study.
    result_file=work/"runs"/run_id/"result.json"
    if result_file.exists(): result_file.unlink()
    return {"run_id":run_id,"candidate":candidate,"fold_id":fold["fold_id"],"role":role,"manifest":asdict(manifest),
      "digests":digests,"trades":trades,"metrics":metrics,"segments":segments,"behavior":behavior}

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--historical-db",default="data/otr_historical.db");parser.add_argument("--database",default="data/otr_phase6.db");parser.add_argument("--work-dir",default="data/phase6-study-work");parser.add_argument("--workers",type=int,default=4);args=parser.parse_args()
    historical=Path(args.historical_db).resolve(); work=Path(args.work_dir).resolve();work.mkdir(parents=True,exist_ok=True)
    wf=preregister_walk_forward("2026-05-01T00:00:00+00:00","2026-08-19T00:00:00+00:00")
    baseline={"pending_lifetime_bars":BASELINE_PENDING_LIFETIMES}
    candidate_rows=[]
    for name,lifetimes in CANDIDATES.items():
        config={"pending_lifetime_bars":lifetimes};diff=enforce_single_thesis(baseline,config)
        candidate_rows.append({"candidate_id":name,"name":name,"hypothesis":"Bounded pending-lifetime sensitivity",
          "configuration":config,"configuration_diff":diff,"definition_digest":digest({"name":name,"configuration":config})})
    definition={"study_id":"phase6-pending-lifetime-v1","hypothesis":"Longer pending lifetimes may preserve structurally valid retracements without resurrecting stale setups.",
      "capture_id":CAPTURE,"capture_digest":CAPTURE_DIGEST,"git_commit":GIT_SHA,"start_time":"2026-05-01T00:00:00+00:00","end_time":"2026-08-19T00:00:00+00:00",
      "replay_mode":"CANDLE_APPROXIMATE","limitations":LIMITATIONS,"preregistration":wf,"created_at":"2026-08-20T00:00:00+00:00"}
    store=Phase6Store(args.database);store.initialize();store.create_study(definition,candidate_rows,wf["folds"])
    jobs=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for name in CANDIDATES:
            for fold in wf["folds"]: jobs.append(pool.submit(run_one,work,name,fold,"OOS",historical))
        runs=[future.result() for future in as_completed(jobs)]
    summary={"study_id":definition["study_id"],"walk_forward":wf,"candidates":{}}
    for name in CANDIDATES:
        selected=sorted([x for x in runs if x["candidate"]==name],key=lambda x:x["fold_id"])
        trades=[t for run in selected for t in run["trades"]]; fold_metrics=[x["metrics"] for x in selected]; segments=segment_metrics(trades)
        verdict=robustness_verdict(fold_metrics,segments)
        payload={"fold_metrics":fold_metrics,"aggregate_metrics":aggregate_walk_forward_metrics(trades,fold_metrics),"segments":segments,
                 "concentration":concentration(trades),"verdict":verdict}
        for run in selected:
            store.append_run({"run_id":run["run_id"],"study_id":definition["study_id"],"candidate_id":name,"fold_id":run["fold_id"],"sample_role":"OOS","manifest":run["manifest"],"metrics":run["metrics"],"segments":run["segments"],"behavior":run["behavior"],"run_digest":digest(run["manifest"]),"decision_digest":run["digests"]["decision_digest"],"trade_digest":run["digests"]["execution_digest"],"status":"COMPLETE"})
        store.append_result(definition["study_id"],name,"WALK_FORWARD_OOS",payload);store.verdict(definition["study_id"],name,verdict)
        summary["candidates"][name]=payload
    # Firewall: no holdout replay is launched unless OOS produces an eligible finalist.
    finalists=[name for name in CANDIDATES if name!="BASELINE_15_8_4_2" and summary["candidates"][name]["verdict"]["verdict"]=="ADVANCE_TO_FINAL_HOLDOUT"]
    summary["finalists"]=finalists;summary["holdout_status"]="UNTOUCHED" if not finalists else "REQUIRED_NOT_RUN_BY_THIS_COMMAND"
    summary["digest"]=digest(summary)
    print(json.dumps(summary,indent=2,sort_keys=True))

if __name__=="__main__":main()
