from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .catalog import CONTRACT_SPECS, parse_contract
from .schema import SCHEMA_SQL


def utc_iso(value: datetime | str) -> str:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RawEvent:
    contract: str
    exchange_timestamp: datetime | str
    last_price: float
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    source: str = "ninjatrader"
    ingested_at: datetime | str | None = None
    data_gap: bool = False
    integrity_status: str = "VALID"
    source_event_id: str | None = None
    sequence_no: int | None = None


class HistoricalStore:
    """Append-only research store, intentionally separate from production SQLite."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.executemany(
                """INSERT OR IGNORE INTO instrument_roots(
                    instrument,root_symbol,size_class,tick_size,point_value,tick_value
                ) VALUES(?,?,?,?,?,?)""",
                [(item.instrument, item.root, item.size_class, item.tick_size,
                  item.point_value, item.tick_value) for item in CONTRACT_SPECS.values()],
            )

    def create_capture(self, capture_id: str, source: str, mode: str, started_at, notes: str = "") -> None:
        now = utc_iso(datetime.now(timezone.utc))
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO capture_sessions(capture_id,source,mode,started_at,created_at,notes) VALUES(?,?,?,?,?,?)",
                (capture_id, source, mode.upper(), utc_iso(started_at), now, notes),
            )

    def ensure_contract(self, contract: str, *, active_from=None, active_to=None, metadata_source="OTR_STATIC_V1") -> None:
        exact = " ".join(contract.strip().upper().split())
        spec, expiry = parse_contract(exact)
        with self.connect() as connection:
            self._ensure_contract(connection, exact, spec, expiry, active_from, active_to, metadata_source)

    @staticmethod
    def _ensure_contract(connection, exact, spec, expiry, active_from, active_to, metadata_source):
        connection.execute(
            """INSERT OR IGNORE INTO contracts(
                contract,instrument,root_symbol,size_class,expiry,expiry_status,
                tick_size,point_value,tick_value,active_from,active_to,metadata_source
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (exact, spec.instrument, spec.root, spec.size_class,
             expiry.isoformat() if expiry else None, "ESTIMATED" if expiry else "UNKNOWN",
             spec.tick_size, spec.point_value, spec.tick_value,
             utc_iso(active_from) if active_from else None, utc_iso(active_to) if active_to else None,
             metadata_source),
        )

    def append_events(self, capture_id: str, events: list[RawEvent]) -> tuple[int, int]:
        """Append events in caller order; assigned sequence numbers never change."""
        inserted = duplicates = 0
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) FROM historical_events WHERE capture_id=?",
                (capture_id,),
            ).fetchone()
            next_sequence = int(row[0]) + 1
            for item in events:
                exact = " ".join(item.contract.strip().upper().split())
                spec, expiry = parse_contract(exact)
                self._ensure_contract(connection, exact, spec, expiry, None, None, "OTR_STATIC_V1")
                sequence = item.sequence_no if item.sequence_no is not None else next_sequence
                ingested = item.ingested_at or datetime.now(timezone.utc)
                before = connection.total_changes
                connection.execute(
                    """INSERT OR IGNORE INTO historical_events(
                       capture_id,sequence_no,root_symbol,contract,size_class,exchange_timestamp,
                       last_price,bid,ask,volume,source,ingested_at,data_gap,integrity_status,source_event_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (capture_id, sequence, spec.root, exact, spec.size_class,
                     utc_iso(item.exchange_timestamp), float(item.last_price), item.bid, item.ask,
                     item.volume, item.source, utc_iso(ingested), int(item.data_gap),
                     item.integrity_status, item.source_event_id),
                )
                if connection.total_changes > before:
                    inserted += 1
                    next_sequence = max(next_sequence, sequence + 1)
                else:
                    duplicates += 1
        return inserted, duplicates
