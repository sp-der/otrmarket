RUN_SCHEMA_SQL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS backtest_runs (
 run_id TEXT PRIMARY KEY,
 parent_run_id TEXT,
 git_commit TEXT NOT NULL,
 capture_id TEXT NOT NULL,
 markets_json TEXT NOT NULL,
 contracts_json TEXT NOT NULL,
 start_time TEXT NOT NULL,
 end_time TEXT NOT NULL,
 timeframes_json TEXT NOT NULL,
 configuration_json TEXT NOT NULL,
 account_profile_json TEXT NOT NULL,
 engine_version TEXT NOT NULL,
 replay_mode TEXT NOT NULL CHECK(replay_mode IN ('TICK_EXACT','CANDLE_APPROXIMATE')),
 fill_model TEXT NOT NULL,
 ambiguity_policy TEXT NOT NULL,
 execution_config_json TEXT NOT NULL,
 pending_lifetime_bars_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 status TEXT NOT NULL,
 decision_digest TEXT,
 ledger_path TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS backtest_runs_no_update_manifest
BEFORE UPDATE OF parent_run_id,git_commit,capture_id,markets_json,contracts_json,start_time,end_time,
 timeframes_json,configuration_json,account_profile_json,engine_version,replay_mode,fill_model,ambiguity_policy,
 execution_config_json,pending_lifetime_bars_json,created_at,ledger_path
ON backtest_runs BEGIN SELECT RAISE(ABORT,'run manifest is immutable'); END;
CREATE TRIGGER IF NOT EXISTS backtest_runs_no_delete
BEFORE DELETE ON backtest_runs BEGIN SELECT RAISE(ABORT,'run manifest is immutable'); END;

CREATE TABLE IF NOT EXISTS decision_traces (
 trace_id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_id TEXT NOT NULL REFERENCES backtest_runs(run_id),
 sequence_no INTEGER NOT NULL,
 event_time TEXT NOT NULL,
 event_type TEXT NOT NULL,
 symbol TEXT,
 timeframe TEXT,
 strategy_type TEXT,
 direction TEXT,
 setup_id TEXT,
 setup_grade TEXT,
 catalyst_json TEXT NOT NULL DEFAULT '{}',
 displacement_json TEXT NOT NULL DEFAULT '{}',
 fvg_json TEXT NOT NULL DEFAULT '{}',
 ote_json TEXT NOT NULL DEFAULT '{}',
 smt_json TEXT NOT NULL DEFAULT '{}',
 htf_context_json TEXT NOT NULL DEFAULT '{}',
 session_json TEXT NOT NULL DEFAULT '{}',
 recovery_json TEXT NOT NULL DEFAULT '{}',
 quality_score REAL,
 risk_reward REAL,
 decision TEXT,
 reason TEXT,
 payload_json TEXT NOT NULL,
 UNIQUE(run_id,sequence_no)
);
CREATE TRIGGER IF NOT EXISTS decision_traces_no_update
BEFORE UPDATE ON decision_traces BEGIN SELECT RAISE(ABORT,'decision trace is immutable'); END;
CREATE TRIGGER IF NOT EXISTS decision_traces_no_delete
BEFORE DELETE ON decision_traces BEGIN SELECT RAISE(ABORT,'decision trace is immutable'); END;

CREATE TABLE IF NOT EXISTS research_trades (
 trade_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES backtest_runs(run_id), setup_id TEXT NOT NULL,
 symbol TEXT NOT NULL, execution_contract TEXT NOT NULL, strategy_type TEXT, timeframe TEXT, session TEXT,
 direction TEXT NOT NULL, setup_grade TEXT, quality_score REAL, recovery_state TEXT,
 signal_time TEXT NOT NULL, pending_time TEXT, fill_time TEXT, exit_time TEXT,
 planned_entry REAL NOT NULL, actual_fill REAL, stop_price REAL NOT NULL, target_price REAL NOT NULL, exit_fill REAL,
 planned_rr REAL, actual_entry_rr REAL, realized_r REAL,
 requested_risk REAL, allowed_risk REAL, per_contract_risk REAL, quantity INTEGER,
 actual_risk REAL, unused_risk REAL, gross_pnl REAL, commission REAL, fees REAL,
 slippage_cost REAL, adverse_slippage_cost REAL, price_improvement REAL, gap_slippage REAL, net_pnl REAL,
 mfe_points REAL, mfe_dollars REAL, mfe_r REAL, mae_points REAL, mae_dollars REAL, mae_r REAL,
 excursion_quality TEXT, entry_reason TEXT, exit_reason TEXT, fill_model TEXT NOT NULL,
 replay_mode TEXT NOT NULL, ambiguity_policy TEXT NOT NULL, ambiguity_flags_json TEXT NOT NULL,
 status TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS research_trades_no_update BEFORE UPDATE ON research_trades
BEGIN SELECT RAISE(ABORT,'research trades are immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_trades_no_delete BEFORE DELETE ON research_trades
BEGIN SELECT RAISE(ABORT,'research trades are immutable'); END;

CREATE TABLE IF NOT EXISTS equity_curve (
 equity_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES backtest_runs(run_id),
 sequence_no INTEGER NOT NULL, timestamp TEXT NOT NULL, event_type TEXT NOT NULL,
 balance REAL NOT NULL, realized_pnl REAL NOT NULL, unrealized_pnl REAL NOT NULL,
 equity REAL NOT NULL, peak_balance REAL NOT NULL, peak_equity REAL NOT NULL,
 realized_drawdown REAL NOT NULL, equity_drawdown REAL NOT NULL, intrabar_approximate_drawdown REAL NOT NULL,
 drawdown_dollars REAL NOT NULL, drawdown_percent REAL NOT NULL,
 daily_pnl REAL NOT NULL, session_pnl REAL NOT NULL, daily_equity_low REAL NOT NULL,
 session_equity_low REAL NOT NULL, open_risk REAL NOT NULL,
 UNIQUE(run_id,sequence_no)
);
CREATE TRIGGER IF NOT EXISTS equity_curve_no_update BEFORE UPDATE ON equity_curve
BEGIN SELECT RAISE(ABORT,'equity curve is immutable'); END;
CREATE TRIGGER IF NOT EXISTS equity_curve_no_delete BEFORE DELETE ON equity_curve
BEGIN SELECT RAISE(ABORT,'equity curve is immutable'); END;

CREATE TABLE IF NOT EXISTS account_blocks (
 block_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES backtest_runs(run_id),
 timestamp TEXT NOT NULL, setup_id TEXT, status TEXT NOT NULL, reason TEXT NOT NULL,
 snapshot_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS account_blocks_no_update BEFORE UPDATE ON account_blocks
BEGIN SELECT RAISE(ABORT,'account blocks are immutable'); END;
CREATE TRIGGER IF NOT EXISTS account_blocks_no_delete BEFORE DELETE ON account_blocks
BEGIN SELECT RAISE(ABORT,'account blocks are immutable'); END;

CREATE TABLE IF NOT EXISTS risk_audits (
 audit_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES backtest_runs(run_id),
 setup_id TEXT NOT NULL, base_risk REAL, evaluation_available_risk REAL, session_multiplier REAL,
 grade_multiplier REAL, strategy_multiplier REAL, entry_location_multiplier REAL, lifetime_multiplier REAL,
 recovery_multiplier REAL, pacing_multiplier REAL, final_allowed_risk REAL NOT NULL,
 per_contract_risk REAL NOT NULL, quantity INTEGER NOT NULL, actual_risk REAL NOT NULL,
 unused_risk REAL NOT NULL, source TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS risk_audits_no_update BEFORE UPDATE ON risk_audits
BEGIN SELECT RAISE(ABORT,'risk audits are immutable'); END;
CREATE TRIGGER IF NOT EXISTS risk_audits_no_delete BEFORE DELETE ON risk_audits
BEGIN SELECT RAISE(ABORT,'risk audits are immutable'); END;
"""
