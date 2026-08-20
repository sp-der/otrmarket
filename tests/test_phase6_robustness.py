from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.research.historical.causal_roll import causal_decisions
from src.research.phase6.engine import (
    BASELINE_PENDING_LIFETIMES, causal_regimes, concentration, digest,
    enforce_single_thesis, execution_stress_config, preregister_walk_forward,
    robustness_verdict, normalized_outcome_digest,
)
from src.research.phase6.store import Phase6Store
from src.research.dashboard import ResearchDashboardRepository
from src.research.phase6.analysis import divergence_audit


class Phase6Tests(unittest.TestCase):
    def test_walk_forward_aggregate_uses_worst_fold_drawdown(self):
        from src.research.phase6.engine import aggregate_walk_forward_metrics

        trades = [
            {"status": "CLOSED", "net_pnl": 100.0},
            {"status": "CLOSED", "net_pnl": -50.0},
        ]
        folds = [
            {"max_drawdown": 125.0, "peak_to_trough_drawdown": 150.0},
            {"max_drawdown": 410.0, "peak_to_trough_drawdown": 460.0},
            {"max_drawdown": 275.0, "peak_to_trough_drawdown": 300.0},
        ]

        metrics = aggregate_walk_forward_metrics(trades, folds)

        self.assertEqual(metrics["max_drawdown"], 410.0)
        self.assertEqual(metrics["peak_to_trough_drawdown"], 460.0)

    def test_divergence_audit_normalizes_identifier_namespaces(self):
        run=lambda name:{"run":{"pending_lifetime_bars_json":'{"1m":15,"5m":8,"15m":4,"1h":2}'},"traces":[{"event_time":"t","event_type":"ORDER_STATE","decision":"PENDING","setup_id":name}],"trades":[{"trade_id":name,"setup_id":name,"symbol":"NQ","status":"CLOSED","net_pnl":10}],"equity":[],"blocks":[]}
        value=divergence_audit(run("base"),run("candidate"),"C")
        self.assertTrue(value["normalized_trade_equivalence"])
        self.assertTrue(value["normalized_decision_equivalence"])
    def test_walk_forward_and_holdout_are_preregistered(self):
        value=preregister_walk_forward("2026-05-01T00:00:00+00:00","2026-08-19T00:00:00+00:00")
        self.assertEqual(len(value["folds"]),4)
        self.assertEqual(value["folds"][0]["is_end"],"2026-06-12T00:00:00+00:00")
        self.assertEqual(value["folds"][3]["oos_end"],"2026-08-07T00:00:00+00:00")
        self.assertEqual(value["final_holdout"],{"start":"2026-08-07T00:00:00+00:00","end":"2026-08-19T00:00:00+00:00"})

    def test_causal_roll_has_no_same_day_future_volume_leakage(self):
        base={("ES","2026-05-01",1,"ES JUN26"):100,("ES","2026-05-01",2,"ES SEP26"):10,
              ("ES","2026-05-02",1,"ES JUN26"):1,("ES","2026-05-02",2,"ES SEP26"):1000,
              ("ES","2026-05-03",1,"ES JUN26"):1,("ES","2026-05-03",2,"ES SEP26"):1}
        first=causal_decisions(base)
        changed=dict(base); changed[("ES","2026-05-02",2,"ES SEP26")]=1_000_000
        second=causal_decisions(changed)
        # May 2 uses only May 1 evidence; changing later May 2 volume cannot change it.
        self.assertEqual(first[1]["selected_contract"],second[1]["selected_contract"])
        self.assertLess(first[1]["evidence_end_time"],first[1]["decision_timestamp"])
        self.assertEqual(second[2]["selected_contract"],"ES SEP26")

    def test_candidate_equivalence_except_pending_lifetime(self):
        base={"pending_lifetime_bars":BASELINE_PENDING_LIFETIMES,"other":7}
        candidate={"pending_lifetime_bars":{"1m":18,"5m":12,"15m":8,"1h":5},"other":7}
        self.assertEqual(len(enforce_single_thesis(base,candidate)),4)
        with self.assertRaises(ValueError): enforce_single_thesis(base,{**candidate,"other":8})

    def test_execution_stress_and_ambiguity_bounds(self):
        base={"slippage_ticks":1,"commission":2.5}
        self.assertEqual(execution_stress_config(base,2)["slippage_ticks"],3)
        self.assertTrue(execution_stress_config(base,0,"TARGET_FIRST")["optimistic_upper_bound"])
        with self.assertRaises(ValueError): execution_stress_config(base,10)

    def test_regime_uses_prior_bars_only(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        bars=[{"open_time":(start+timedelta(minutes=i)).isoformat(),"open":100+i,"high":102+i,"low":99+i,"close":101+i} for i in range(21)]
        labels=causal_regimes(bars)
        mutated=[dict(x) for x in bars]; mutated[-1].update(high=10000,low=1,close=9000)
        self.assertEqual(labels[-1],causal_regimes(mutated)[-1])
        self.assertEqual(labels[19]["regime"],"UNKNOWN")

    def test_concentration_and_sample_guard(self):
        trades=[{"status":"CLOSED","net_pnl":100,"symbol":"NQ","strategy_type":"ICT","timeframe":"5m","session":"NY","fold_id":"F1"}]
        self.assertEqual(concentration(trades)["largest_trade_share"],1)
        self.assertEqual(robustness_verdict([{"trades":1}],{},30)["verdict"],"INSUFFICIENT_SAMPLE")

    def test_deterministic_manifests_and_isolated_immutable_store(self):
        definition={"study_id":"s","hypothesis":"h","capture_id":"c","capture_digest":"d","git_commit":"g",
          "start_time":"a","end_time":"b","replay_mode":"CANDLE_APPROXIMATE","limitations":[],"preregistration":{},"created_at":"fixed"}
        candidate={"candidate_id":"base","name":"BASELINE","hypothesis":"baseline","configuration":{},"configuration_diff":[],"definition_digest":digest({"base":1})}
        fold={"fold_id":"F1","is_start":"a","is_end":"b","oos_start":"c","oos_end":"d"}
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"phase6.db"; store=Phase6Store(db);store.initialize(); one=store.create_study(definition,[candidate],[fold])
            self.assertEqual(one,digest(definition))
            with self.assertRaises(sqlite3.IntegrityError):
                with sqlite3.connect(db) as c:c.execute("update phase6_studies set hypothesis='x'")

    def test_normalized_digest_ignores_run_identifier_namespace(self):
        one={"run_id":"a","trade_id":"a-1","setup_id":"a-s","net_pnl":10}
        two={"run_id":"b","trade_id":"b-1","setup_id":"b-s","net_pnl":10}
        self.assertEqual(normalized_outcome_digest(one),normalized_outcome_digest(two))

    def test_production_database_unchanged(self):
        path=Path(__file__).resolve().parents[1]/"data"/"otrmarket.db"
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        preregister_walk_forward("2026-05-01T00:00:00+00:00","2026-08-19T00:00:00+00:00")
        self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

    def test_phase6_dashboard_repository_is_read_only_and_handles_empty_data(self):
        definition={"study_id":"s","hypothesis":"h","capture_id":"c","capture_digest":"d","git_commit":"g",
          "start_time":"a","end_time":"b","replay_mode":"CANDLE_APPROXIMATE","limitations":["limited"],
          "preregistration":{"final_holdout":{"start":"x","end":"y"}},"created_at":"fixed"}
        candidate={"candidate_id":"base","name":"BASELINE","hypothesis":"baseline","configuration":{},"configuration_diff":[],"definition_digest":digest({"base":2})}
        fold={"fold_id":"F1","is_start":"a","is_end":"b","oos_start":"c","oos_end":"d"}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d); db=path/"phase6.db"; store=Phase6Store(db); store.initialize(); store.create_study(definition,[candidate],[fold])
            store.study_result("s","FINALIST_SELECTION",{"status":"NO_ELIGIBLE_FINALIST"})
            repo=ResearchDashboardRepository(path/"missing-runs.db",path/"missing-history.db",db)
            self.assertEqual(repo.phase6_studies()[0]["study_id"],"s")
            detail=repo.phase6_study_detail("s")
            self.assertTrue(detail["research_only"])
            self.assertEqual(detail["verdict"]["verdict"],"NO CANDIDATE ADVANCES")
            self.assertEqual(detail["study_results"]["FINALIST_SELECTION"]["payload"]["status"],"NO_ELIGIBLE_FINALIST")
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM phase6_studies").fetchone()[0],1)


if __name__=="__main__": unittest.main()
