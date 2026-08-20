SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS capture_sessions (
    capture_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('IMPORT', 'REPLAY', 'LIVE')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    immutable INTEGER NOT NULL DEFAULT 1 CHECK(immutable = 1)
);

CREATE TABLE IF NOT EXISTS instrument_roots (
    instrument TEXT PRIMARY KEY,
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ', 'ES', 'GC')),
    size_class TEXT NOT NULL CHECK(size_class IN ('MINI', 'MICRO')),
    tick_size REAL NOT NULL,
    point_value REAL NOT NULL,
    tick_value REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS contracts (
    contract TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ', 'ES', 'GC')),
    size_class TEXT NOT NULL CHECK(size_class IN ('MINI', 'MICRO')),
    expiry TEXT,
    expiry_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    tick_size REAL NOT NULL,
    point_value REAL NOT NULL,
    tick_value REAL NOT NULL,
    active_from TEXT,
    active_to TEXT,
    rollover_from TEXT,
    rollover_to TEXT,
    rollover_notes TEXT NOT NULL DEFAULT '',
    metadata_source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historical_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    sequence_no INTEGER NOT NULL,
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ', 'ES', 'GC')),
    contract TEXT NOT NULL REFERENCES contracts(contract),
    size_class TEXT NOT NULL CHECK(size_class IN ('MINI', 'MICRO')),
    exchange_timestamp TEXT NOT NULL,
    last_price REAL NOT NULL,
    bid REAL,
    ask REAL,
    volume INTEGER,
    source TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    data_gap INTEGER NOT NULL DEFAULT 0 CHECK(data_gap IN (0, 1)),
    integrity_status TEXT NOT NULL,
    source_event_id TEXT,
    UNIQUE(capture_id, sequence_no),
    UNIQUE(capture_id, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_historical_events_contract_time
ON historical_events(contract, exchange_timestamp, sequence_no);
CREATE INDEX IF NOT EXISTS idx_historical_events_root_time
ON historical_events(root_symbol, exchange_timestamp, sequence_no);

CREATE TRIGGER IF NOT EXISTS historical_events_no_update
BEFORE UPDATE ON historical_events BEGIN SELECT RAISE(ABORT, 'historical events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS historical_events_no_delete
BEFORE DELETE ON historical_events BEGIN SELECT RAISE(ABORT, 'historical events are immutable'); END;

CREATE TABLE IF NOT EXISTS canonical_candles (
    candle_id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    contract TEXT NOT NULL REFERENCES contracts(contract),
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ', 'ES', 'GC')),
    timeframe TEXT NOT NULL CHECK(timeframe IN ('1m', '5m', '15m', '30m', '1h')),
    open_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER,
    event_count INTEGER NOT NULL,
    completeness_state TEXT NOT NULL,
    source_coverage TEXT NOT NULL,
    gap_state TEXT NOT NULL,
    UNIQUE(capture_id, contract, timeframe, open_time)
);

CREATE INDEX IF NOT EXISTS idx_canonical_candles_root_tf_time
ON canonical_candles(root_symbol, timeframe, open_time);

CREATE TRIGGER IF NOT EXISTS canonical_candles_no_update
BEFORE UPDATE ON canonical_candles BEGIN SELECT RAISE(ABORT, 'canonical candles are immutable'); END;
CREATE TRIGGER IF NOT EXISTS canonical_candles_no_delete
BEFORE DELETE ON canonical_candles BEGIN SELECT RAISE(ABORT, 'canonical candles are immutable'); END;

CREATE TABLE IF NOT EXISTS integrity_findings (
    finding_id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    root_symbol TEXT,
    contract TEXT,
    timeframe TEXT,
    start_time TEXT,
    end_time TEXT,
    finding_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    details TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    UNIQUE(capture_id, finding_type, contract, timeframe, start_time, end_time, details)
);

CREATE TABLE IF NOT EXISTS raw_import_bars (
    raw_bar_id INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    source_file TEXT NOT NULL,
    source_row_number INTEGER NOT NULL,
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ', 'ES', 'GC')),
    contract TEXT NOT NULL REFERENCES contracts(contract),
    size_class TEXT NOT NULL CHECK(size_class IN ('MINI', 'MICRO')),
    source_timezone TEXT NOT NULL,
    original_timestamp TEXT NOT NULL,
    normalized_timestamp TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL CHECK(interval_minutes = 1),
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    row_digest TEXT NOT NULL,
    integrity_status TEXT NOT NULL,
    findings_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(capture_id, source_file, source_row_number)
);

CREATE INDEX IF NOT EXISTS idx_raw_import_bars_contract_time
ON raw_import_bars(capture_id, contract, normalized_timestamp);

CREATE TRIGGER IF NOT EXISTS raw_import_bars_no_update
BEFORE UPDATE ON raw_import_bars BEGIN SELECT RAISE(ABORT, 'raw import bars are immutable'); END;
CREATE TRIGGER IF NOT EXISTS raw_import_bars_no_delete
BEFORE DELETE ON raw_import_bars BEGIN SELECT RAISE(ABORT, 'raw import bars are immutable'); END;

CREATE TABLE IF NOT EXISTS capture_manifests (
    capture_id TEXT PRIMARY KEY REFERENCES capture_sessions(capture_id),
    source TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    markets_json TEXT NOT NULL,
    contracts_json TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    source_timezone TEXT NOT NULL,
    resolution TEXT NOT NULL,
    raw_row_count INTEGER NOT NULL,
    canonical_counts_json TEXT NOT NULL,
    coverage_percentage REAL NOT NULL,
    integrity_summary_json TEXT NOT NULL,
    roll_boundaries_json TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    construction_version TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK(validation_status IN ('SMOKE_ONLY','INCOMPLETE','USABLE_WITH_WARNINGS','VALIDATED')),
    holiday_calendar_status TEXT NOT NULL,
    manifest_digest TEXT NOT NULL UNIQUE
);

CREATE TRIGGER IF NOT EXISTS capture_manifests_no_update
BEFORE UPDATE ON capture_manifests BEGIN SELECT RAISE(ABORT, 'capture manifests are immutable'); END;
CREATE TRIGGER IF NOT EXISTS capture_manifests_no_delete
BEFORE DELETE ON capture_manifests BEGIN SELECT RAISE(ABORT, 'capture manifests are immutable'); END;

CREATE TABLE IF NOT EXISTS provider_capture_metadata (
    capture_id TEXT PRIMARY KEY REFERENCES capture_sessions(capture_id),
    provider TEXT NOT NULL,
    dataset TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    job_id TEXT NOT NULL,
    source_package_sha256 TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    requested_start TEXT NOT NULL,
    requested_end TEXT NOT NULL,
    condition_json TEXT NOT NULL,
    degraded_dates_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS provider_capture_metadata_no_update
BEFORE UPDATE ON provider_capture_metadata BEGIN SELECT RAISE(ABORT, 'provider metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS provider_capture_metadata_no_delete
BEFORE DELETE ON provider_capture_metadata BEGIN SELECT RAISE(ABORT, 'provider metadata is immutable'); END;

CREATE TABLE IF NOT EXISTS databento_instruments (
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    instrument_id INTEGER NOT NULL,
    raw_symbol TEXT NOT NULL,
    contract TEXT NOT NULL REFERENCES contracts(contract),
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ', 'ES', 'GC')),
    mapping_start TEXT NOT NULL,
    mapping_end TEXT NOT NULL,
    PRIMARY KEY(capture_id, instrument_id)
);

CREATE TRIGGER IF NOT EXISTS databento_instruments_no_update
BEFORE UPDATE ON databento_instruments BEGIN SELECT RAISE(ABORT, 'Databento instruments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS databento_instruments_no_delete
BEFORE DELETE ON databento_instruments BEGIN SELECT RAISE(ABORT, 'Databento instruments are immutable'); END;

CREATE TABLE IF NOT EXISTS databento_bar_provenance (
    raw_bar_id INTEGER PRIMARY KEY REFERENCES raw_import_bars(raw_bar_id),
    capture_id TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    raw_symbol TEXT NOT NULL,
    publisher_id INTEGER,
    FOREIGN KEY(capture_id, instrument_id) REFERENCES databento_instruments(capture_id, instrument_id)
);

CREATE TRIGGER IF NOT EXISTS databento_bar_provenance_no_update
BEFORE UPDATE ON databento_bar_provenance BEGIN SELECT RAISE(ABORT, 'Databento provenance is immutable'); END;
CREATE TRIGGER IF NOT EXISTS databento_bar_provenance_no_delete
BEFORE DELETE ON databento_bar_provenance BEGIN SELECT RAISE(ABORT, 'Databento provenance is immutable'); END;

CREATE TABLE IF NOT EXISTS research_series_bars (
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ', 'ES', 'GC')),
    open_time TEXT NOT NULL,
    candle_id INTEGER NOT NULL REFERENCES canonical_candles(candle_id),
    instrument_id INTEGER NOT NULL,
    contract TEXT NOT NULL,
    selection_method TEXT NOT NULL,
    PRIMARY KEY(capture_id, root_symbol, open_time)
);

CREATE INDEX IF NOT EXISTS idx_research_series_capture_root_time
ON research_series_bars(capture_id, root_symbol, open_time);

CREATE TRIGGER IF NOT EXISTS research_series_bars_no_update
BEFORE UPDATE ON research_series_bars BEGIN SELECT RAISE(ABORT, 'research series is immutable'); END;
CREATE TRIGGER IF NOT EXISTS research_series_bars_no_delete
BEFORE DELETE ON research_series_bars BEGIN SELECT RAISE(ABORT, 'research series is immutable'); END;

CREATE TABLE IF NOT EXISTS causal_roll_decisions (
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ','ES','GC')),
    effective_date TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    selected_contract TEXT NOT NULL,
    instrument_id INTEGER NOT NULL,
    evidence_end_time TEXT,
    evidence_json TEXT NOT NULL,
    selector_version TEXT NOT NULL,
    PRIMARY KEY(capture_id, root_symbol, effective_date)
);

CREATE TABLE IF NOT EXISTS causal_research_series_bars (
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    root_symbol TEXT NOT NULL CHECK(root_symbol IN ('NQ','ES','GC')),
    open_time TEXT NOT NULL,
    candle_id INTEGER NOT NULL REFERENCES canonical_candles(candle_id),
    instrument_id INTEGER NOT NULL,
    contract TEXT NOT NULL,
    roll_decision_date TEXT NOT NULL,
    selector_version TEXT NOT NULL,
    PRIMARY KEY(capture_id, root_symbol, open_time)
);

CREATE INDEX IF NOT EXISTS idx_causal_series_capture_root_time
ON causal_research_series_bars(capture_id, root_symbol, open_time);

CREATE TRIGGER IF NOT EXISTS causal_roll_decisions_no_update BEFORE UPDATE ON causal_roll_decisions
BEGIN SELECT RAISE(ABORT, 'causal roll decisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS causal_roll_decisions_no_delete BEFORE DELETE ON causal_roll_decisions
BEGIN SELECT RAISE(ABORT, 'causal roll decisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS causal_series_no_update BEFORE UPDATE ON causal_research_series_bars
BEGIN SELECT RAISE(ABORT, 'causal research series is immutable'); END;
CREATE TRIGGER IF NOT EXISTS causal_series_no_delete BEFORE DELETE ON causal_research_series_bars
BEGIN SELECT RAISE(ABORT, 'causal research series is immutable'); END;

CREATE TABLE IF NOT EXISTS canonical_bar_provenance (
    candle_id INTEGER PRIMARY KEY REFERENCES canonical_candles(candle_id),
    capture_id TEXT NOT NULL,
    source TEXT NOT NULL,
    contract TEXT NOT NULL,
    raw_first_id INTEGER,
    raw_last_id INTEGER,
    construction_version TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS canonical_bar_provenance_no_update
BEFORE UPDATE ON canonical_bar_provenance BEGIN SELECT RAISE(ABORT, 'canonical provenance is immutable'); END;
CREATE TRIGGER IF NOT EXISTS canonical_bar_provenance_no_delete
BEFORE DELETE ON canonical_bar_provenance BEGIN SELECT RAISE(ABORT, 'canonical provenance is immutable'); END;

CREATE TABLE IF NOT EXISTS declared_roll_boundaries (
    capture_id TEXT NOT NULL REFERENCES capture_sessions(capture_id),
    root_symbol TEXT NOT NULL,
    from_contract TEXT NOT NULL,
    to_contract TEXT NOT NULL,
    roll_timestamp TEXT NOT NULL,
    method TEXT NOT NULL,
    PRIMARY KEY(capture_id, root_symbol, from_contract, to_contract, roll_timestamp)
);
"""
