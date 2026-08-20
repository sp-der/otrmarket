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
"""
