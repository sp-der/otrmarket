#!/usr/bin/env python3
"""Deterministic plumbing examples only; never strategy evaluation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

from src.research.execution.account import reference_account_profile
from src.research.execution.in_loop import InLoopResearchExecutor
from src.research.execution.simulator import ExecutionConfig
from tests.test_operation50 import ict_setup

BASE=datetime(2026,8,19,14,0,tzinfo=timezone.utc)
BASELINE={"1m":15,"5m":8,"15m":4,"1h":2}


def setup(ident,timeframe="1m",grade="A+"):
    value=ict_setup("bullish",created_at=BASE);value.setup_id=ident;value.timeframe=timeframe
    value.entry_price,value.stop_price,value.target_price,value.risk_reward=100,95,110,2
    value.metadata={"strategy":"ICT_CONFLUENCE","a_plus_context":{"quality_grade":grade,"quality_score":90},
      "evaluation_guard":{"risk_cap_dollars":250,"risk_dollars":250},"recovery_control_70":{"mode":"NORMAL"}}
    return value


def executor(lifetimes=BASELINE):
    return InLoopResearchExecutor("phase31-smoke",reference_account_profile(),ExecutionConfig(fill_model="IDEAL_TOUCH"),"CANDLE_APPROXIMATE",lifetimes)


def main():
    unfilled=executor();a=unfilled.register_setup(setup("unfilled"),risk_dollars=250)
    unfilled.on_price("NQ",105,BASE+timedelta(minutes=16))
    loss=executor();b=loss.register_setup(setup("loss"),risk_dollars=250)
    loss.on_candle("NQ",BASE+timedelta(minutes=1),100,101,99,100)
    loss.on_candle("NQ",BASE+timedelta(minutes=2),94,94,93,94)
    bplus=loss.simulator.account.recovery("NQ","B+",BASE+timedelta(minutes=32))
    baseline=executor();c1=baseline.register_setup(setup("baseline-15m","15m"),risk_dollars=250)
    baseline.on_price("NQ",105,BASE+timedelta(minutes=60));at_boundary=c1.status
    baseline.on_price("NQ",105,BASE+timedelta(minutes=60,seconds=1))
    override=executor({**BASELINE,"15m":8});c2=override.register_setup(setup("override-15m","15m"),risk_dollars=250)
    override.on_price("NQ",105,BASE+timedelta(minutes=75))
    print(json.dumps({"label":"PLUMBING ONLY — NOT VALID FOR STRATEGY EVALUATION",
      "unfilled":{"status":a.status,"result":a.result,"realized_pnl":0,"recovery":unfilled.simulator.account.recovery("NQ","B+",BASE+timedelta(minutes=20))[2]},
      "loss":{"status":b.status,"result":b.result,"net_pnl":b.result_dollars,"recovery":loss.simulator.account.recovery("NQ","A+",BASE+timedelta(minutes=32))[2],"b_plus_allowed":bplus[0]},
      "15m_baseline":{"configured_bars":4,"status_at_boundary":at_boundary,"status_after_boundary":c1.status},
      "15m_override":{"configured_bars":8,"status_after_5_bars":c2.status}},sort_keys=True,indent=2))


if __name__=="__main__":main()
