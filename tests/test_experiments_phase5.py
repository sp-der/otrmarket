from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.research.dashboard import ResearchDashboardRepository
from src.research.experiments import (
    BASELINE_PENDING_LIFETIMES, CANDIDATE_A_PENDING_LIFETIMES,
    ExperimentEngine, ExperimentSpec, ExperimentStore, compare_runs,
)
from src.research.experiments.engine import CandidateSpec, configuration_diff, enforce_equivalence, verdict


COMMIT = "9ac13de0590edb2ffca4416dda86cb01c260099f"
CAPTURE = "retained-operation70-phase1"


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(lifetimes, **overrides):
    value = {
        "git_commit": COMMIT, "capture_id": CAPTURE, "start_time": "2026-08-01T00:00:00Z",
        "end_time": "2026-08-02T00:00:00Z", "replay_mode": "CANDLE_APPROXIMATE",
        "fill_model": "SLIPPAGE_MODEL", "ambiguity_policy": "STOP_FIRST",
        "markets": ["NQ", "ES", "GC"], "contracts": ["MNQ SEP26", "MES SEP26", "MGC DEC26"],
        "account_profile": {"starting_balance": 50000, "profile_verification": "RESEARCH_REFERENCE_PROFILE"},
        "execution_config": {"slippage_ticks": 1, "commission": 1.5, "fees": .5},
        "configuration": {"pending_lifetime_bars": dict(lifetimes), "operation": "7.0"},
    }
    value.update(overrides)
    return value


def trace(setup_id, decision, time, thesis="thesis-15m", **payload):
    return {"setup_id": setup_id, "event_time": time, "event_type": decision, "decision": decision,
            "symbol": "NQ", "timeframe": "15m", "strategy_type": "ICT / OTE", "direction": "bullish",
            "setup_grade": "A", "payload": {"thesis_id": thesis, **payload}}


def trade(setup_id, pnl, exit_reason="TARGET"):
    return {"trade_id": f"trade-{setup_id}", "setup_id": setup_id, "status": "CLOSED", "fill_time": "2026-08-01T01:20:00Z",
            "exit_time": "2026-08-01T01:30:00Z", "symbol": "NQ", "direction": "bullish", "strategy_type": "ICT / OTE",
            "setup_grade": "A", "timeframe": "15m", "session": "London", "recovery_state": "NORMAL",
            "net_pnl": pnl, "gross_pnl": pnl + 2, "realized_r": 2 if pnl > 0 else -1, "commission": 1.5,
            "fees": .5, "adverse_slippage_cost": 1, "price_improvement": 0, "actual_fill": 20000,
            "stop_price": 19990, "target_price": 20020, "mfe_r": 2, "mae_r": .4, "exit_reason": exit_reason}


def scenario(candidate_trade=None, candidate_final="FILLED"):
    baseline = [trace("base-1", "SETUP_DETECTED", "2026-08-01T00:00:00Z"),
                trace("base-1", "PENDING_EXPIRED", "2026-08-01T01:00:00Z", bars_elapsed=4, structure_valid_at_expiration="VALID")]
    candidate = [trace("cand-1", "SETUP_DETECTED", "2026-08-01T00:00:00Z"),
                 trace("cand-1", candidate_final, "2026-08-01T01:20:00Z", bars_elapsed=5)]
    return compare_runs(
        experiment_id="pending-v1", candidate_id="candidate-a", baseline_manifest=manifest(BASELINE_PENDING_LIFETIMES),
        candidate_manifest=manifest(CANDIDATE_A_PENDING_LIFETIMES), baseline_traces=baseline, candidate_traces=candidate,
        baseline_trades=[], candidate_trades=[candidate_trade] if candidate_trade else [], baseline_equity=[], candidate_equity=[],
        data_quality_status="INCOMPLETE", minimum_sample=30,
    )


class Phase5ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "experiments.db"
        self.store = ExperimentStore(self.database)
        self.store.initialize()

    def tearDown(self):
        self.temp.cleanup()

    def define(self):
        spec = ExperimentSpec("pending-v1", "Pending Lifetime Research v1",
            "Operation 7.0 may expire higher-timeframe pending setups before structurally valid retracements occur.",
            "baseline-run", COMMIT, CAPTURE, ("NQ", "ES", "GC"), ("MNQ SEP26", "MES SEP26", "MGC DEC26"),
            "2026-08-01", "2026-08-02", "CANDLE_APPROXIMATE", "SLIPPAGE_MODEL", "STOP_FIRST",
            {"starting_balance": 50000}, {"slippage_ticks": 1}, {"pending_lifetime_bars": BASELINE_PENDING_LIFETIMES},
            created_at="2026-08-03T00:00:00Z")
        candidate = CandidateSpec("candidate-a", "Candidate A", "candidate-run",
            {"pending_lifetime_bars": CANDIDATE_A_PENDING_LIFETIMES}, created_at="2026-08-03T00:00:00Z")
        return ExperimentEngine(self.store).define(spec, [candidate])

    def test_experiment_creation_and_candidate_relationship(self):
        result = self.define()
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM experiments").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT experiment_id FROM experiment_candidates").fetchone()[0], "pending-v1")
        self.assertEqual(result["definition"]["data_quality_status"], "INCOMPLETE")

    def test_immutable_definitions(self):
        self.define()
        with sqlite3.connect(self.database) as connection:
            with self.assertRaises(sqlite3.IntegrityError): connection.execute("UPDATE experiments SET hypothesis='changed'")
            with self.assertRaises(sqlite3.IntegrityError): connection.execute("DELETE FROM experiment_candidates")

    def test_config_diff_one_hypothesis(self):
        changes = configuration_diff({"pending_lifetime_bars": BASELINE_PENDING_LIFETIMES}, {"pending_lifetime_bars": CANDIDATE_A_PENDING_LIFETIMES})
        self.assertEqual([row["path"] for row in changes], ["pending_lifetime_bars.15m", "pending_lifetime_bars.1h", "pending_lifetime_bars.1m", "pending_lifetime_bars.5m"])

    def test_equivalent_run_enforcement(self):
        self.assertEqual(enforce_equivalence(manifest(BASELINE_PENDING_LIFETIMES), manifest(CANDIDATE_A_PENDING_LIFETIMES), {f"pending_lifetime_bars.{tf}" for tf in BASELINE_PENDING_LIFETIMES}), "EQUIVALENT")

    def test_mismatched_capture_rejected(self):
        with self.assertRaisesRegex(ValueError, "capture_id"): enforce_equivalence(manifest(BASELINE_PENDING_LIFETIMES), manifest(CANDIDATE_A_PENDING_LIFETIMES, capture_id="other"), set())

    def test_mismatched_date_rejected(self):
        with self.assertRaisesRegex(ValueError, "end_time"): enforce_equivalence(manifest(BASELINE_PENDING_LIFETIMES), manifest(CANDIDATE_A_PENDING_LIFETIMES, end_time="later"), set())

    def test_mismatched_fill_model_rejected(self):
        with self.assertRaisesRegex(ValueError, "fill_model"): enforce_equivalence(manifest(BASELINE_PENDING_LIFETIMES), manifest(CANDIDATE_A_PENDING_LIFETIMES, fill_model="IDEAL_TOUCH"), set())

    def test_non_equivalent_label_is_explicit(self):
        status = enforce_equivalence(manifest(BASELINE_PENDING_LIFETIMES), manifest(CANDIDATE_A_PENDING_LIFETIMES, capture_id="other"), set(), True)
        self.assertEqual(status, "NON_EQUIVALENT")

    def test_recovered_winner(self):
        result = scenario(trade("cand-1", 198))
        self.assertEqual(result["setup_matches"][0]["classification"], "SAME_SETUP_BASELINE_EXPIRED_CANDIDATE_FILLED")
        self.assertEqual(result["timeframe_analysis"]["15m"]["later_winners"], 1)

    def test_extra_loser(self):
        result = scenario(trade("cand-1", -102, "STOP"))
        self.assertEqual(result["setup_matches"][0]["classification"], "SAME_SETUP_BASELINE_EXPIRED_CANDIDATE_FILLED")
        self.assertEqual(result["timeframe_analysis"]["15m"]["later_losers"], 1)

    def test_zombie_setup(self):
        result = scenario(None, "STRUCTURE_INVALIDATED")
        match = result["setup_matches"][0]
        self.assertFalse(match["candidate"].get("trade"))
        self.assertEqual(result["timeframe_analysis"]["15m"]["never_filled"], 1)

    def test_downstream_divergence(self):
        result = scenario(trade("cand-1", 198))
        self.assertEqual(result["first_divergence"]["event_time"], "2026-08-01T01:00:00Z")

    def test_later_unmatched_setup_is_downstream_divergence(self):
        baseline = [trace("base-1", "PENDING_EXPIRED", "2026-08-01T01:00:00Z", thesis="first")]
        candidate = [trace("cand-1", "FILLED", "2026-08-01T01:10:00Z", thesis="first"),
                     trace("cand-2", "SETUP_DETECTED", "2026-08-01T02:00:00Z", thesis="later")]
        result = compare_runs(experiment_id="x", candidate_id="c", baseline_manifest=manifest(BASELINE_PENDING_LIFETIMES),
            candidate_manifest=manifest(CANDIDATE_A_PENDING_LIFETIMES), baseline_traces=baseline, candidate_traces=candidate,
            baseline_trades=[], candidate_trades=[], baseline_equity=[], candidate_equity=[], data_quality_status="INCOMPLETE")
        self.assertIn("DOWNSTREAM_DIVERGENCE", {row["classification"] for row in result["setup_matches"]})

    def test_monetary_and_behavioral_delta(self):
        result = scenario(trade("cand-1", 198))
        self.assertEqual(result["metric_deltas"]["net_pnl"]["absolute_delta"], 198)
        self.assertGreaterEqual(result["behavior_deltas"]["fills"]["candidate"], 1)

    def test_deterministic_rerun_hashes_and_setup_matching(self):
        one, two = scenario(trade("cand-1", 198)), scenario(trade("cand-1", 198))
        self.assertEqual(one["digests"], two["digests"])
        self.assertEqual(one["setup_matches"][0]["matching_confidence"], "HIGH")

    def test_all_required_segments_are_supported(self):
        result = scenario(trade("cand-1", 198))
        for dimension in ("symbol", "direction", "strategy_type", "setup_grade", "timeframe", "session", "recovery_state"):
            self.assertIn(dimension, result["segment_deltas"])

    def test_incomplete_data_and_sample_guards(self):
        result = scenario(trade("cand-1", 198))
        self.assertEqual(result["verdict"]["verdict"], "INCOMPLETE_DATA")
        small = verdict({"candidate": {"total_trades": 2}, "deltas": {}}, {}, "COMPLETE", 30)
        self.assertEqual(small["verdict"], "INSUFFICIENT_SAMPLE")

    def test_retained_capture_cannot_be_marked_complete(self):
        baseline = manifest(BASELINE_PENDING_LIFETIMES)
        candidate = manifest(CANDIDATE_A_PENDING_LIFETIMES)
        result = compare_runs(experiment_id="x", candidate_id="c", baseline_manifest=baseline,
            candidate_manifest=candidate, baseline_traces=[], candidate_traces=[], baseline_trades=[],
            candidate_trades=[], baseline_equity=[], candidate_equity=[], data_quality_status="COMPLETE")
        self.assertEqual(result["verdict"]["verdict"], "INCOMPLETE_DATA")

    def test_no_production_ready_verdict(self):
        self.assertNotIn("PRODUCTION_READY", {"INCOMPLETE_DATA", "INSUFFICIENT_SAMPLE", "WORSE", "MIXED", "PROMISING", "NEEDS_OUT_OF_SAMPLE_TEST"})

    def test_results_persist_append_only_with_dashboard_views(self):
        self.define(); result = scenario(trade("cand-1", 198)); ExperimentEngine(self.store).persist_comparison(result)
        with sqlite3.connect(self.database) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE experiment_verdicts SET verdict='MIXED'")
        repo = ResearchDashboardRepository(self.database, self.root / "missing-history.db")
        self.assertEqual(repo.experiments()[0]["experiment_name"], "Pending Lifetime Research v1")
        detail = repo.experiment_detail("pending-v1")
        self.assertEqual(detail["candidates"][0]["comparison"]["verdict"]["verdict"], "INCOMPLETE_DATA")
        self.assertIn("15m", detail["candidates"][0]["comparison"]["timeframes"])

    def test_dashboard_experiment_surface(self):
        static = Path(__file__).resolve().parents[1] / "src/dashboard/static"
        content = (static / "research.html").read_text() + (static / "research.js").read_text()
        for text in ("Experiment List", "Baseline vs Candidate", "Configuration Diff", "Metric Deltas", "Behavior Deltas", "Matched Setup Explorer", "Divergence Timeline", "Pending Lifetime"):
            self.assertIn(text, content)

    def test_research_api_is_get_only(self):
        from src.dashboard.app import app
        routes = [route for route in app.routes if "/api/research/experiments" in getattr(route, "path", "")]
        self.assertTrue(routes and all(route.methods == {"GET"} for route in routes))

    def test_production_database_unchanged(self):
        production = Path(__file__).resolve().parents[1] / "data/otrmarket.db"
        before = file_digest(production); self.define(); scenario(trade("cand-1", 198)); self.assertEqual(before, file_digest(production))


if __name__ == "__main__":
    unittest.main()
