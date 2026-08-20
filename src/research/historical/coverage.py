from __future__ import annotations

import json
from datetime import datetime

from .acquisition import expected_minutes


TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h")


def coverage_rows(connection, capture_id: str | None = None) -> list[dict]:
    where, args = ("WHERE capture_id=?", (capture_id,)) if capture_id else ("", ())
    contracts = connection.execute(f"SELECT root_symbol,contract,MIN(exchange_timestamp),MAX(exchange_timestamp),COUNT(*) FROM historical_events {where} GROUP BY root_symbol,contract ORDER BY root_symbol,contract", args).fetchall()
    result = []
    for root, contract, start, end, events in contracts:
        bar_counts = {}
        complete = total = 0
        for timeframe in TIMEFRAMES:
            clause = "AND capture_id=?" if capture_id else ""
            params = (root, contract, timeframe, capture_id) if capture_id else (root, contract, timeframe)
            row = connection.execute(f"SELECT COUNT(*),SUM(completeness_state='COMPLETE') FROM canonical_candles WHERE root_symbol=? AND contract=? AND timeframe=? {clause}", params).fetchone()
            bar_counts[timeframe] = int(row[0] or 0)
            if timeframe == "1m":
                total, complete = int(row[0] or 0), int(row[1] or 0)
        gap_clause = "AND capture_id=?" if capture_id else ""
        gap_params = (root, contract, capture_id) if capture_id else (root, contract)
        gaps = connection.execute(f"SELECT COUNT(*) FROM integrity_findings WHERE root_symbol=? AND (contract=? OR contract IS NULL) AND finding_type IN ('MISSING_PERIOD','SEQUENCE_GAP','MISSING_NQ_ES_PAIR') {gap_clause}", gap_params).fetchone()[0]
        result.append({"root": root, "contract": contract, "start": start, "end": end, "events": events, "bars": bar_counts, "gaps": gaps, "coverage_percentage": (100.0 * complete / total) if total else 0.0,
                       "source": "event/replay capture", "missing_bars": gaps, "duplicates": 0,
                       "roll_boundary": None, "validation_status": "SMOKE_ONLY" if capture_id == "retained-operation70-phase1" else "INCOMPLETE"})
    has_raw = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_import_bars'").fetchone()
    if has_raw:
        raw_where, raw_args = ("WHERE r.capture_id=?", (capture_id,)) if capture_id else ("", ())
        imported = connection.execute(f"""SELECT r.capture_id,r.root_symbol,r.contract,MIN(r.normalized_timestamp),
          MAX(r.normalized_timestamp),COUNT(*),m.source,m.coverage_percentage,m.integrity_summary_json,
          m.roll_boundaries_json,m.validation_status
          FROM raw_import_bars r JOIN capture_manifests m ON m.capture_id=r.capture_id {raw_where}
          GROUP BY r.capture_id,r.root_symbol,r.contract ORDER BY r.root_symbol,r.contract""", raw_args).fetchall()
        for capture, root, contract, start, end, raw_count, source, percentage, integrity_json, rolls_json, status in imported:
            counts = {}
            for timeframe in TIMEFRAMES:
                counts[timeframe] = int(connection.execute("SELECT COUNT(*) FROM canonical_candles WHERE capture_id=? AND contract=? AND timeframe=?", (capture, contract, timeframe)).fetchone()[0])
            integrity = json.loads(integrity_json)
            rolls = json.loads(rolls_json)
            timestamps = {datetime.fromisoformat(row[0]) for row in connection.execute(
                "SELECT normalized_timestamp FROM raw_import_bars WHERE capture_id=? AND contract=?",
                (capture, contract))}
            expected = set(expected_minutes(datetime.fromisoformat(start), datetime.fromisoformat(end)))
            missing = len(expected - timestamps)
            contract_coverage = 100.0 * len(expected & timestamps) / len(expected) if expected else 0.0
            duplicates = int(connection.execute("""SELECT COALESCE(SUM(n-1),0) FROM (
              SELECT COUNT(*) n FROM raw_import_bars WHERE capture_id=? AND contract=?
              GROUP BY normalized_timestamp HAVING COUNT(*)>1)""", (capture, contract)).fetchone()[0])
            result.append({"capture_id": capture, "root": root, "contract": contract, "start": start,
                           "end": end, "events": raw_count, "bars": counts,
                           "gaps": missing, "coverage_percentage": contract_coverage, "source": source,
                           "missing_bars": missing, "duplicates": duplicates,
                           "roll_boundary": rolls or None, "validation_status": status})
    return result


def format_coverage(rows: list[dict]) -> str:
    blocks = []
    for row in rows:
        blocks.append("\n".join([
            row["root"], f"contract: {row['contract']}", f"start: {row['start']}", f"end: {row['end']}",
            f"events: {row['events']}", *[f"{tf} bars: {row['bars'][tf]}" for tf in TIMEFRAMES],
            f"gaps: {row['gaps']}", f"coverage percentage: {row['coverage_percentage']:.2f}%",
        ]))
    return "\n\n".join(blocks)
