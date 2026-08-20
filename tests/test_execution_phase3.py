import hashlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.research.execution.account import AccountState, PropConfig
from src.research.execution.metrics import raw_metrics
from src.research.execution.simulator import ExecutionConfig, FuturesExecutionSimulator, SetupIntent

UTC=timezone.utc
BASE=datetime(2026,8,19,14,0,tzinfo=UTC)


def intent(symbol="NQ",risk=250,entry=100,stop=95,target=110,direction="bullish",grade="A+",ident="s1"):
    contract={"NQ":"NQ SEP26","ES":"ES SEP26","GC":"GC DEC26"}[symbol]
    return SetupIntent("run",ident,symbol,contract,"ICT_CONFLUENCE","5m",direction,BASE,entry,stop,target,2,risk,risk,grade,90)


class Phase3SizingTests(unittest.TestCase):
    def sim(self,**kwargs): return FuturesExecutionSimulator("run",execution=ExecutionConfig(fill_model="IDEAL_TOUCH",**kwargs))

    def test_mnq_risk_sizing(self):
        trade=self.sim().submit(intent("NQ",250,100,95,110)); self.assertEqual((trade.per_contract_risk,trade.quantity,trade.actual_risk),(10,25,250))

    def test_mes_risk_sizing(self):
        trade=self.sim().submit(intent("ES",250,100,95,110)); self.assertEqual((trade.per_contract_risk,trade.quantity,trade.actual_risk),(25,10,250))

    def test_mgc_risk_sizing(self):
        trade=self.sim().submit(intent("GC",250,100,95,110)); self.assertEqual((trade.per_contract_risk,trade.quantity,trade.actual_risk),(50,5,250))

    def test_integer_rounding_and_unused_budget(self):
        trade=self.sim().submit(intent("MNQ" if False else "NQ",103,100,97,106)); self.assertEqual(trade.quantity,17); self.assertEqual(trade.unused_risk,1)

    def test_insufficient_risk_rejects(self):
        record=self.sim().submit(intent("NQ",9,100,95,110)); self.assertEqual(record["status"],"INSUFFICIENT_RISK")

    def test_tick_rounding_reuses_otr_geometry(self):
        trade=self.sim().submit(intent("NQ",250,100.12,95.13,110.11)); self.assertEqual((trade.planned_entry,trade.stop,trade.target),(100.0,95.0,110.25))


class Phase3FillTests(unittest.TestCase):
    def simulator(self,model="IDEAL_TOUCH",**kw): return FuturesExecutionSimulator("run",execution=ExecutionConfig(fill_model=model,**kw))

    def close_trade(self,sim,setup=None):
        trade=sim.submit(setup or intent()); sim.on_candle(BASE+timedelta(minutes=1),101,101,99,100,"NQ"); sim.on_candle(BASE+timedelta(minutes=2),109,111,108,110,"NQ"); return sim.records[-1]

    def test_commission_and_fee_deductions(self):
        record=self.close_trade(self.simulator()); self.assertEqual(record["net_pnl"],record["gross_pnl"]-record["commission"]-record["fees"]); self.assertGreater(record["commission"],0)

    def test_slippage_model(self):
        sim=self.simulator("SLIPPAGE_MODEL",slippage_ticks=1); trade=sim.submit(intent()); sim.on_candle(BASE+timedelta(minutes=1),100,101,99,100,"NQ"); self.assertEqual(trade.actual_fill,100.25); self.assertGreater(trade.slippage_cost,0)

    def test_bid_ask_fill(self):
        sim=self.simulator("BID_ASK"); trade=sim.submit(intent(entry=100.5)); sim.on_tick(BASE+timedelta(seconds=1),100.25,100,100.25,"NQ"); self.assertEqual(trade.actual_fill,100.25)

    def test_stop_first_ambiguity(self):
        sim=self.simulator(ambiguity_policy="STOP_FIRST"); sim.submit(intent()); sim.on_candle(BASE+timedelta(minutes=1),100,111,94,100,"NQ"); self.assertEqual(sim.records[-1]["exit_reason"],"STOP"); self.assertIn("STOP_AND_TARGET_SAME_CANDLE",sim.records[-1]["ambiguity_flags"])

    def test_target_first_optional(self):
        sim=self.simulator(ambiguity_policy="TARGET_FIRST"); sim.submit(intent()); sim.on_candle(BASE+timedelta(minutes=1),100,111,94,100,"NQ"); self.assertEqual(sim.records[-1]["exit_reason"],"TARGET")

    def test_ambiguous_skip_keeps_open(self):
        sim=self.simulator(ambiguity_policy="AMBIGUOUS_SKIP"); sim.submit(intent()); sim.on_candle(BASE+timedelta(minutes=1),100,111,94,100,"NQ"); self.assertEqual(len(sim.open),1)

    def test_gap_through_stop(self):
        sim=self.simulator(); trade=sim.submit(intent()); sim.on_candle(BASE+timedelta(minutes=1),100,101,99,100,"NQ"); sim.on_candle(BASE+timedelta(minutes=2),93,94,92,93,"NQ"); self.assertEqual(sim.records[-1]["exit_fill"],93); self.assertGreater(sim.records[-1]["gap_slippage"],0)

    def test_mfe_mae_and_realized_r(self):
        sim=self.simulator(); trade=sim.submit(intent()); sim.on_candle(BASE+timedelta(minutes=1),100,104,98,102,"NQ"); sim.on_candle(BASE+timedelta(minutes=2),109,111,108,110,"NQ"); record=sim.records[-1]
        self.assertEqual(record["mfe_points"],11); self.assertEqual(record["mae_points"],0); self.assertIsNotNone(record["realized_r"])
        self.assertEqual(record["excursion_quality"],"ENTRY_CANDLE_AMBIGUOUS")

    def test_identical_events_produce_identical_money(self):
        outputs=[]
        for _ in range(2):
            sim=self.simulator(); outputs.append(self.close_trade(sim))
        monetary=("quantity","actual_risk","gross_pnl","commission","fees","slippage_cost","net_pnl","realized_r")
        self.assertEqual({k:outputs[0][k] for k in monetary},{k:outputs[1][k] for k in monetary})

    def test_no_production_database_mutation(self):
        production=Path(__file__).resolve().parents[1]/"data"/"otrmarket.db"
        before=hashlib.sha256(production.read_bytes()).hexdigest(); self.close_trade(self.simulator())
        self.assertEqual(before,hashlib.sha256(production.read_bytes()).hexdigest())


class Phase3AccountTests(unittest.TestCase):
    def test_daily_pnl_equity_peak_and_drawdown(self):
        account=AccountState(PropConfig()); account.fill(BASE,100); account.close("NQ",BASE+timedelta(minutes=1),200,100,"WIN"); account.fill(BASE+timedelta(minutes=2),100); account.close("ES",BASE+timedelta(minutes=3),-300,100,"LOSS")
        self.assertEqual(account.daily_pnl[next(iter(account.daily_pnl))],-100); self.assertEqual(account.peak_equity,50200); self.assertEqual(account.equity_points[-1]["drawdown_dollars"],300)

    def test_concurrent_risk(self):
        sim=FuturesExecutionSimulator("run",execution=ExecutionConfig(fill_model="IDEAL_TOUCH")); sim.submit(intent(ident="one")); blocked=sim.submit(intent("ES",ident="two")); self.assertEqual(blocked["status"],"POSITION_LOCK")

    def test_prop_daily_stop(self):
        account=AccountState(PropConfig(internal_daily_stop=100)); account.close("NQ",BASE,-101,0,"LOSS"); self.assertEqual(account.can_open("ES","A+",BASE+timedelta(minutes=1))[1],"DAILY_LOCK")

    def test_trailing_mll(self):
        account=AccountState(PropConfig()); prior=(BASE-timedelta(days=1)).astimezone(); account.daily_pnl[prior.astimezone().date()]=2200
        self.assertGreaterEqual(account.mll_floor(BASE.date()),50100)

    def test_max_trades_and_loss_breaker(self):
        account=AccountState(PropConfig(max_trades_per_day=1,max_consecutive_losses=1)); account.daily_trades[BASE.astimezone().date()]=1
        self.assertEqual(account.can_open("NQ","A+",BASE)[1],"DAILY_LOCK")
        account.daily_trades.clear(); account.consecutive_losses=1; self.assertEqual(account.can_open("NQ","A+",BASE)[1],"DAILY_LOCK")

    def test_session_profit_cap(self):
        from src.research.execution.account import session_name,trading_day
        account=AccountState(PropConfig(session_profit_cap=100)); account.session_pnl[(trading_day(BASE),session_name(BASE))]=101
        self.assertEqual(account.can_open("NQ","A+",BASE)[1],"SESSION_PROFIT_LOCK")

    def test_operation70_recovery_one_and_two_losses(self):
        account=AccountState(PropConfig()); account.close("NQ",BASE,-100,0,"LOSS")
        allowed,cap,state,_=account.recovery("NQ","A+",BASE+timedelta(minutes=31)); self.assertTrue(allowed); self.assertEqual((cap,state),(.60,"SYMBOL_RECOVERY"))
        account.close("ES",BASE+timedelta(minutes=1),-100,0,"LOSS"); allowed,cap,state,_=account.recovery("GC","A+",BASE+timedelta(minutes=32)); self.assertEqual((cap,state),(.35,"ACCOUNT_RECOVERY"))

    def test_b_plus_disabled(self):
        account=AccountState(PropConfig()); account.close("NQ",BASE,-100,0,"LOSS"); self.assertFalse(account.recovery("NQ","B+",BASE+timedelta(minutes=31))[0])

    def test_isolated_account_state(self):
        one,two=AccountState(PropConfig()),AccountState(PropConfig()); one.close("NQ",BASE,-100,0,"LOSS"); self.assertEqual(two.balance,50000); self.assertEqual(two.consecutive_losses,0)

    def test_metrics(self):
        trades=[{"status":"CLOSED","net_pnl":100,"gross_pnl":110,"realized_r":1,"direction":"bullish","symbol":"NQ"},{"status":"CLOSED","net_pnl":-50,"gross_pnl":-40,"realized_r":-.5,"direction":"bearish","symbol":"ES"}]
        metrics,_=raw_metrics(trades,[{"drawdown_dollars":50,"drawdown_percent":.1}]); self.assertEqual(metrics["net_pnl"],50); self.assertEqual(metrics["profit_factor"],2); self.assertEqual(metrics["total_trades"],2)


if __name__=="__main__": unittest.main()
