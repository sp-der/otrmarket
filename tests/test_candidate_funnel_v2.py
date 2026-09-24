from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import json
import tempfile

from src.storage import database
from src.research.live_training72t import install_training_capture_72t
from src.research.run_archive81 import start_fresh_run81, scoped_archive_rows81
from src.research.run_dashboard81 import run_dashboard81
from src.research.run_scope import current_run_id
from src.research.outcomes81 import label_window81
from src.otr8.runner_up81 import check_runner_up81
from test_operation81_research_run_scoping import _gc_setup, _create_and_close_gc_trade


class SnapshotAndRunTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(database,'DB_PATH',Path(self.temp.name)/'test.db')
        self.patch.start()
        self.con = database.get_connection()
        current_run_id(self.con)
        install_training_capture_72t()

    def tearDown(self):
        self.con.close(); self.patch.stop(); self.temp.cleanup()

    def test_features_survive_updates_outcomes_and_capture_reinstallation(self):
        setup=_gc_setup('immutable',0)
        setup.metadata={'strategy':'ICT_CONFLUENCE','decision_feature':42}
        database.save_setup(self.con,setup)
        first=self.con.execute('SELECT entry_price,payload_json,created_at,last_seen_at FROM training_decisions_72t').fetchone()
        setup.entry_price=3501
        setup.metadata['decision_feature']=999
        setup.metadata['future_result']='WIN'
        setup.status='CLOSED'
        database.save_setup(self.con,setup)
        install_training_capture_72t()
        second=self.con.execute('SELECT entry_price,payload_json,created_at,last_seen_at FROM training_decisions_72t').fetchone()
        self.assertEqual(first,second)
        self.assertNotIn('future_result',second[1])
        self.assertEqual(first[2],first[3])

    def test_non_destructive_run_boundary_preserves_13000_and_history(self):
        _create_and_close_gc_trade(self.con,'baseline',0)
        # Synthetic fixture, not a production/replay performance claim.
        self.con.execute("UPDATE paper_trades SET result_dollars=13000 WHERE setup_id='baseline'")
        self.con.commit()
        old=current_run_id(self.con)
        result=start_fresh_run81(self.con,baseline_label='Test baseline',new_label='Validation')
        self.assertNotEqual(old,result['run_id'])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM paper_trades').fetchone()[0],1)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM strategy_setups').fetchone()[0],1)
        snapshot=run_dashboard81(self.con)
        self.assertEqual((snapshot['net_pnl'],snapshot['trades'],snapshot['setups']),(0,0,0))
        archive=result['archive']
        self.assertEqual(archive['net_pnl'],13000)
        self.assertEqual(scoped_archive_rows81(self.con,archive['archive_id'],'paper_trades')[0]['result_dollars'],13000)
        self.assertEqual(len(scoped_archive_rows81(self.con,archive['archive_id'],'strategy_setups')),1)
        install_training_capture_72t()
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM training_decisions_72t WHERE run_id=?',(result['run_id'],)).fetchone()[0],0)
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM training_trades_72t WHERE run_id=?',(old,)).fetchone()[0],1)

    def test_open_position_prevents_new_run(self):
        setup=_gc_setup('open',0)
        from src.execution.paper import PaperExecutor
        database.save_setup(self.con,setup)
        database.upsert_paper_trade(self.con,PaperExecutor().register_setup(setup,risk_dollars=500),setup.created_at.isoformat())
        old=current_run_id(self.con)
        with self.assertRaises(ValueError):start_fresh_run81(self.con,baseline_label='baseline',new_label='new')
        self.assertEqual(current_run_id(self.con),old)


class RunnerFreshnessTests(TestCase):
    def setUp(self):
        self.setup=_gc_setup('runner',0)
        self.now=self.setup.created_at
        self.runtime=SimpleNamespace(clock=SimpleNamespace(event_time=lambda symbol:self.now))
        self.bar=SimpleNamespace(open_time=self.now-timedelta(minutes=5),close_time=self.now,
                                 close=3501,high=3502,low=3499)
        self.histories={('GC','5m'):[self.bar]}

    def check(self):return check_runner_up81(self.setup,self.histories,self.runtime)[0]
    def test_fresh_valid_runner(self):self.assertIsNone(self.check())
    def test_stale_candle(self):
        self.now+=timedelta(minutes=6)
        self.assertEqual(self.check(),'RUNNER_UP_STALE')
    def test_expired_thesis_with_fresh_candle(self):
        self.now+=timedelta(minutes=60);self.bar.close_time=self.now
        self.assertEqual(self.check(),'RUNNER_UP_STALE')
    def test_invalidated(self):
        self.bar.close=3494
        self.assertEqual(self.check(),'RUNNER_UP_INVALIDATED')
    def test_entry_passed(self):
        self.bar.close=3509
        self.assertEqual(self.check(),'RUNNER_UP_ENTRY_PASSED')
    def test_live_quote_invalidates_even_when_candle_is_valid(self):
        self.runtime.market_state={'GC':{'price':3494}}
        self.assertEqual(self.check(),'RUNNER_UP_INVALIDATED')
    def test_future_bar_does_not_supply_evidence(self):
        future=SimpleNamespace(open_time=self.now,close_time=self.now+timedelta(minutes=5),close=3490,high=3491,low=3489)
        self.histories[('GC','5m')].append(future)
        self.assertIsNone(self.check())
        self.assertEqual(len(check_runner_up81(self.setup,self.histories,self.runtime)[3][('GC','5m')]),1)
    def test_missing_clock_and_candles_fails_closed(self):
        self.runtime=SimpleNamespace()
        self.histories={}
        self.assertEqual(self.check(),'RUNNER_UP_STALE')


class OutcomeIsolationTests(TestCase):
    def test_future_bars_cannot_resolve_snapshot(self):
        now=datetime(2026,9,1,12,tzinfo=timezone.utc)
        snapshot=dict(created_at=now.isoformat(),direction='bullish',entry_price=100,stop_price=99,target_price=103)
        frozen=json.dumps(snapshot,sort_keys=True)
        bars=[SimpleNamespace(open_time=now+timedelta(minutes=1),close_time=now+timedelta(minutes=2),low=100,high=101),
              SimpleNamespace(open_time=now+timedelta(minutes=2),close_time=now+timedelta(minutes=3),low=100,high=103)]
        self.assertIsNone(label_window81(snapshot,bars,now)[0])
        self.assertEqual(label_window81(snapshot,bars,now+timedelta(minutes=3))[0],'WOULD_HAVE_REACHED_3R')
        self.assertEqual(json.dumps(snapshot,sort_keys=True),frozen)
    def test_ambiguous_entry_stop_is_not_a_win(self):
        now=datetime(2026,9,1,12,tzinfo=timezone.utc)
        snapshot=dict(created_at=now.isoformat(),direction='bullish',entry_price=100,stop_price=99,target_price=103)
        bar=SimpleNamespace(open_time=now,close_time=now+timedelta(minutes=5),low=98,high=104)
        self.assertEqual(label_window81(snapshot,[bar],bar.close_time)[0],'NOT_EVALUABLE')


class PromotionExecutionTests(TestCase):
    def test_invalid_runner_never_reaches_executor(self):
        import sqlite3
        from test_pipeline80 import _Arbiter, _Regime, _Console
        from src.otr8.pipeline import OTRPipeline80
        for price,age,expected in [(3494,0,'RUNNER_UP_INVALIDATED'),(3509,0,'RUNNER_UP_ENTRY_PASSED'),(3501,60,'RUNNER_UP_STALE')]:
            with self.subTest(expected=expected):
                first=_gc_setup('first',0); second=_gc_setup('second',0)
                for setup in (first,second):setup.metadata={'strategy':'ICT_CONFLUENCE'}
                attempts=[]
                def register(setup,**kwargs):
                    attempts.append(setup.setup_id)
                    raise ValueError('invalid trade geometry')
                now=first.created_at+timedelta(minutes=age)
                runtime=SimpleNamespace(strategy=SimpleNamespace(),paper=SimpleNamespace(register_setup=register),
                    clock=SimpleNamespace(event_time=lambda _:now),
                    evaluation_guard=SimpleNamespace(decide=lambda *args:SimpleNamespace(allowed=True,status='EVAL',risk_dollars=500,reason='ok',snapshot={})),
                    save_setup=lambda *args:None,upsert_paper_trade=lambda *args:None,console=_Console())
                pipeline=OTRPipeline80(runtime=runtime,session_gate=lambda *args:SimpleNamespace(allowed=True,reason='ok',details={}),
                    quality_gate=lambda *args:(True,'ok'),setup_risk=lambda *args:(500,1),arbiter=_Arbiter(),regime_engine=_Regime())
                pipeline.promote_runner_up=True
                bar=SimpleNamespace(open_time=now-timedelta(minutes=5),close_time=now,close=price,high=price,low=price)
                con=sqlite3.connect(':memory:')
                pipeline.process_candidates(con,[first,second],{('GC','5m'):[bar]})
                self.assertEqual(attempts,['second'])
                self.assertEqual(first.status,expected)
                con.close()

    def test_pre_arm_is_observation_only(self):
        import sqlite3
        from test_pipeline80 import _Regime,_Console
        from src.otr8.pipeline import OTRPipeline80
        setup=_gc_setup('preview',0)
        setup.metadata={'strategy':'ICT_CONFLUENCE','preview_only_80':True}
        def unexpected(*args,**kwargs):raise AssertionError('Preview reached execution/approval')
        runtime=SimpleNamespace(strategy=SimpleNamespace(),paper=SimpleNamespace(register_setup=unexpected),save_setup=lambda *args:None,console=_Console())
        pipeline=OTRPipeline80(runtime=runtime,session_gate=unexpected,quality_gate=unexpected,setup_risk=unexpected,regime_engine=_Regime())
        pipeline.promote_runner_up=True
        con=sqlite3.connect(':memory:')
        pipeline.process_candidates(con,[setup],{})
        self.assertEqual(setup.status,'PRE_ARMED')
        con.close()


class DevelopingLifecycleTests(TestCase):
    def test_promotes_same_displacement_without_rewriting_preview(self):
        import sqlite3
        from test_operation81_candidate_ledger_refresh import _engine_state
        from src.research.developing81 import observe_developing81
        con=sqlite3.connect(':memory:');_engine_state(con)
        setup=_gc_setup('preview',0)
        setup.metadata={'candidate_source_80':'EARLY_ARM_72H','preview_only_80':True}
        observe_developing81(con,[setup],'GC','5m',{},setup.created_at)
        frozen=con.execute('SELECT snapshot_json FROM developing_setups_81').fetchone()[0]
        setup.setup_id='executable';setup.metadata['preview_only_80']=False
        observe_developing81(con,[setup],'GC','5m',{},setup.created_at+timedelta(minutes=5))
        state,raw=con.execute('SELECT state,snapshot_json FROM developing_setups_81').fetchone()
        self.assertEqual(state,'PROMOTED_TO_CANDIDATE');self.assertEqual(raw,frozen)
        con.close()
    def test_invalidates_prospectively(self):
        import sqlite3
        from test_operation81_candidate_ledger_refresh import _engine_state
        from src.research.developing81 import observe_developing81
        con=sqlite3.connect(':memory:');_engine_state(con)
        setup=_gc_setup('preview',0);setup.metadata={'candidate_source_80':'EARLY_ARM_72H','preview_only_80':True}
        observe_developing81(con,[setup],'GC','5m',{},setup.created_at)
        later=setup.created_at+timedelta(minutes=5)
        bar=SimpleNamespace(open_time=setup.created_at,close_time=later,low=3490,high=3500)
        observe_developing81(con,[],'GC','5m',{('GC','5m'):[bar]},later)
        self.assertEqual(con.execute('SELECT state FROM developing_setups_81').fetchone()[0],'INVALIDATED')
        con.close()
