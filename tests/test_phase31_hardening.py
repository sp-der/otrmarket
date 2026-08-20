import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.research.execution.in_loop import InLoopResearchExecutor
from src.research.execution.simulator import ExecutionConfig, SetupIntent, FuturesExecutionSimulator
from src.research.execution.account import reference_account_profile
from tests.test_operation50 import ict_setup

UTC=timezone.utc
BASE=datetime(2026,8,19,14,0,tzinfo=UTC)
LIFETIMES={"1m":15,"5m":8,"15m":4,"1h":2}


def setup(timeframe="1m",ident="setup",grade="A+",created=BASE):
    value=ict_setup("bullish",created_at=created);value.setup_id=ident;value.timeframe=timeframe
    value.entry_price=100;value.stop_price=95;value.target_price=110;value.risk_reward=2
    value.metadata={"strategy":"ICT_CONFLUENCE","a_plus_context":{"quality_grade":grade,"quality_score":90},
      "evaluation_guard":{"risk_cap_dollars":250,"risk_dollars":250},"recovery_control_70":{"mode":"NORMAL"}}
    return value


def executor(mode="CANDLE_APPROXIMATE",lifetimes=None,policy="STOP_FIRST",model="IDEAL_TOUCH"):
    return InLoopResearchExecutor("run",reference_account_profile(),ExecutionConfig(fill_model=model,ambiguity_policy=policy),mode,lifetimes or LIFETIMES)


class Phase31PendingTests(unittest.TestCase):
    def assert_expiry(self,timeframe,bars):
        ex=executor();position=ex.register_setup(setup(timeframe),risk_dollars=250)
        seconds={"1m":60,"5m":300,"15m":900,"1h":3600}[timeframe]
        ex.on_price("NQ",105,BASE+timedelta(seconds=seconds*bars))
        self.assertEqual(position.status,"PENDING")
        ex.on_price("NQ",105,BASE+timedelta(seconds=seconds*bars+1))
        self.assertEqual(position.result,"EXPIRED_BEFORE_ENTRY");self.assertEqual(position.cancellation_details["configured_max_bars"],bars)

    def test_baseline_pending_lifetimes(self):
        for timeframe,bars in LIFETIMES.items():self.assert_expiry(timeframe,bars)

    def test_research_lifetime_override(self):
        ex=executor(lifetimes={**LIFETIMES,"15m":8});position=ex.register_setup(setup("15m"),risk_dollars=250)
        ex.on_price("NQ",105,BASE+timedelta(minutes=15*5));self.assertEqual(position.status,"PENDING")

    def test_stop_before_entry_cancels_and_never_fills(self):
        ex=executor(mode="TICK_EXACT");position=ex.register_setup(setup(),risk_dollars=250)
        ex.on_price("NQ",94,BASE+timedelta(seconds=1));self.assertEqual(position.cancellation_details["cancellation_reason"],"STOP_BREACHED_BEFORE_ENTRY")
        ex.on_price("NQ",100,BASE+timedelta(seconds=2));self.assertNotEqual(position.status,"OPEN")

    def test_target_progress_cancels(self):
        ex=executor();position=ex.register_setup(setup(),risk_dollars=250)
        ex.on_price("NQ",107.5,BASE+timedelta(seconds=1));self.assertEqual(position.cancellation_details["cancellation_reason"],"TARGET_PROGRESS_75")

    def test_stale_at_registration(self):
        ex=executor();ex.last_prices["NQ"]=108;position=ex.register_setup(setup(),risk_dollars=250)
        self.assertEqual(position.cancellation_details["cancellation_reason"],"STALE_AT_REGISTRATION")

    def test_unfilled_has_no_recovery(self):
        ex=executor();position=ex.register_setup(setup(),risk_dollars=250);ex.on_price("NQ",105,BASE+timedelta(minutes=16))
        self.assertEqual(ex.simulator.account.consecutive_losses,0);self.assertEqual(ex.simulator.account.recovery("NQ","B+",BASE+timedelta(minutes=20))[2],"NORMAL")

    def test_stale_can_arm_continuation_but_original_stays_dead(self):
        from src.strategies.continuation import ContinuationRearmEngine
        ex=executor();position=ex.register_setup(setup(),risk_dollars=250);ex.on_price("NQ",108,BASE+timedelta(seconds=1))
        continuation=ContinuationRearmEngine();self.assertTrue(continuation.arm_from_stale(position.setup,BASE+timedelta(seconds=1)))
        self.assertNotIn(position.setup.setup_id,ex.positions)


class Phase31RiskRecoveryTests(unittest.TestCase):
    def test_final_risk_multiplier_applied_once(self):
        sim=FuturesExecutionSimulator("run");intent=SetupIntent("run","risk","NQ","NQ SEP26","ICT","5m","bullish",BASE,100,95,110,2,250,150,"A+",90,recovery_state="SYMBOL_RECOVERY")
        trade=sim.submit_final(intent);audit=sim.risk_audits[-1]
        self.assertEqual(audit["final_allowed_risk"],150);self.assertEqual(trade.quantity,15);self.assertEqual(trade.actual_risk,150)

    def test_loss_drives_recovery_and_b_plus_block(self):
        ex=executor();position=ex.register_setup(setup(),risk_dollars=250)
        ex.on_candle("NQ",BASE+timedelta(minutes=1),100,101,99,100)
        ex.on_candle("NQ",BASE+timedelta(minutes=2),94,94,93,94)
        self.assertEqual(position.result,"LOSS")
        allowed,cap,state,_=ex.simulator.account.recovery("NQ","A+",BASE+timedelta(minutes=32));self.assertEqual((allowed,cap,state),(True,.60,"SYMBOL_RECOVERY"))
        self.assertFalse(ex.simulator.account.recovery("NQ","B+",BASE+timedelta(minutes=32))[0])

    def test_two_losses_drive_account_recovery(self):
        sim=FuturesExecutionSimulator("run");sim.account.close("NQ",BASE,-100,0,"LOSS");sim.account.close("ES",BASE+timedelta(minutes=1),-100,0,"LOSS")
        self.assertEqual(sim.account.recovery("GC","A+",BASE+timedelta(minutes=2))[2],"ACCOUNT_RECOVERY")


class Phase31ExecutionTests(unittest.TestCase):
    def test_replay_worker_has_single_execution_authority(self):
        worker=(Path(__file__).resolve().parents[1]/"src/research/replay/worker.py").read_text()
        self.assertIn("runtime.paper=research_executor",worker)
        self.assertFalse((Path(__file__).resolve().parents[1]/"src/research/execution/pipeline.py").exists())

    def test_entry_candle_flags_and_excursion(self):
        for high,low,flag in ((104,94,"ENTRY_AND_STOP_SAME_CANDLE"),(111,99,"ENTRY_AND_TARGET_SAME_CANDLE"),(111,94,"ENTRY_STOP_TARGET_SAME_CANDLE")):
            sim=FuturesExecutionSimulator("run",execution=ExecutionConfig(fill_model="IDEAL_TOUCH",ambiguity_policy="STOP_FIRST"));sim.submit_final(SetupIntent("run",flag,"NQ","NQ SEP26","ICT","5m","bullish",BASE,100,95,110,2,250,250))
            sim.on_candle(BASE+timedelta(minutes=1),101,high,low,100,"NQ")
            trade=(sim.records[-1] if sim.records else sim._record(sim.open[-1]));self.assertIn(flag,trade["ambiguity_flags"]);self.assertEqual(trade["excursion_quality"],"ENTRY_CANDLE_AMBIGUOUS")
            self.assertEqual((trade["mfe_points"],trade["mae_points"]),(0,0))

    def test_mark_to_market_and_intrabar_drawdown(self):
        sim=FuturesExecutionSimulator("run",execution=ExecutionConfig(fill_model="IDEAL_TOUCH"));sim.submit_final(SetupIntent("run","mtm","NQ","NQ SEP26","ICT","5m","bullish",BASE,100,95,110,2,250,250))
        sim.on_tick(BASE+timedelta(seconds=1),100,symbol="NQ");sim.mark_to_market(BASE+timedelta(seconds=2),{"NQ":102},{"NQ":98})
        point=sim.account.equity_points[-1];self.assertGreater(point["unrealized_pnl"],0);self.assertGreater(point["intrabar_approximate_drawdown"],0)

    def test_bid_ask_both_directions(self):
        long=FuturesExecutionSimulator("l",execution=ExecutionConfig(fill_model="BID_ASK"));lt=long.submit_final(SetupIntent("l","l","NQ","NQ SEP26","ICT","1m","bullish",BASE,100,95,110,2,100,100));long.on_tick(BASE,99.9,99.75,100.25,"NQ");self.assertIsNone(lt.actual_fill);long.on_tick(BASE,99.75,99.5,100,"NQ");self.assertEqual(lt.actual_fill,100)
        short=FuturesExecutionSimulator("s",execution=ExecutionConfig(fill_model="BID_ASK"));st=short.submit_final(SetupIntent("s","s","NQ","NQ SEP26","ICT","1m","bearish",BASE,100,105,90,2,100,100));short.on_tick(BASE,100.1,99.75,100.25,"NQ");self.assertIsNone(st.actual_fill);short.on_tick(BASE,100.25,100,100.5,"NQ");self.assertEqual(st.actual_fill,100)

    def test_price_improvement_and_no_double_slippage(self):
        sim=FuturesExecutionSimulator("run",execution=ExecutionConfig(fill_model="IDEAL_TOUCH",round_turn_commission=2,round_turn_fees=1));sim.submit_final(SetupIntent("run","p","NQ","NQ SEP26","ICT","1m","bullish",BASE,100,95,110,2,100,100))
        sim.on_candle(BASE+timedelta(minutes=1),99,99,98,99,"NQ");sim.on_candle(BASE+timedelta(minutes=2),110,111,109,110,"NQ");record=sim.records[-1]
        self.assertGreater(record["price_improvement"],0);self.assertEqual(record["net_pnl"],record["gross_pnl"]-record["commission"]-record["fees"])

    def test_complete_reference_profile(self):
        profile=reference_account_profile();required={"starting_balance","profit_target","max_loss_limit","trailing_loss_basis","firm_daily_loss_limit","internal_daily_stop","base_risk","minimum_risk","max_trades_per_day","max_consecutive_losses","max_concurrent_positions","max_micros","session_profit_cap","no_new_trades_after_et","resume_trading_et","continue_after_target","session_timezone","profile_verification"}
        self.assertTrue(required.issubset(profile));self.assertEqual(profile["profile_verification"],"RESEARCH_REFERENCE_PROFILE")


if __name__=="__main__":unittest.main()
