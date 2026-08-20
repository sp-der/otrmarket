#!/usr/bin/env python3
"""Resume Phase 6 from retained completed runs without rerunning OOS."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import json
from pathlib import Path
import sqlite3
from bisect import bisect_right
from datetime import datetime
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from src.research.phase6.analysis import divergence_audit,load_run
from src.research.phase6.store import Phase6Store
from src.research.phase6.engine import aggregate_walk_forward_metrics,causal_regimes,concentration,robust_metrics,segment_metrics,digest
from src.research.execution.account import PropConfig
from scripts.phase6_walk_forward import CANDIDATES,run_one

STUDY="phase6-pending-lifetime-v1"
AUDIT_CANDIDATES=("PENDING_LIFETIME_A","EXTEND_1M_ONLY","EXTEND_5M_ONLY","EXTEND_HTF_ONLY")

def run_path(work: Path,candidate: str,fold: int)->Path:
    return work/f"{STUDY}-{candidate.lower()}-fold_{fold}-oos.db"

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--database",default="data/otr_phase6_results.db");parser.add_argument("--work-dir",default="data/phase6-study-work-v2");parser.add_argument("--run-is",action="store_true");parser.add_argument("--posthoc",action="store_true");parser.add_argument("--run-robustness",action="store_true");parser.add_argument("--finalize",action="store_true");parser.add_argument("--is-work-dir",default="data/phase6-is-work");parser.add_argument("--robustness-work-dir",default="data/phase6-robustness-work");parser.add_argument("--historical-db",default="data/otr_historical.db");parser.add_argument("--workers",type=int,default=4);args=parser.parse_args()
    if args.run_is:
        run_is(Path(args.database),Path(args.is_work_dir),Path(args.historical_db),args.workers);return
    if args.posthoc:
        posthoc(Path(args.database),Path(args.work_dir),Path(args.historical_db));return
    if args.run_robustness:
        run_robustness(Path(args.database),Path(args.robustness_work_dir),Path(args.historical_db),args.workers);return
    if args.finalize:
        finalize(Path(args.database));return
    work=Path(args.work_dir);folds={}
    for fold in range(1,5):folds[fold]=load_run(run_path(work,"BASELINE_15_8_4_2",fold))
    payload={"artifact_authority":{"authoritative_database":str(Path(args.database)),"authoritative_workspace":str(work),"excluded_interrupted_workspace":"data/phase6-study-work/"},"folds":{},"summary":{}}
    for candidate in AUDIT_CANDIDATES:
        audits=[]
        for fold in range(1,5):audits.append(divergence_audit(folds[fold],load_run(run_path(work,candidate,fold)),candidate))
        payload["folds"][candidate]=audits
        payload["summary"][candidate]={key:sum(int(x[key]) for x in audits) for key in ("later_fills","later_winners","later_losers","trade_identity_divergences","decision_level_divergences")}
        payload["summary"][candidate]["normalized_trade_equivalence"]=all(x["normalized_trade_equivalence"] for x in audits)
        payload["summary"][candidate]["normalized_decision_equivalence"]=all(x["normalized_decision_equivalence"] for x in audits)
        payload["summary"][candidate]["economically_meaningful_divergences"]=any(x["economically_meaningful_divergences"] for x in audits)
    store=Phase6Store(args.database);store.initialize();store.study_result(STUDY,"PENDING_LIFETIME_DIVERGENCE_AUDIT",payload)
    print(payload)

def run_is(database:Path,work:Path,historical:Path,workers:int):
    connection=sqlite3.connect(f"file:{database.resolve()}?mode=ro",uri=True);connection.row_factory=sqlite3.Row
    folds=[dict(x) for x in connection.execute("SELECT * FROM phase6_folds WHERE study_id=? ORDER BY fold_id",(STUDY,))];connection.close()
    work.mkdir(parents=True,exist_ok=True);jobs=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for candidate in CANDIDATES:
            for fold in folds:jobs.append(pool.submit(run_one,work,candidate,fold,"IS",historical.resolve()))
        runs=[future.result() for future in as_completed(jobs)]
    store=Phase6Store(database);store.initialize()
    summary={}
    for candidate in CANDIDATES:
        selected=sorted((x for x in runs if x["candidate"]==candidate),key=lambda x:x["fold_id"])
        trades=[trade for run in selected for trade in run["trades"]]
        payload={"fold_metrics":[x["metrics"] for x in selected],"aggregate_metrics":aggregate_walk_forward_metrics(trades,[x["metrics"] for x in selected]),
          "segments":segment_metrics(trades),"concentration":concentration(trades)}
        with sqlite3.connect(database) as c:
            existing={x[0] for x in c.execute("SELECT run_id FROM phase6_runs")}
        for run in selected:
            if run["run_id"] not in existing:
                store.append_run({"run_id":run["run_id"],"study_id":STUDY,"candidate_id":candidate,"fold_id":run["fold_id"],"sample_role":"IS","manifest":run["manifest"],"metrics":run["metrics"],"segments":run["segments"],"behavior":run["behavior"],"run_digest":digest(run["manifest"]),"decision_digest":run["digests"]["decision_digest"],"trade_digest":run["digests"]["execution_digest"],"status":"COMPLETE"})
        with sqlite3.connect(database) as c:
            exists=c.execute("SELECT 1 FROM phase6_results WHERE study_id=? AND candidate_id=? AND result_type='WALK_FORWARD_IS'",(STUDY,candidate)).fetchone()
        if not exists:store.append_result(STUDY,candidate,"WALK_FORWARD_IS",payload)
        summary[candidate]=payload["aggregate_metrics"]
    print(json.dumps(summary,sort_keys=True,indent=2))

def _put_study_result(store:Phase6Store,database:Path,result_type:str,payload:dict):
    with sqlite3.connect(database) as c:exists=c.execute("SELECT 1 FROM phase6_study_results WHERE study_id=? AND result_type=?",(STUDY,result_type)).fetchone()
    if not exists:store.study_result(STUDY,result_type,payload)

def posthoc(database:Path,work:Path,historical:Path):
    baseline=[load_run(run_path(work,"BASELINE_15_8_4_2",fold)) for fold in range(1,5)]
    trades=[]
    for fold,run in enumerate(baseline,1):trades.extend({**x,"fold_id":f"FOLD_{fold}"} for x in run["trades"])
    closed=sorted((x for x in trades if x.get("status")=="CLOSED"),key=lambda x:x.get("exit_time") or "")
    # Labels use the 20 bars strictly preceding each current bar.
    hc=sqlite3.connect(f"file:{historical.resolve()}?mode=ro",uri=True);hc.row_factory=sqlite3.Row
    labels={}
    for root in ("NQ","ES","GC"):
        bars=[dict(x) for x in hc.execute("""SELECT cc.open_time,cc.open,cc.high,cc.low,cc.close FROM canonical_candles cc
          JOIN causal_research_series_bars rs ON rs.capture_id=cc.capture_id AND rs.root_symbol=cc.root_symbol
           AND rs.open_time=cc.open_time AND rs.contract=cc.contract
          WHERE cc.capture_id=? AND cc.root_symbol=? AND cc.timeframe='1m' ORDER BY cc.open_time""",("databento-glbx-20260501-20260818-v1",root))]
        rows=causal_regimes(bars);times=[x["timestamp"] for x in rows];labels[root]=(times,rows)
    hc.close();regime_trades=[]
    for trade in closed:
        times,rows=labels[trade["symbol"]];index=bisect_right(times,trade["signal_time"])-1
        regime=rows[index]["regime"] if index>=0 else "UNKNOWN"
        regime_trades.append({**trade,"regime":regime})
    by_regime={}
    for name,predicate in {
      "higher_volatility":lambda x:"HIGH_VOLATILITY" in x["regime"],"lower_volatility":lambda x:"LOW_VOLATILITY" in x["regime"],
      "directional_trending":lambda x:"DIRECTIONAL" in x["regime"],"rotational_ranging":lambda x:"ROTATIONAL" in x["regime"]}.items():
        by_regime[name]=robust_metrics([x for x in regime_trades if predicate(x)],[])
    regime_payload={"classifier":"CAUSAL_ATR_TREND_V1","information_set":"20 strictly prior 1-minute causal-series bars","replay_required":False,"results":by_regime,"unknown":sum(x["regime"]=="UNKNOWN" for x in regime_trades)}
    recovery={"losses_prevented":{"count":None,"status":"NOT_IDENTIFIABLE_WITHOUT_COUNTERFACTUAL_EXECUTION"},"winners_prevented":{"count":None,"status":"NOT_IDENTIFIABLE_WITHOUT_COUNTERFACTUAL_EXECUTION"},"b_plus_blocks":0,"same_symbol_cooldown_effects":0,"portfolio_recovery_effects":0,"reduced_risk_effects":0,"unrelated_symbol_effects":0,"cross_market_penalty_observed":False}
    for run in baseline:
        for trace in run["traces"]:
            value=trace.get("recovery_json")
            state=json.loads(value) if value else {}
            line=" ".join(str(trace.get(k) or "") for k in ("decision","reason")).upper()
            mode=state.get("mode")
            if state.get("quality_grade")=="B+" and "BLOCK" in line and mode in {"SYMBOL_RECOVERY","ACCOUNT_RECOVERY"}:recovery["b_plus_blocks"]+=1
            if mode=="SYMBOL_RECOVERY":recovery["same_symbol_cooldown_effects"]+=1
            if mode=="ACCOUNT_RECOVERY":recovery["portfolio_recovery_effects"]+=1
            if state.get("risk_cap") is not None and float(state["risk_cap"])<1:recovery["reduced_risk_effects"]+=1
            if mode=="NORMAL" and int(state.get("futures_consecutive_losses") or 0)>0 and int(state.get("symbol_losses_today") or 0)==0:recovery["unrelated_symbol_effects"]+=1
            recovery["cross_market_penalty_observed"] |= bool(state.get("cross_market_penalty"))
    pnl=[float(x.get("net_pnl") or 0) for x in closed];balance=50000.0;peak=balance;minimum=balance;max_dd=0;streak=max_streak=0;daily={};target_time=None
    for trade,value in zip(closed,pnl):
        balance+=value;peak=max(peak,balance);minimum=min(minimum,balance);max_dd=max(max_dd,peak-balance);streak=streak+1 if value<0 else 0;max_streak=max(max_streak,streak)
        day=(trade.get("exit_time") or "")[:10];daily[day]=daily.get(day,0)+value
        if target_time is None and balance>=53000:target_time=trade.get("exit_time")
    config=PropConfig();prop={"profile":"RESEARCH_REFERENCE_PROFILE","starting_equity":config.starting_balance,"ending_equity":balance,"max_drawdown":max_dd,"daily_stop_breaches":sum(x<=-config.internal_daily_stop for x in daily.values()),"max_loss_breaches":int(minimum<=config.starting_balance-config.max_loss_limit),"consecutive_losses":max_streak,"simulated_target_reached":target_time is not None,"time_to_target":target_time,"minimum_equity":minimum,"micros_required":max((int(x.get("quantity") or 0) for x in closed),default=0),"max_micros":config.max_micros,"rule_failure_reason":"PROFIT_TARGET_NOT_REACHED" if target_time is None else None,"method":"Sequential retained OOS closed-trade ledger; diagnostic because folds originally reset account state."}
    winners=sorted((float(x.get("net_pnl") or 0) for x in closed),reverse=True);total=sum(pnl)
    concentration_payload={**concentration(closed),"largest_single_trade":{"net_pnl":winners[0] if winners else 0,"share_of_net":winners[0]/total if winners and total else None},"top_3_trades":{"net_pnl":sum(winners[:3]),"share_of_net":sum(winners[:3])/total if total else None},"positive_pnl_excessively_dependent_on_one_trade":bool(total>0 and winners and winners[0]/total>.5)}
    store=Phase6Store(database);store.initialize();_put_study_result(store,database,"CAUSAL_REGIME_ANALYSIS",regime_payload);_put_study_result(store,database,"RECOVERY_EFFECT_ANALYSIS",recovery);_put_study_result(store,database,"PROP_EVAL_SIMULATION",prop);_put_study_result(store,database,"CONCENTRATION_REVIEW",concentration_payload)
    print(json.dumps({"regime":regime_payload,"recovery":recovery,"prop_eval":prop,"concentration":concentration_payload},sort_keys=True,indent=2))

def run_robustness(database:Path,work:Path,historical:Path,workers:int):
    connection=sqlite3.connect(f"file:{database.resolve()}?mode=ro",uri=True);connection.row_factory=sqlite3.Row
    folds=[dict(x) for x in connection.execute("SELECT * FROM phase6_folds WHERE study_id=? ORDER BY fold_id",(STUDY,))];connection.close();work.mkdir(parents=True,exist_ok=True)
    variants=(("BASE_PLUS_1_TICK",{"slippage_ticks":2},"STOP_FIRST","stress_plus_1"),("BASE_PLUS_2_TICKS",{"slippage_ticks":3},"STOP_FIRST","stress_plus_2"),("AMBIGUOUS_SKIP",{},"AMBIGUOUS_SKIP","ambiguous_skip"))
    jobs=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for label,execution,policy,suffix in variants:
            for fold in folds:jobs.append((label,pool.submit(run_one,work,"BASELINE_15_8_4_2",fold,"OOS",historical.resolve(),execution_overrides=execution,ambiguity_policy=policy,run_label=suffix)))
        completed=[(label,future.result()) for label,future in jobs]
    output={};store=Phase6Store(database);store.initialize()
    for label,_,_,_ in variants:
        selected=sorted((run for value,run in completed if value==label),key=lambda x:x["fold_id"]);trades=[x for run in selected for x in run["trades"]]
        output[label]={"fold_metrics":[x["metrics"] for x in selected],"aggregate_metrics":aggregate_walk_forward_metrics(trades,[x["metrics"] for x in selected]),"segments":segment_metrics(trades),"concentration":concentration(trades),"candidate_scope":"BASELINE_ONLY_NO_ELIGIBLE_CANDIDATE"}
    _put_study_result(store,database,"EXECUTION_STRESS",{"BASE":{"source":"existing completed baseline OOS"},"BASE_PLUS_1_TICK":output["BASE_PLUS_1_TICK"],"BASE_PLUS_2_TICKS":output["BASE_PLUS_2_TICKS"]})
    _put_study_result(store,database,"AMBIGUITY_ROBUSTNESS",{"STOP_FIRST":{"source":"existing completed baseline OOS"},"AMBIGUOUS_SKIP":output["AMBIGUOUS_SKIP"],"TARGET_FIRST":{"status":"NOT_RUN","role":"OPTIMISTIC_DIAGNOSTIC_ONLY","can_qualify":False}})
    print(json.dumps(output,sort_keys=True,indent=2))

def finalize(database:Path):
    connection=sqlite3.connect(database);connection.row_factory=sqlite3.Row
    verdicts={x["candidate_id"]:x["verdict"] for x in connection.execute("SELECT candidate_id,verdict FROM phase6_verdicts WHERE study_id=?",(STUDY,))}
    result_rows=list(connection.execute("SELECT candidate_id,result_type,payload_json FROM phase6_results WHERE study_id=? AND result_type IN ('WALK_FORWARD_IS','WALK_FORWARD_OOS')",(STUDY,)))
    connection.close();metrics={}
    for row in result_rows:metrics.setdefault(row["candidate_id"],{})[row["result_type"]]=json.loads(row["payload_json"])["aggregate_metrics"]
    degradation={}
    for candidate,roles in metrics.items():
        if set(roles)!={"WALK_FORWARD_IS","WALK_FORWARD_OOS"}:continue
        ins,oos=roles["WALK_FORWARD_IS"],roles["WALK_FORWARD_OOS"]
        degradation[candidate]={key:{"is":ins.get(key),"oos":oos.get(key),"delta":(oos.get(key)-ins.get(key)) if isinstance(ins.get(key),(int,float)) and isinstance(oos.get(key),(int,float)) else None} for key in ("trades","net_pnl","profit_factor","expectancy","max_drawdown")}
    eligible=[name for name,value in verdicts.items() if value=="ADVANCE_TO_FINAL_HOLDOUT"]
    if eligible:raise RuntimeError(f"Eligible finalist exists; holdout decision requires review: {eligible}")
    store=Phase6Store(database);store.initialize()
    _put_study_result(store,database,"IS_OOS_DEGRADATION",degradation)
    _put_study_result(store,database,"FINALIST_SELECTION",{"status":"NO_ELIGIBLE_FINALIST","eligible_candidates":[],"candidate_verdicts":verdicts,"basis":"All preregistered OOS candidate verdicts are FRAGILE; missing robustness analyses did not improve eligibility."})
    _put_study_result(store,database,"FINAL_HOLDOUT_FIREWALL",{"status":"UNTOUCHED","start":"2026-08-07T00:00:00+00:00","end":"2026-08-19T00:00:00+00:00","reason":"NO_ELIGIBLE_FINALIST"})
    _put_study_result(store,database,"PHASE6_FINAL_VERDICT",{"verdict":"NO CANDIDATE ADVANCES","phase6_status":"PHASE 6 STATUS: NO CANDIDATE ADVANCES","holdout_opened":False,"production_ready":False,"phase7_started":False})
    print(json.dumps({"degradation":degradation,"finalists":[],"holdout":"UNTOUCHED","verdict":"NO CANDIDATE ADVANCES"},sort_keys=True,indent=2))

if __name__=="__main__":main()
