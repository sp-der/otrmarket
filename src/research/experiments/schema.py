EXPERIMENT_SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS experiments (
 experiment_id TEXT PRIMARY KEY, experiment_name TEXT NOT NULL, hypothesis TEXT NOT NULL,
 baseline_run_id TEXT NOT NULL, created_at TEXT NOT NULL, git_commit TEXT NOT NULL,
 data_capture_id TEXT NOT NULL, markets_json TEXT NOT NULL, contracts_json TEXT NOT NULL,
 start_time TEXT NOT NULL, end_time TEXT NOT NULL, replay_mode TEXT NOT NULL,
 fill_model TEXT NOT NULL, ambiguity_policy TEXT NOT NULL, account_profile_json TEXT NOT NULL,
 execution_config_json TEXT NOT NULL, baseline_configuration_json TEXT NOT NULL,
 status TEXT NOT NULL, data_quality_status TEXT NOT NULL, definition_digest TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS experiments_no_update BEFORE UPDATE ON experiments
BEGIN SELECT RAISE(ABORT,'experiment definition is immutable'); END;
CREATE TRIGGER IF NOT EXISTS experiments_no_delete BEFORE DELETE ON experiments
BEGIN SELECT RAISE(ABORT,'experiment definition is immutable'); END;

CREATE TABLE IF NOT EXISTS experiment_candidates (
 candidate_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
 candidate_name TEXT NOT NULL, run_id TEXT NOT NULL, configuration_json TEXT NOT NULL,
 configuration_diff_json TEXT NOT NULL, equivalence_status TEXT NOT NULL,
 created_at TEXT NOT NULL, definition_digest TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS experiment_candidates_no_update BEFORE UPDATE ON experiment_candidates
BEGIN SELECT RAISE(ABORT,'experiment candidate is immutable'); END;
CREATE TRIGGER IF NOT EXISTS experiment_candidates_no_delete BEFORE DELETE ON experiment_candidates
BEGIN SELECT RAISE(ABORT,'experiment candidate is immutable'); END;

CREATE TABLE IF NOT EXISTS experiment_comparisons (
 comparison_id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
 candidate_id TEXT NOT NULL REFERENCES experiment_candidates(candidate_id), created_at TEXT NOT NULL,
 equivalence_status TEXT NOT NULL, first_divergence_json TEXT NOT NULL, baseline_digest TEXT NOT NULL,
 candidate_digest TEXT NOT NULL, comparison_digest TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS experiment_setup_matches (
 match_id INTEGER PRIMARY KEY AUTOINCREMENT, comparison_id INTEGER NOT NULL REFERENCES experiment_comparisons(comparison_id),
 thesis_key TEXT NOT NULL, baseline_setup_id TEXT, candidate_setup_id TEXT, classification TEXT NOT NULL,
 matching_basis TEXT NOT NULL, matching_confidence TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_metric_deltas (
 delta_id INTEGER PRIMARY KEY AUTOINCREMENT, comparison_id INTEGER NOT NULL REFERENCES experiment_comparisons(comparison_id),
 metric TEXT NOT NULL, baseline_value REAL, candidate_value REAL, absolute_delta REAL, percentage_delta REAL,
 UNIQUE(comparison_id,metric)
);
CREATE TABLE IF NOT EXISTS experiment_behavior_deltas (
 delta_id INTEGER PRIMARY KEY AUTOINCREMENT, comparison_id INTEGER NOT NULL REFERENCES experiment_comparisons(comparison_id),
 metric TEXT NOT NULL, baseline_value REAL NOT NULL, candidate_value REAL NOT NULL, absolute_delta REAL NOT NULL,
 UNIQUE(comparison_id,metric)
);
CREATE TABLE IF NOT EXISTS experiment_segment_deltas (
 delta_id INTEGER PRIMARY KEY AUTOINCREMENT, comparison_id INTEGER NOT NULL REFERENCES experiment_comparisons(comparison_id),
 dimension TEXT NOT NULL, segment TEXT NOT NULL, payload_json TEXT NOT NULL,
 UNIQUE(comparison_id,dimension,segment)
);
CREATE TABLE IF NOT EXISTS experiment_verdicts (
 verdict_id INTEGER PRIMARY KEY AUTOINCREMENT, comparison_id INTEGER NOT NULL REFERENCES experiment_comparisons(comparison_id),
 verdict TEXT NOT NULL CHECK(verdict IN ('INCOMPLETE_DATA','INSUFFICIENT_SAMPLE','WORSE','MIXED','PROMISING','NEEDS_OUT_OF_SAMPLE_TEST')),
 reasons_json TEXT NOT NULL, sample_counts_json TEXT NOT NULL, verdict_digest TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS experiment_timeframe_analysis (
 analysis_id INTEGER PRIMARY KEY AUTOINCREMENT, comparison_id INTEGER NOT NULL REFERENCES experiment_comparisons(comparison_id),
 timeframe TEXT NOT NULL, payload_json TEXT NOT NULL, UNIQUE(comparison_id,timeframe)
);
CREATE TRIGGER IF NOT EXISTS experiment_comparisons_no_update BEFORE UPDATE ON experiment_comparisons BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_comparisons_no_delete BEFORE DELETE ON experiment_comparisons BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_setup_matches_no_update BEFORE UPDATE ON experiment_setup_matches BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_setup_matches_no_delete BEFORE DELETE ON experiment_setup_matches BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_metric_deltas_no_update BEFORE UPDATE ON experiment_metric_deltas BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_metric_deltas_no_delete BEFORE DELETE ON experiment_metric_deltas BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_behavior_deltas_no_update BEFORE UPDATE ON experiment_behavior_deltas BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_behavior_deltas_no_delete BEFORE DELETE ON experiment_behavior_deltas BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_segment_deltas_no_update BEFORE UPDATE ON experiment_segment_deltas BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_segment_deltas_no_delete BEFORE DELETE ON experiment_segment_deltas BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_verdicts_no_update BEFORE UPDATE ON experiment_verdicts BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_verdicts_no_delete BEFORE DELETE ON experiment_verdicts BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_timeframe_analysis_no_update BEFORE UPDATE ON experiment_timeframe_analysis BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_timeframe_analysis_no_delete BEFORE DELETE ON experiment_timeframe_analysis BEGIN SELECT RAISE(ABORT,'experiment results are append-only'); END;
"""
