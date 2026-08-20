from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .candles import build_canonical_candles
from .integrity import analyze_integrity
from .store import HistoricalStore, RawEvent


def import_retained_market_quotes(production_db: str | Path, historical_db: str | Path, capture_id: str) -> dict:
    """Copy retained futures quotes without changing or initializing production DB."""
    uri = f"file:{Path(production_db).resolve()}?mode=ro"
    source = sqlite3.connect(uri, uri=True)
    source.row_factory = sqlite3.Row
    store = HistoricalStore(historical_db)
    store.initialize()
    rows = source.execute("""SELECT id,received_at,exchange_time,source,symbol,price,bid,ask,ingested_at
                             FROM market_quotes WHERE symbol IN ('NQ','ES','GC') AND price IS NOT NULL
                             ORDER BY COALESCE(exchange_time,received_at),id""").fetchall()
    if not rows:
        source.close()
        raise ValueError("No retained NQ/ES/GC rows found")
    started, ended = rows[0]["exchange_time"] or rows[0]["received_at"], rows[-1]["exchange_time"] or rows[-1]["received_at"]
    store.create_capture(capture_id, "otrmarket.market_quotes", "IMPORT", started,
                         "Retained rolling production rows; volume unavailable and prior pruning/completeness unknown.")
    events = []
    for row in rows:
        raw_source = str(row["source"] or "")
        contract = raw_source.split(":", 1)[1].strip().upper() if raw_source.startswith("ninjatrader:") else ""
        status = "IMPORTED_VOLUME_MISSING"
        gap = False
        if not contract:
            # Exact identity cannot be invented; retained rows without it are not importable.
            continue
        events.append(RawEvent(
            contract=contract, exchange_timestamp=row["exchange_time"] or row["received_at"],
            last_price=row["price"], bid=row["bid"], ask=row["ask"], volume=None,
            source=raw_source, ingested_at=row["ingested_at"] or row["received_at"],
            data_gap=gap, integrity_status=status, source_event_id=f"market_quotes:{row['id']}",
        ))
    inserted, duplicates = store.append_events(capture_id, events)
    with store.connect() as connection:
        connection.execute("UPDATE capture_sessions SET ended_at=? WHERE capture_id=?", (ended, capture_id))
        bars = build_canonical_candles(connection, capture_id)
        findings = analyze_integrity(connection, capture_id)
    source.close()
    return {"capture_id": capture_id, "inserted": inserted, "duplicates": duplicates, "candles": bars, "findings": len(findings)}
