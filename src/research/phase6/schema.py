PHASE6_SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS phase6_studies (
 study_id TEXT PRIMARY KEY, hypothesis TEXT NOT NULL, capture_id TEXT NOT NULL,
 capture_digest TEXT NOT NULL, git_commit TEXT NOT NULL, start_time TEXT NOT NULL,
 end_time TEXT NOT NULL, replay_mode TEXT NOT NULL CHECK(replay_mode='CANDLE_APPROXIMATE'),
 limitations_json TEXT NOT NULL, preregistration_json TEXT NOT NULL,
 created_at TEXT NOT NULL, definition_digest TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS phase6_candidates (
 candidate_id TEXT PRIMARY KEY, study_id TEXT NOT NULL REFERENCES phase6_studies(study_id),
 name TEXT NOT NULL, hypothesis TEXT NOT NULL, configuration_json TEXT NOT NULL,
 configuration_diff_json TEXT NOT NULL, definition_digest TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS phase6_folds (
 study_id TEXT NOT NULL REFERENCES phase6_studies(study_id), fold_id TEXT NOT NULL,
 is_start TEXT NOT NULL, is_end TEXT NOT NULL, oos_start TEXT NOT NULL, oos_end TEXT NOT NULL,
 is_holdout INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(study_id,fold_id)
);
CREATE TABLE IF NOT EXISTS phase6_runs (
 run_id TEXT PRIMARY KEY, study_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
 fold_id TEXT NOT NULL, sample_role TEXT NOT NULL, manifest_json TEXT NOT NULL,
 metrics_json TEXT NOT NULL, segments_json TEXT NOT NULL, behavior_json TEXT NOT NULL,
 run_digest TEXT NOT NULL, decision_digest TEXT NOT NULL, trade_digest TEXT NOT NULL,
 status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS phase6_results (
 result_id INTEGER PRIMARY KEY, study_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
 result_type TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS phase6_verdicts (
 study_id TEXT NOT NULL, candidate_id TEXT NOT NULL, verdict TEXT NOT NULL,
 reasons_json TEXT NOT NULL, payload_json TEXT NOT NULL, digest TEXT NOT NULL UNIQUE,
 PRIMARY KEY(study_id,candidate_id)
);
CREATE TABLE IF NOT EXISTS phase6_study_results (
 result_id INTEGER PRIMARY KEY, study_id TEXT NOT NULL, result_type TEXT NOT NULL,
 payload_json TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 UNIQUE(study_id,result_type)
);
CREATE TRIGGER IF NOT EXISTS phase6_studies_no_update BEFORE UPDATE ON phase6_studies BEGIN SELECT RAISE(ABORT,'Phase 6 definitions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS phase6_studies_no_delete BEFORE DELETE ON phase6_studies BEGIN SELECT RAISE(ABORT,'Phase 6 definitions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS phase6_candidates_no_update BEFORE UPDATE ON phase6_candidates BEGIN SELECT RAISE(ABORT,'Phase 6 candidates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS phase6_candidates_no_delete BEFORE DELETE ON phase6_candidates BEGIN SELECT RAISE(ABORT,'Phase 6 candidates are immutable'); END;
CREATE TRIGGER IF NOT EXISTS phase6_folds_no_update BEFORE UPDATE ON phase6_folds BEGIN SELECT RAISE(ABORT,'Phase 6 folds are immutable'); END;
CREATE TRIGGER IF NOT EXISTS phase6_folds_no_delete BEFORE DELETE ON phase6_folds BEGIN SELECT RAISE(ABORT,'Phase 6 folds are immutable'); END;
"""
