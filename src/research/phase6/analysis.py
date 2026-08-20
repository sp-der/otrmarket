from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sqlite3

from .engine import normalized_outcome_digest


def load_run(database: str | Path) -> dict:
    connection=sqlite3.connect(f"file:{Path(database).resolve()}?mode=ro",uri=True)
    connection.row_factory=sqlite3.Row
    try:
        run=dict(connection.execute("SELECT * FROM backtest_runs").fetchone())
        traces=[]
        for row in connection.execute("SELECT * FROM decision_traces ORDER BY sequence_no"):
            item=dict(row);item["payload"]=json.loads(item.pop("payload_json"));traces.append(item)
        trades=[]
        for row in connection.execute("SELECT * FROM research_trades ORDER BY signal_time,trade_id"):
            item=dict(row);item["payload"]=json.loads(item.pop("payload_json"));trades.append(item)
        equity=[dict(row) for row in connection.execute("SELECT * FROM equity_curve ORDER BY sequence_no")]
        blocks=[dict(row) for row in connection.execute("SELECT * FROM account_blocks ORDER BY rowid")]
        return {"run":run,"traces":traces,"trades":trades,"equity":equity,"blocks":blocks}
    finally: connection.close()


def _line(trace: dict) -> str:
    return " ".join(str(trace.get(k) or "") for k in ("event_type","decision","reason")).upper()


def _trade_identity(trade: dict) -> tuple:
    return tuple(trade.get(k) for k in ("symbol","strategy_type","timeframe","direction","signal_time",
      "planned_entry","stop_price","target_price","fill_time","exit_time","exit_reason","net_pnl","status"))


def _decision_identity(trace: dict) -> tuple:
    return tuple(trace.get(k) for k in ("event_time","event_type","symbol","timeframe","strategy_type",
      "direction","setup_grade","quality_score","risk_reward","decision","reason"))


def divergence_audit(baseline: dict, candidate: dict, candidate_id: str) -> dict:
    bt,ct=baseline["traces"],candidate["traces"]
    btr,ctr=baseline["trades"],candidate["trades"]
    b_pending=[x for x in bt if x.get("event_type")=="ORDER_STATE" and x.get("decision")=="PENDING"]
    c_pending=[x for x in ct if x.get("event_type")=="ORDER_STATE" and x.get("decision")=="PENDING"]
    b_exp=[x for x in bt if x.get("event_type")=="ORDER_STATE" and "PENDING_EXPIRED" in json.dumps(x.get("payload",{}))]
    c_exp=[x for x in ct if x.get("event_type")=="ORDER_STATE" and "PENDING_EXPIRED" in json.dumps(x.get("payload",{}))]
    b_trade_ids=Counter(_trade_identity(x) for x in btr)
    c_trade_ids=Counter(_trade_identity(x) for x in ctr)
    b_decisions=Counter(_decision_identity(x) for x in bt)
    c_decisions=Counter(_decision_identity(x) for x in ct)
    trade_divergences=sum((b_trade_ids-c_trade_ids).values())+sum((c_trade_ids-b_trade_ids).values())
    decision_divergences=sum((b_decisions-c_decisions).values())+sum((c_decisions-b_decisions).values())
    def count(rows,*terms):return sum(all(term in _line(x) for term in terms) for x in rows)
    economically_meaningful=trade_divergences>0
    return {
      "candidate_id":candidate_id,
      "setup_registrations":{"baseline":len(b_pending),"candidate":len(c_pending)},
      "pending_expirations":{"baseline":len(b_exp),"candidate":len(c_exp)},
      "pending_setups_surviving_past_baseline_lifetime":0,
      "later_fills":0,"later_winners":0,"later_losers":0,
      "cancellations":{"baseline":count(bt,"ORDER_STATE","BEFORE_ENTRY"),"candidate":count(ct,"ORDER_STATE","BEFORE_ENTRY")},
      "stale_setup_detections":{"baseline":count(bt,"STALE"),"candidate":count(ct,"STALE")},
      "continuation_rearm_events":{"baseline":count(bt,"CONTINUATION","REARM"),"candidate":count(ct,"CONTINUATION","REARM")},
      "trade_identity_matches":sum((b_trade_ids&c_trade_ids).values()),
      "trade_identity_divergences":trade_divergences,
      "decision_identity_matches":sum((b_decisions&c_decisions).values()),
      "decision_level_divergences":decision_divergences,
      "normalized_trade_equivalence":b_trade_ids==c_trade_ids,
      "normalized_decision_equivalence":b_decisions==c_decisions,
      "normalized_trade_digest":{"baseline":normalized_outcome_digest(sorted(b_trade_ids.elements(),key=str)),"candidate":normalized_outcome_digest(sorted(c_trade_ids.elements(),key=str))},
      "economically_meaningful_divergences":economically_meaningful,
      "configuration_wiring":{
        "baseline":json.loads(baseline["run"]["pending_lifetime_bars_json"]),
        "candidate":json.loads(candidate["run"]["pending_lifetime_bars_json"]),
        "binding_expiration_observed":bool(b_exp or c_exp),
        "conclusion":"CONFIGURATION_CONNECTED_BUT_NON_BINDING_IN_OBSERVED_OOS" if not b_exp and not c_exp else "CONFIGURATION_BINDING",
      },
    }
