from __future__ import annotations


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
        result.append({"root": root, "contract": contract, "start": start, "end": end, "events": events, "bars": bar_counts, "gaps": gaps, "coverage_percentage": (100.0 * complete / total) if total else 0.0})
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
