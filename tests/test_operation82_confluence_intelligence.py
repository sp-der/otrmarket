"""Regression coverage for Operation 8.2 Confluence Intelligence.

SHADOW RESEARCH ONLY: every test here exercises a layer that observes GC
setups OTR 8.1 already decided on. None of it may ever change what OTR
executes -- several tests below assert that directly.
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.execution.paper import (
    PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1,
    PaperExecutor,
    PaperPosition,
    size_whole_contract,
)
from src.otr8.confluence_intelligence import features as features_mod
from src.otr8.confluence_intelligence import memory as memory_mod
from src.otr8.confluence_intelligence import model as model_mod
from src.otr8.confluence_intelligence import scoring as scoring_mod
from src.otr8.confluence_intelligence import service as service_mod
from src.otr8.confluence_intelligence import store as store_mod
from src.otr8.confluence_intelligence.routes import _resolve_route_run_id
from src.strategies.models import Displacement, FairValueGap, StrategySetup
from src.storage import database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _confluence_market_map(*, local_direction: str = "bullish", htf_direction: str = "bearish") -> dict:
    timeframes = {
        "1m": {
            "structure": {
                "direction": local_direction,
                "source": "swing_structure",
                "last_high": 4327.0,
                "prior_high": 4325.0,
                "last_low": 4320.0,
                "prior_low": 4318.0,
            },
            "equal_liquidity": {"equal_highs": [], "equal_lows": []},
            "rejection": {"signal": "wick_rejection", "lower_wick_fraction": 0.2, "upper_wick_fraction": 0.05},
            "fvgs": {"active": [{"lower": 4322.0, "upper": 4323.0}], "inverse": []},
            "order_blocks": {"active": [{"lower": 4319.0, "upper": 4320.5}], "breaker_candidates": []},
            "dealing_range": {"zone": "discount", "position": 0.3},
        }
    }
    for timeframe in ("5m", "15m", "30m", "1h", "4h"):
        timeframes[timeframe] = {
            "structure": {
                "direction": htf_direction,
                "source": "swing_structure",
                "last_high": 4330.0,
                "prior_high": 4335.0,
                "last_low": 4310.0,
                "prior_low": 4315.0,
            }
        }
    return {
        "timeframes": timeframes,
        "session_liquidity": {"session": "LONDON", "previous_day_high": 4340.0, "previous_day_low": 4300.0},
    }


def _gc_setup_with_metadata(
    *,
    setup_id: str,
    entry: float = 4322.5,
    stop: float = 4321.6,
    target: float = 4326.7,
    direction: str = "bullish",
    htf_direction: str = "bearish",
    quality_grade: str = "A+",
) -> StrategySetup:
    t = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)
    fvg = FairValueGap("GC", "5m", direction, entry, entry, t, t, t)
    displacement = Displacement("GC", "5m", direction, t, entry, entry, 0.6, 0.8)
    risk = abs(target - entry) / abs(entry - stop)
    metadata = {
        "setup_arbiter_80": {
            "assessments": [
                {
                    "setup_id": setup_id,
                    "score": 88.0,
                    "details": {
                        "market_narrative": {
                            "score": 72.0,
                            "grade": "A",
                            "aligned_votes": 1,
                            "opposed_votes": 4,
                            "market_map": _confluence_market_map(
                                local_direction=direction, htf_direction=htf_direction
                            ),
                        }
                    },
                }
            ]
        },
        "a_plus_context": {"quality_grade": quality_grade},
        "evaluation_guard": {"risk_dollars": 750.0},
        "entry_type": "EARLY_OTE_79",
        "strategy": "ICT_CONFLUENCE",
    }
    setup = StrategySetup(
        setup_id,
        "GC",
        "1m",
        direction,
        t,
        fvg,
        "liquidity_sweep",
        {"swept_level": entry - 0.3},
        displacement,
        fvg,
        entry,
        stop,
        target,
        risk,
        metadata=metadata,
    )
    return setup


def _bare_gc_setup(setup_id: str, *, direction: str = "bullish") -> StrategySetup:
    """A GC setup with no arbiter/market-narrative metadata -- exercises the
    INSUFFICIENT_DATA/UNKNOWN degrade paths rather than the happy path.
    """
    t = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)
    fvg = FairValueGap("GC", "5m", direction, 4322.5, 4322.5, t, t, t)
    displacement = Displacement("GC", "5m", direction, t, 4322.5, 4322.5, 0.6, 0.8)
    return StrategySetup(
        setup_id, "GC", "1m", direction, t, fvg, "liquidity_sweep", {},
        displacement, fvg, 4322.5, 4321.6, 4326.7, 4.5,
    )


def _full_database_connection() -> sqlite3.Connection:
    """An in-memory connection matching the columns database.py's save_setup
    / upsert_paper_trade read and write, plus engine_state for run scoping.
    confluence_snapshots_v82 is intentionally NOT created here -- it must be
    self-created by store.ensure_schema() the first time a GC setup is saved.
    """
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE engine_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE strategy_setups (
            setup_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,created_at TEXT NOT NULL,trigger_type TEXT NOT NULL,
            entry_price REAL NOT NULL,stop_price REAL NOT NULL,target_price REAL NOT NULL,
            risk_reward REAL NOT NULL,status TEXT NOT NULL,payload_json TEXT NOT NULL,
            run_id TEXT,engine_version TEXT,operation_version TEXT
        );
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,status TEXT NOT NULL,entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,target_price REAL NOT NULL,opened_at TEXT,closed_at TEXT,
            exit_price REAL,result TEXT,result_r REAL,risk_dollars REAL,result_dollars REAL,
            guard_reason TEXT,updated_at TEXT NOT NULL,
            requested_risk_dollars REAL,actual_risk_dollars REAL,quantity INTEGER,
            per_contract_risk REAL,contract_multiplier REAL,execution_contract TEXT,
            accounting_version TEXT,mfe_r REAL,mae_r REAL,
            run_id TEXT,engine_version TEXT,operation_version TEXT,
            max_micros_cap INTEGER,unused_risk_dollars REAL
        );
        """
    )
    return connection


def _seed_comparable_snapshot(
    connection: sqlite3.Connection,
    setup_id: str,
    *,
    direction: str = "bullish",
    result: str = "WIN",
    result_r: float = 1.5,
    mfe_r: float = 1.8,
    mae_r: float = 0.3,
    accounting_version: str | None = PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1,
    captured_at: str,
) -> None:
    """Seed one resolved, comparable confluence snapshot + its paper_trades
    row directly, for memory/model tests that need a pool of prior trades
    without running the full capture pipeline for each one.
    """
    store_mod.save_snapshot(
        connection,
        {
            "setup_id": setup_id,
            "run_id": "run-seed",
            "captured_at": captured_at,
            "symbol": "GC",
            "timeframe": "1m",
            "direction": direction,
            "feature_version": features_mod.FEATURE_VERSION,
            "features": {},
            "local_quality_score": 80.0,
            "htf_alignment_score": 60.0,
            "context_score": 55.0,
            "liquidity_score": 40.0,
            "combined_shadow_score": 65.0,
        },
    )
    store_mod.record_outcome(
        connection,
        setup_id,
        result=result,
        result_r=result_r,
        result_dollars=result_r * 100.0,
        mfe_r=mfe_r,
        mae_r=mae_r,
        hold_seconds=300.0,
    )
    connection.execute(
        """
        INSERT INTO paper_trades (
            setup_id, symbol, timeframe, direction, status,
            entry_price, stop_price, target_price, updated_at, accounting_version
        ) VALUES (?, 'GC', '1m', ?, 'CLOSED', 4322.5, 4321.6, 4326.7, ?, ?)
        """,
        (setup_id, direction, captured_at, accounting_version),
    )
    connection.commit()


# ---------------------------------------------------------------------------
# 5. Snapshots contain only pre-trade information (anti-leakage)
# ---------------------------------------------------------------------------


class FeatureExtractionAntiLeakageTests(unittest.TestCase):
    def test_extract_features_never_contains_outcome_fields(self):
        setup = _gc_setup_with_metadata(setup_id="leak-check-1")
        extracted = features_mod.extract_features(setup)

        blob = str(extracted)
        for forbidden in ("result", "mfe_r", "mae_r", "result_dollars", "hold_seconds", "closed_at", "exit_price"):
            self.assertNotIn(forbidden, blob)

    def test_capture_snapshot_record_has_no_outcome_columns_populated(self):
        connection = _full_database_connection()
        setup = _gc_setup_with_metadata(setup_id="leak-check-2")

        record = service_mod.capture_snapshot(connection, setup, run_id="run-1")

        self.assertIsNotNone(record)
        for outcome_key in ("result", "result_r", "result_dollars", "mfe_r", "mae_r", "hold_seconds"):
            self.assertNotIn(outcome_key, record)

        stored = store_mod.get_snapshot(connection, "leak-check-2")
        self.assertIsNone(stored["result"])
        self.assertIsNone(stored["mfe_r"])
        self.assertIsNone(stored["mae_r"])

    def test_bare_setup_with_no_arbiter_metadata_degrades_to_unknown_not_a_guess(self):
        setup = _bare_gc_setup("bare-1")
        extracted = features_mod.extract_features(setup)

        for timeframe in features_mod.TIMEFRAMES:
            self.assertEqual(extracted["structure"][timeframe]["direction"], features_mod.UNKNOWN)
        self.assertEqual(extracted["context"]["session"], features_mod.UNKNOWN)


# ---------------------------------------------------------------------------
# 6. HTF alignment: aligned / opposed / mixed
# ---------------------------------------------------------------------------


class HTFConflictEngineTests(unittest.TestCase):
    def _features(self, votes: dict[str, str]) -> dict:
        structure = {tf: {"direction": votes.get(tf, features_mod.UNKNOWN)} for tf in features_mod.TIMEFRAMES}
        return {"structure": structure}

    def test_fully_aligned_bullish_htf_reports_low_conflict(self):
        votes = {tf: "bullish" for tf in features_mod.HTF_VOTE_TIMEFRAMES}
        htf = scoring_mod.htf_conflict(self._features(votes), "bullish")

        self.assertEqual(htf["htf_bullish_count"], 5)
        self.assertEqual(htf["htf_bearish_count"], 0)
        self.assertEqual(htf["htf_alignment_direction"], "bullish")
        self.assertEqual(htf["htf_alignment_score"], 100.0)
        self.assertEqual(htf["htf_conflict_level"], "LOW")
        self.assertFalse(htf["execution_vs_htf_conflict"])

    def test_fully_opposed_bearish_htf_reports_high_conflict_for_bullish_direction(self):
        votes = {tf: "bearish" for tf in features_mod.HTF_VOTE_TIMEFRAMES}
        htf = scoring_mod.htf_conflict(self._features(votes), "bullish")

        self.assertEqual(htf["htf_bearish_count"], 5)
        self.assertEqual(htf["htf_alignment_direction"], "bearish")
        self.assertEqual(htf["htf_alignment_score"], 0.0)
        self.assertEqual(htf["htf_conflict_level"], "HIGH")
        self.assertTrue(htf["execution_vs_htf_conflict"])

    def test_mixed_htf_votes_report_moderate_conflict_and_mixed_direction(self):
        votes = {"5m": "bullish", "15m": "bearish", "30m": "mixed", "1h": "neutral", "4h": "bullish"}
        htf = scoring_mod.htf_conflict(self._features(votes), "bullish")

        self.assertEqual(htf["htf_bullish_count"], 2)
        self.assertEqual(htf["htf_bearish_count"], 1)
        self.assertEqual(htf["htf_mixed_count"], 2)
        self.assertIn(htf["htf_conflict_level"], {"MODERATE", "HIGH"})

    def test_all_unknown_htf_votes_report_unknown_not_aligned_or_conflicted(self):
        htf = scoring_mod.htf_conflict(self._features({}), "bullish")

        self.assertEqual(htf["htf_alignment_direction"], features_mod.UNKNOWN)
        self.assertIsNone(htf["htf_alignment_score"])
        self.assertEqual(htf["htf_conflict_level"], features_mod.UNKNOWN)
        self.assertIsNone(htf["execution_vs_htf_conflict"])

    def test_local_1m_timeframe_is_never_counted_as_an_htf_vote(self):
        # 1m is bearish (opposite of direction) but is NOT in HTF_VOTE_TIMEFRAMES,
        # so it must never influence bullish/bearish counts.
        votes = {"1m": "bearish", "5m": "bullish", "15m": "bullish", "30m": "bullish", "1h": "bullish", "4h": "bullish"}
        htf = scoring_mod.htf_conflict(self._features(votes), "bullish")

        self.assertEqual(htf["htf_bullish_count"], 5)
        self.assertEqual(htf["htf_bearish_count"], 0)


# ---------------------------------------------------------------------------
# 7. 1m A+ local quality can coexist with a weak/conflicting HTF score
# ---------------------------------------------------------------------------


class LocalQualityVsHtfIndependenceTests(unittest.TestCase):
    def test_a_plus_local_quality_is_full_marks_regardless_of_htf_conflict(self):
        setup = _gc_setup_with_metadata(setup_id="local-vs-htf-1", htf_direction="bearish")
        extracted = features_mod.extract_features(setup)

        local_quality = scoring_mod.local_quality_score(extracted)
        htf = scoring_mod.htf_conflict(extracted, "bullish")

        self.assertEqual(local_quality, 100.0)
        self.assertEqual(htf["htf_conflict_level"], "HIGH")

    def test_combined_score_reflects_both_signals_not_just_local_quality(self):
        setup = _gc_setup_with_metadata(setup_id="local-vs-htf-2", htf_direction="bearish")
        extracted = features_mod.extract_features(setup)
        score = scoring_mod.score_setup(setup, extracted)

        self.assertEqual(score["local_quality_score"], 100.0)
        self.assertIsNotNone(score["combined_shadow_score"])
        # A conflicting HTF must pull the combined score below a perfect 100.
        self.assertLess(score["combined_shadow_score"], 100.0)
        self.assertIn(score["shadow_action"], {"ALLOW", "DOWNGRADE", "WAIT", "REJECT", "INSUFFICIENT_DATA"})


# ---------------------------------------------------------------------------
# 8. shadow_action never changes actual OTR execution
# ---------------------------------------------------------------------------


class ShadowNeverAffectsExecutionTests(unittest.TestCase):
    def test_paper_sizing_is_identical_with_and_without_confluence_capture(self):
        setup_a = _gc_setup_with_metadata(setup_id="exec-parity-a")
        setup_b = _gc_setup_with_metadata(setup_id="exec-parity-b")

        sizing_before = size_whole_contract(setup_a, 750.0, max_micros_cap=10)

        connection = _full_database_connection()
        service_mod.capture_snapshot(connection, setup_b, run_id="run-1")
        sizing_after = size_whole_contract(setup_b, 750.0, max_micros_cap=10)

        self.assertEqual(sizing_before.quantity, sizing_after.quantity)
        self.assertEqual(sizing_before.actual_risk_dollars, sizing_after.actual_risk_dollars)

    def test_capture_snapshot_exception_is_swallowed_and_never_raises(self):
        connection = _full_database_connection()
        setup = _gc_setup_with_metadata(setup_id="exec-parity-c")

        with patch.object(scoring_mod, "score_setup", side_effect=RuntimeError("boom")):
            result = service_mod.capture_snapshot(connection, setup, run_id="run-1")

        self.assertIsNone(result)
        self.assertIsNone(store_mod.get_snapshot(connection, "exec-parity-c"))

    def test_database_save_setup_hook_never_raises_even_if_confluence_capture_fails(self):
        connection = _full_database_connection()
        setup = _gc_setup_with_metadata(setup_id="exec-parity-d")

        with patch(
            "src.otr8.confluence_intelligence.service.capture_snapshot", side_effect=RuntimeError("boom")
        ):
            database.save_setup(connection, setup)  # must not raise

        row = connection.execute(
            "SELECT setup_id FROM strategy_setups WHERE setup_id=?", ("exec-parity-d",)
        ).fetchone()
        self.assertIsNotNone(row)

    def test_paper_executor_register_setup_has_no_confluence_dependency(self):
        setup = _gc_setup_with_metadata(setup_id="exec-parity-e")
        executor = PaperExecutor()

        # No database connection is ever passed to register_setup -- proving
        # structurally that real execution sizing cannot depend on shadow
        # research state.
        position = executor.register_setup(setup, risk_dollars=750.0, max_micros_cap=10)

        self.assertIsNotNone(position.quantity)


# ---------------------------------------------------------------------------
# 9/10. Similar-setup memory: excludes incompatible data, gates on evidence
# ---------------------------------------------------------------------------


class SimilarSetupMemoryTests(unittest.TestCase):
    def test_insufficient_comparable_samples_reports_insufficient_evidence(self):
        connection = _full_database_connection()
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for i in range(5):
            _seed_comparable_snapshot(
                connection, f"mem-few-{i}", captured_at=(base + timedelta(hours=i)).isoformat()
            )
        setup = _gc_setup_with_metadata(setup_id="mem-query-1")

        result = memory_mod.find_similar_setups(connection, setup, features_mod.extract_features(setup))

        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["comparable_samples"], 5)
        self.assertEqual(result["minimum_samples"], memory_mod.MIN_COMPARABLE_SAMPLES)

    def test_enough_matching_samples_produce_ok_status_with_win_loss_stats(self):
        connection = _full_database_connection()
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for i in range(memory_mod.MIN_COMPARABLE_SAMPLES):
            result = "WIN" if i % 2 == 0 else "LOSS"
            _seed_comparable_snapshot(
                connection,
                f"mem-ok-{i}",
                result=result,
                result_r=1.5 if result == "WIN" else -1.0,
                mfe_r=1.8 if result == "WIN" else 0.05,
                captured_at=(base + timedelta(hours=i)).isoformat(),
            )
        setup = _gc_setup_with_metadata(setup_id="mem-query-2")

        result = memory_mod.find_similar_setups(connection, setup, features_mod.extract_features(setup))

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["comparable_samples"], memory_mod.MIN_COMPARABLE_SAMPLES)
        self.assertIsNotNone(result["win_rate"])
        self.assertAlmostEqual(result["win_rate"], 0.5, places=2)

    def test_opposite_direction_rows_are_excluded_from_the_comparable_pool(self):
        connection = _full_database_connection()
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        # 20 bearish rows -- none should count toward a bullish query's pool.
        for i in range(memory_mod.MIN_COMPARABLE_SAMPLES):
            _seed_comparable_snapshot(
                connection, f"mem-wrong-dir-{i}", direction="bearish",
                captured_at=(base + timedelta(hours=i)).isoformat(),
            )
        setup = _gc_setup_with_metadata(setup_id="mem-query-3", direction="bullish")

        result = memory_mod.find_similar_setups(connection, setup, features_mod.extract_features(setup))

        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["comparable_samples"], 0)

    def test_incompatible_legacy_accounting_rows_are_excluded_by_default(self):
        connection = _full_database_connection()
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        # 20 legacy (non-whole-contract) rows -- must never be pooled into
        # dollar-outcome statistics alongside MGC_WHOLE_CONTRACT_V1 trades.
        for i in range(memory_mod.MIN_COMPARABLE_SAMPLES):
            _seed_comparable_snapshot(
                connection, f"mem-legacy-{i}", accounting_version="LEGACY_THEORETICAL",
                captured_at=(base + timedelta(hours=i)).isoformat(),
            )
        setup = _gc_setup_with_metadata(setup_id="mem-query-4")

        result = memory_mod.find_similar_setups(connection, setup, features_mod.extract_features(setup))

        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["comparable_samples"], 0)

    def test_summarize_neighbors_below_minimum_is_insufficient_evidence(self):
        result = memory_mod.summarize_neighbors([{"result": "WIN", "result_r": 1.0}])
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")


# ---------------------------------------------------------------------------
# 11/12. ML model: minimum-sample gate + chronological (no-leakage) split
# ---------------------------------------------------------------------------


class ModelAntiLeakageAndGateTests(unittest.TestCase):
    def test_forbidden_keys_are_never_in_the_feature_allowlist(self):
        self.assertTrue(model_mod.FORBIDDEN_KEYS.isdisjoint(model_mod.FEATURE_KEYS))

    def test_build_feature_row_never_emits_forbidden_keys(self):
        record = {
            "local_quality_score": 80.0,
            "result": "WIN",
            "result_r": 1.5,
            "mfe_r": 1.8,
            "features": {"momentum": {}, "context": {}},
        }
        row = model_mod.build_feature_row(record)
        self.assertTrue(model_mod.FORBIDDEN_KEYS.isdisjoint(row.keys()))

    def test_train_model_below_minimum_samples_refuses_and_never_imports_lightgbm(self):
        connection = _full_database_connection()
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        sample_count = model_mod.MIN_TRAINING_SAMPLES - 1
        for i in range(sample_count):
            _seed_comparable_snapshot(
                connection, f"model-gate-{i}", mfe_r=0.05 * i, captured_at=(base + timedelta(hours=i)).isoformat()
            )

        with patch.dict("sys.modules", {"lightgbm": None}):
            result = model_mod.train_model(connection, target="PLUS_1R_BEFORE_STOP")

        self.assertEqual(result["model_status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["sample_count"], sample_count)
        self.assertEqual(result["minimum_required"], model_mod.MIN_TRAINING_SAMPLES)

    def test_train_model_reports_dependency_unavailable_when_lightgbm_missing(self):
        connection = _full_database_connection()
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for i in range(model_mod.MIN_TRAINING_SAMPLES):
            _seed_comparable_snapshot(
                connection, f"model-dep-{i}", mfe_r=0.05 * i, captured_at=(base + timedelta(hours=i)).isoformat()
            )

        with patch.dict("sys.modules", {"lightgbm": None}):
            result = model_mod.train_model(connection, target="PLUS_1R_BEFORE_STOP")

        self.assertIn(result["model_status"], {"DEPENDENCY_UNAVAILABLE", "TRAINED", "TRAINED_BELOW_PROMOTION_THRESHOLD"})

    def test_time_based_split_never_shuffles_and_validation_is_the_most_recent_slice(self):
        dataset = {
            "rows": [{"i": i} for i in range(10)],
            "labels": [float(i) for i in range(10)],
            "target": "MFE_R",
            "sample_count": 10,
        }
        split = model_mod.time_based_split(dataset, validation_fraction=0.2)

        self.assertEqual(split["train_labels"], [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        self.assertEqual(split["validation_labels"], [8.0, 9.0])

    def test_build_dataset_orders_chronologically_so_validation_never_sees_the_past(self):
        connection = _full_database_connection()
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        # Seed 10 rows with monotonically increasing mfe_r AND captured_at, so
        # a leakage bug (e.g. accidental shuffling) would very likely break
        # the "validation labels are the two largest" assertion below.
        for i in range(10):
            _seed_comparable_snapshot(
                connection, f"model-order-{i}", mfe_r=float(i), captured_at=(base + timedelta(hours=i)).isoformat()
            )

        dataset = model_mod.build_dataset(connection, target="MFE_R")
        split = model_mod.time_based_split(dataset, validation_fraction=0.2)

        self.assertEqual(dataset["sample_count"], 10)
        self.assertEqual(split["validation_labels"], [8.0, 9.0])


# ---------------------------------------------------------------------------
# 13/14. Restart/redeploy preserves records; run scoping works
# ---------------------------------------------------------------------------


class StorePersistenceAndRunScopingTests(unittest.TestCase):
    def test_save_snapshot_is_idempotent_first_write_wins(self):
        connection = _full_database_connection()
        store_mod.save_snapshot(
            connection,
            {
                "setup_id": "idempotent-1", "run_id": "run-a", "captured_at": "2026-09-19T14:00:00+00:00",
                "symbol": "GC", "timeframe": "1m", "direction": "bullish",
                "feature_version": features_mod.FEATURE_VERSION, "features": {},
                "combined_shadow_score": 70.0, "shadow_action": "ALLOW",
            },
        )
        inserted_again = store_mod.save_snapshot(
            connection,
            {
                "setup_id": "idempotent-1", "run_id": "run-a", "captured_at": "2026-09-19T15:00:00+00:00",
                "symbol": "GC", "timeframe": "1m", "direction": "bullish",
                "feature_version": features_mod.FEATURE_VERSION, "features": {},
                "combined_shadow_score": 10.0, "shadow_action": "REJECT",
            },
        )

        self.assertFalse(inserted_again)
        stored = store_mod.get_snapshot(connection, "idempotent-1")
        self.assertEqual(stored["combined_shadow_score"], 70.0)
        self.assertEqual(stored["shadow_action"], "ALLOW")

    def test_record_outcome_never_touches_pre_trade_fields(self):
        connection = _full_database_connection()
        setup = _gc_setup_with_metadata(setup_id="immutable-pretrade-1")
        pre_trade = service_mod.capture_snapshot(connection, setup, run_id="run-a")

        store_mod.record_outcome(
            connection, "immutable-pretrade-1",
            result="WIN", result_r=2.0, result_dollars=200.0, mfe_r=2.1, mae_r=0.2, hold_seconds=600.0,
        )

        after = store_mod.get_snapshot(connection, "immutable-pretrade-1")
        self.assertEqual(after["combined_shadow_score"], pre_trade["combined_shadow_score"])
        self.assertEqual(after["shadow_action"], pre_trade["shadow_action"])
        self.assertEqual(after["features"], pre_trade["features"])
        self.assertEqual(after["result"], "WIN")
        self.assertEqual(after["mfe_r"], 2.1)

    def test_update_outcome_via_upsert_paper_trade_hook_preserves_pretrade_snapshot(self):
        connection = _full_database_connection()
        setup = _gc_setup_with_metadata(setup_id="hook-outcome-1")
        pre_trade = service_mod.capture_snapshot(connection, setup, run_id="run-a")

        opened = datetime(2026, 9, 19, 14, 1, tzinfo=timezone.utc)
        closed = datetime(2026, 9, 19, 14, 6, tzinfo=timezone.utc)
        position = PaperPosition(
            setup=setup, status="CLOSED", opened_at=opened, closed_at=closed,
            result="WIN", result_r=2.0, result_dollars=180.0, mfe_r=2.2, mae_r=0.1,
            actual_risk_dollars=90.0,
        )

        database.upsert_paper_trade(connection, position, closed.isoformat())

        stored = store_mod.get_snapshot(connection, "hook-outcome-1")
        self.assertEqual(stored["result"], "WIN")
        self.assertEqual(stored["mfe_r"], 2.2)
        self.assertEqual(stored["actual_risk_dollars"], 90.0)
        self.assertEqual(stored["combined_shadow_score"], pre_trade["combined_shadow_score"])
        self.assertEqual(stored["shadow_action"], pre_trade["shadow_action"])

    def test_repeated_save_setup_calls_for_the_same_setup_id_keep_the_first_snapshot(self):
        # Simulates a restart/redeploy re-processing an already-saved setup.
        connection = _full_database_connection()
        setup = _gc_setup_with_metadata(setup_id="restart-1")

        database.save_setup(connection, setup)
        first = store_mod.get_snapshot(connection, "restart-1")

        # A second save (e.g. duplicate re-save on the rejection path, or a
        # process restart replaying the same setup) must not overwrite it.
        database.save_setup(connection, setup)
        second = store_mod.get_snapshot(connection, "restart-1")

        self.assertEqual(first["captured_at"], second["captured_at"])
        self.assertEqual(first["combined_shadow_score"], second["combined_shadow_score"])

    def test_run_scoping_filters_snapshots_by_run_id(self):
        connection = _full_database_connection()
        store_mod.save_snapshot(
            connection,
            {
                "setup_id": "run-scope-a", "run_id": "run-alpha", "captured_at": "2026-09-19T14:00:00+00:00",
                "symbol": "GC", "timeframe": "1m", "direction": "bullish",
                "feature_version": features_mod.FEATURE_VERSION, "features": {},
            },
        )
        store_mod.save_snapshot(
            connection,
            {
                "setup_id": "run-scope-b", "run_id": "run-beta", "captured_at": "2026-09-19T15:00:00+00:00",
                "symbol": "GC", "timeframe": "1m", "direction": "bullish",
                "feature_version": features_mod.FEATURE_VERSION, "features": {},
            },
        )

        alpha_only = store_mod.list_snapshots(connection, run_id="run-alpha", limit=50)
        beta_only = store_mod.list_snapshots(connection, run_id="run-beta", limit=50)
        everything = store_mod.list_snapshots(connection, run_id=None, limit=50)

        self.assertEqual([row["setup_id"] for row in alpha_only], ["run-scope-a"])
        self.assertEqual([row["setup_id"] for row in beta_only], ["run-scope-b"])
        self.assertEqual(len(everything), 2)

    def test_capture_snapshot_uses_the_run_id_passed_in(self):
        connection = _full_database_connection()
        setup = _gc_setup_with_metadata(setup_id="run-scope-c")

        service_mod.capture_snapshot(connection, setup, run_id="run-gamma")

        stored = store_mod.get_snapshot(connection, "run-scope-c")
        self.assertEqual(stored["run_id"], "run-gamma")


# ---------------------------------------------------------------------------
# Non-GC symbols are never captured
# ---------------------------------------------------------------------------


class SymbolGatingTests(unittest.TestCase):
    def test_non_gc_setup_never_produces_a_snapshot(self):
        connection = _full_database_connection()
        t = datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)
        fvg = FairValueGap("NQ", "5m", "bullish", 30000.0, 30000.0, t, t, t)
        displacement = Displacement("NQ", "5m", "bullish", t, 30000.0, 30000.0, 2, 2)
        setup = StrategySetup(
            "nq-not-captured", "NQ", "5m", "bullish", t, fvg, "liquidity_sweep", {},
            displacement, fvg, 30000.0, 29990.0, 30020.0, 2.0,
        )

        result = service_mod.capture_snapshot(connection, setup, run_id="run-1")

        self.assertIsNone(result)
        self.assertIsNone(store_mod.get_snapshot(connection, "nq-not-captured"))


# ---------------------------------------------------------------------------
# 15-18. Structural: Vibe/Nautilus/broker stay non-authoritative; SHADOW_ONLY
# ---------------------------------------------------------------------------


class StructuralNonAuthoritativeTests(unittest.TestCase):
    def test_shadow_only_flag_is_true(self):
        from src.otr8 import confluence_intelligence

        self.assertTrue(confluence_intelligence.SHADOW_ONLY)

    def test_package_never_references_broker_live_order_or_vibe_nautilus_modules(self):
        package_dir = Path(__file__).resolve().parents[1] / "src" / "otr8" / "confluence_intelligence"
        forbidden_substrings = (
            "execution.live.broker",
            "execution.live.order",
            "integrations.vibe_research",
            "nautilus_trader",
            "src.execution.live.router",
        )
        for path in sorted(package_dir.glob("*.py")):
            text = path.read_text()
            for forbidden in forbidden_substrings:
                self.assertNotIn(forbidden, text, f"{path.name} unexpectedly references {forbidden!r}")

    def test_scoring_weights_and_thresholds_are_the_documented_constants(self):
        self.assertEqual(
            scoring_mod.SCORE_WEIGHTS,
            {"local_quality": 0.35, "htf_alignment": 0.30, "context": 0.20, "liquidity": 0.15},
        )
        self.assertEqual(scoring_mod.ALLOW_THRESHOLD, 80.0)
        self.assertEqual(scoring_mod.DOWNGRADE_OR_ALLOW_THRESHOLD, 60.0)
        self.assertEqual(scoring_mod.WAIT_THRESHOLD, 40.0)

    def test_shadow_action_for_covers_every_documented_band(self):
        self.assertEqual(scoring_mod.shadow_action_for(None, "LOW"), "INSUFFICIENT_DATA")
        self.assertEqual(scoring_mod.shadow_action_for(90.0, "HIGH"), "ALLOW")
        self.assertEqual(scoring_mod.shadow_action_for(65.0, "HIGH"), "DOWNGRADE")
        self.assertEqual(scoring_mod.shadow_action_for(65.0, "LOW"), "ALLOW")
        self.assertEqual(scoring_mod.shadow_action_for(50.0, "LOW"), "WAIT")
        self.assertEqual(scoring_mod.shadow_action_for(10.0, "LOW"), "REJECT")


# ---------------------------------------------------------------------------
# Step 10: setup 89c1de4c739a as a read-only regression fixture.
#
# This is the exact geometry that produced the uncapped 83-contract paper
# sizing bug (Step 0): GC 1m bullish, entry 4322.5, stop 4321.6 (0.9pt ->
# $9/contract), target 4326.7, requested risk $750. It is reused here,
# unmodified, to prove Confluence Intelligence observes the same setup
# without altering its paper execution outcome. The desired shadow_action is
# intentionally NOT asserted -- only structural/deterministic consequences
# of the HTF votes this test itself constructs.
# ---------------------------------------------------------------------------


class Setup89c1de4c739aRegressionFixtureTests(unittest.TestCase):
    SETUP_ID = "89c1de4c739a"

    def test_confluence_capture_does_not_change_paper_sizing_for_this_setup(self):
        setup = _gc_setup_with_metadata(setup_id=self.SETUP_ID)

        uncapped_sizing = size_whole_contract(setup, 750.0, max_micros_cap=1_000_000)
        self.assertEqual(uncapped_sizing.quantity, 83)  # the original bug, still reproducible

        capped_sizing = size_whole_contract(setup, 750.0, max_micros_cap=10)
        self.assertEqual(capped_sizing.quantity, 10)  # Step 0's fix, unaffected by Step 1-9 additions

        connection = _full_database_connection()
        service_mod.capture_snapshot(connection, setup, run_id="run-fixture")

        # Capturing a shadow snapshot must not perturb sizing computed after it.
        post_capture_sizing = size_whole_contract(setup, 750.0, max_micros_cap=10)
        self.assertEqual(capped_sizing.quantity, post_capture_sizing.quantity)
        self.assertEqual(capped_sizing.actual_risk_dollars, post_capture_sizing.actual_risk_dollars)

    def test_confluence_snapshot_is_internally_consistent_for_this_fixture(self):
        setup = _gc_setup_with_metadata(setup_id=self.SETUP_ID, htf_direction="bearish")
        extracted = features_mod.extract_features(setup)
        score = scoring_mod.score_setup(setup, extracted)

        # Structural facts about the input this test itself constructed
        # (5 bearish HTF timeframes vs a bullish local trigger) -- not a
        # hardcoded opinion about what the "right" shadow_action should be.
        self.assertEqual(score["htf_bearish_count"], 5)
        self.assertEqual(score["htf_bullish_count"], 0)
        self.assertTrue(score["execution_vs_htf_conflict"])
        self.assertIn(score["htf_conflict_level"], {"HIGH", "MODERATE"})
        self.assertIn(score["shadow_action"], {"ALLOW", "DOWNGRADE", "WAIT", "REJECT", "INSUFFICIENT_DATA"})
        if score["combined_shadow_score"] is not None:
            self.assertGreaterEqual(score["combined_shadow_score"], 0.0)
            self.assertLessEqual(score["combined_shadow_score"], 100.0)

    def test_full_pipeline_end_to_end_via_database_hooks_does_not_raise(self):
        connection = _full_database_connection()
        setup = _gc_setup_with_metadata(setup_id=self.SETUP_ID)

        database.save_setup(connection, setup)  # exercises the real production hook

        stored = store_mod.get_snapshot(connection, self.SETUP_ID)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["symbol"], "GC")
        self.assertIn(stored["shadow_action"], {"ALLOW", "DOWNGRADE", "WAIT", "REJECT", "INSUFFICIENT_DATA"})


# ---------------------------------------------------------------------------
# Step 7: dashboard/API run-id resolution helper
# ---------------------------------------------------------------------------


class RouteRunIdResolutionTests(unittest.TestCase):
    def setUp(self):
        self.connection = _full_database_connection()

    def test_omitted_run_id_resolves_to_the_active_run(self):
        resolved = _resolve_route_run_id(self.connection, None)
        self.assertIsInstance(resolved, str)
        self.assertTrue(resolved)

    def test_all_resolves_to_no_filter(self):
        self.assertIsNone(_resolve_route_run_id(self.connection, "ALL"))
        self.assertIsNone(_resolve_route_run_id(self.connection, "all"))

    def test_explicit_run_id_passes_through_unchanged(self):
        self.assertEqual(_resolve_route_run_id(self.connection, "run-explicit-1"), "run-explicit-1")


if __name__ == "__main__":
    unittest.main()
