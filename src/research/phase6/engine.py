from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from statistics import median

from src.research.experiments.engine import BASELINE_PENDING_LIFETIMES, configuration_diff
from src.research.replay.runs import canonical_json


PHASE6_VERDICTS = {"INCOMPLETE_DATA","INSUFFICIENT_SAMPLE","WORSE","FRAGILE","MIXED","ROBUST","ADVANCE_TO_FINAL_HOLDOUT","ADVANCE_TO_PHASE7"}
REGIME_VERSION = "CAUSAL_ATR_TREND_V1"
LIMITATIONS = ["CANDLE_APPROXIMATE 1-minute Last OHLCV",
 "Production 5m intrabar acceleration (0.25-second checks, three confirmations, 0.75-second stability, exact forming-bar timing) is not faithfully reproducible; Phase 7 shadow validation is required."]


def digest(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def normalized_outcome_digest(value) -> str:
    """Digest behavior independent of deterministic run/setup identifier namespace."""
    ignored={"run_id","trade_id","setup_id","baseline_setup_id","candidate_setup_id","created_at"}
    def normalize(item):
        if isinstance(item,dict): return {k:normalize(v) for k,v in sorted(item.items()) if k not in ignored}
        if isinstance(item,list): return [normalize(v) for v in item]
        return item
    return digest(normalize(value))


def utc(value: str) -> datetime:
    result=datetime.fromisoformat(value.replace("Z","+00:00"))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)


def preregister_walk_forward(start: str, end_exclusive: str) -> dict:
    start_dt,end_dt=utc(start),utc(end_exclusive)
    folds=[]
    for index in range(4):
        is_start=start_dt+timedelta(days=14*index); is_end=is_start+timedelta(days=42)
        oos_start=is_end; oos_end=oos_start+timedelta(days=14)
        if oos_end>end_dt: break
        folds.append({"fold_id":f"FOLD_{index+1}","is_start":is_start.isoformat(),"is_end":is_end.isoformat(),
                      "oos_start":oos_start.isoformat(),"oos_end":oos_end.isoformat()})
    holdout_start=max((utc(x["oos_end"]) for x in folds),default=start_dt)
    if holdout_start>=end_dt: raise ValueError("No untouched final holdout remains")
    return {"folds":folds,"final_holdout":{"start":holdout_start.isoformat(),"end":end_dt.isoformat()},
            "is_days":42,"oos_days":14,"step_days":14,"firewall":"UNTOUCHED_UNTIL_FINALISTS_SELECTED"}


def validate_candidate(configuration: dict) -> None:
    lifetimes=configuration["pending_lifetime_bars"]
    ranges={"1m":(15,25),"5m":(8,18),"15m":(4,12),"1h":(2,8)}
    for tf,(low,high) in ranges.items():
        if not low<=int(lifetimes[tf])<=high: raise ValueError(f"{tf} lifetime outside approved bounded range")


def enforce_single_thesis(baseline: dict, candidate: dict) -> list[dict]:
    changes=configuration_diff(baseline,candidate)
    invalid=[x for x in changes if not x["path"].startswith("pending_lifetime_bars.")]
    if invalid: raise ValueError(f"Candidate changes outside pending-lifetime thesis: {invalid}")
    validate_candidate(candidate)
    return changes


def causal_regimes(candles: list[dict], lookback: int = 20) -> list[dict]:
    """Labels bar N from bars strictly before N; no current/future outcome enters."""
    result=[]
    for index,row in enumerate(candles):
        prior=candles[max(0,index-lookback):index]
        if len(prior)<lookback:
            label="UNKNOWN"
        else:
            ranges=[float(x["high"])-float(x["low"]) for x in prior]
            recent=sum(ranges[-5:])/5; history=sum(ranges)/len(ranges)
            volatility="HIGH_VOLATILITY" if recent>=history else "LOW_VOLATILITY"
            net=abs(float(prior[-1]["close"])-float(prior[0]["open"])); travel=sum(abs(float(x["close"])-float(x["open"])) for x in prior)
            structure="DIRECTIONAL" if travel and net/travel>=0.35 else "ROTATIONAL"
            label=f"{volatility}_{structure}"
        result.append({"timestamp":row["open_time"],"regime":label,"regime_version":REGIME_VERSION})
    return result


def robust_metrics(trades: list[dict], equity: list[dict]) -> dict:
    closed=[x for x in trades if x.get("status")=="CLOSED"]
    pnl=[float(x.get("net_pnl") or 0) for x in closed]; rs=[float(x["realized_r"]) for x in closed if x.get("realized_r") is not None]
    wins=[x for x in pnl if x>0]; losses=[x for x in pnl if x<0]; gp=sum(wins); gl=-sum(losses)
    streak_w=streak_l=max_w=max_l=0
    for value in pnl:
        streak_w=streak_w+1 if value>0 else 0; streak_l=streak_l+1 if value<0 else 0
        max_w=max(max_w,streak_w); max_l=max(max_l,streak_l)
    start=utc(closed[0]["fill_time"]) if closed and closed[0].get("fill_time") else None
    end=utc(closed[-1]["exit_time"]) if closed and closed[-1].get("exit_time") else start
    days=max(1,(end-start).total_seconds()/86400) if start and end else 1
    return {"trades":len(closed),"wins":len(wins),"losses":len(losses),"win_rate":100*len(wins)/len(closed) if closed else 0,
      "gross_profit":gp,"gross_loss":gl,"net_pnl":sum(pnl),"profit_factor":gp/gl if gl else None,
      "expectancy":sum(pnl)/len(pnl) if pnl else 0,"expectancy_r":sum(rs)/len(rs) if rs else 0,
      "average_r":sum(rs)/len(rs) if rs else 0,"median_r":median(rs) if rs else 0,
      "max_drawdown":max([0]+[float(x.get("drawdown_dollars") or 0) for x in equity]),
      "peak_to_trough_drawdown":max([0]+[float(x.get("equity_drawdown") or 0) for x in equity]),
      "maximum_adverse_excursion":max([0]+[abs(float(x.get("mae_points") or 0)) for x in closed]),
      "maximum_favorable_excursion":max([0]+[float(x.get("mfe_points") or 0) for x in closed]),
      "average_winner":sum(wins)/len(wins) if wins else 0,"average_loser":sum(losses)/len(losses) if losses else 0,
      "largest_winner":max(wins,default=0),"largest_loser":min(losses,default=0),
      "consecutive_wins":max_w,"consecutive_losses":max_l,"trades_per_day":len(closed)/days,
      "exposure_minutes":sum(max(0,(utc(x["exit_time"])-utc(x["fill_time"])).total_seconds()/60) for x in closed if x.get("fill_time") and x.get("exit_time"))}

def aggregate_walk_forward_metrics(trades: list[dict], fold_metrics: list[dict]) -> dict:
    """Aggregate trade statistics while preserving fold-isolated drawdown semantics.

    Walk-forward folds reset account state, so equity curves must not be
    concatenated into one synthetic account. Trade statistics are aggregated
    across folds; drawdown is the worst independently measured fold drawdown.
    """
    metrics = robust_metrics(trades, [])

    def worst(key: str):
        values = [
            float(row[key])
            for row in fold_metrics
            if isinstance(row.get(key), (int, float))
        ]
        return max(values) if values else None

    max_dd = worst("max_drawdown")
    peak_dd = worst("peak_to_trough_drawdown")

    if max_dd is not None:
        metrics["max_drawdown"] = max_dd
    if peak_dd is not None:
        metrics["peak_to_trough_drawdown"] = peak_dd

    return metrics


def segment_metrics(trades: list[dict], equity: list[dict] | None = None) -> dict:
    dimensions=("symbol","strategy_type","timeframe","setup_grade","session","direction","execution_contract","recovery_state","regime","roll_regime")
    result={}
    for field in dimensions:
        groups={}
        for trade in trades: groups.setdefault(str(trade.get(field) or "UNKNOWN"),[]).append(trade)
        result[field]={key:robust_metrics(rows,[]) for key,rows in sorted(groups.items()) if rows}
    return result


def concentration(trades: list[dict]) -> dict:
    closed=[x for x in trades if x.get("status")=="CLOSED"]
    total=sum(float(x.get("net_pnl") or 0) for x in closed)
    def dimension(field):
        values={}
        for row in closed: values[str(row.get(field) or "UNKNOWN")]=values.get(str(row.get(field) or "UNKNOWN"),0)+float(row.get("net_pnl") or 0)
        return {k:{"net_pnl":v,"share_of_net":v/total if total else None} for k,v in sorted(values.items())}
    winners=sorted((float(x.get("net_pnl") or 0) for x in closed),reverse=True)
    return {field:dimension(field) for field in ("symbol","strategy_type","timeframe","session","fold_id")}|{
      "largest_trade_share":winners[0]/total if winners and total else None,"top_5_trade_share":sum(winners[:5])/total if total else None}


def execution_stress_config(base: dict, extra_ticks: int, ambiguity_policy: str = "STOP_FIRST") -> dict:
    if extra_ticks not in (0,1,2): raise ValueError("Only bounded 0/+1/+2 tick stresses are allowed")
    if ambiguity_policy not in ("STOP_FIRST","AMBIGUOUS_SKIP","TARGET_FIRST"): raise ValueError("Unknown ambiguity policy")
    return {**base,"slippage_ticks":int(base.get("slippage_ticks",1))+extra_ticks,"ambiguity_policy":ambiguity_policy,
            "stress_label":f"BASE_PLUS_{extra_ticks}_TICKS","optimistic_upper_bound":ambiguity_policy=="TARGET_FIRST"}


def robustness_verdict(fold_metrics: list[dict], segments: dict, minimum_trades: int = 30, holdout_seen: bool = False) -> dict:
    total=sum(int(x.get("trades") or 0) for x in fold_metrics)
    if total<minimum_trades: value,reasons="INSUFFICIENT_SAMPLE",[f"{total} OOS trades; minimum is {minimum_trades}."]
    elif any((x.get("profit_factor") or 0)<1 or x.get("expectancy",0)<=0 for x in fold_metrics): value,reasons="FRAGILE",["At least one OOS fold lacks positive expectancy or PF above 1.0."]
    elif len([x for x in segments.get("symbol",{}).values() if x.get("trades",0)>0])<2: value,reasons="FRAGILE",["Performance is concentrated in fewer than two markets."]
    else: value,reasons=("ADVANCE_TO_PHASE7" if holdout_seen else "ADVANCE_TO_FINAL_HOLDOUT"),["OOS folds meet preregistered robustness gates."]
    assert value in PHASE6_VERDICTS and value!="PRODUCTION_READY"
    return {"verdict":value,"reasons":reasons,"total_oos_trades":total,"holdout_seen":holdout_seen}
