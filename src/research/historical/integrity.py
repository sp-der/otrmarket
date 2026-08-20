from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json


def _dt(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def analyze_integrity(connection, capture_id: str) -> list[dict]:
    findings = []
    rows = connection.execute(
        "SELECT * FROM historical_events WHERE capture_id=? ORDER BY sequence_no", (capture_id,)
    ).fetchall()
    by_contract = {}
    prior_sequence = None
    prior_time = None
    fingerprints = set()
    for row in rows:
        sequence = int(row["sequence_no"])
        timestamp = _dt(row["exchange_timestamp"])
        if prior_sequence is not None and sequence != prior_sequence + 1:
            findings.append(_finding(row, "SEQUENCE_GAP", "ERROR", f"Expected {prior_sequence + 1}, observed {sequence}"))
        if prior_time is not None and timestamp < prior_time:
            findings.append(_finding(row, "OUT_OF_ORDER_TIMESTAMP", "ERROR", "Exchange timestamp moved backwards in capture sequence"))
        fingerprint = (row["contract"], row["exchange_timestamp"], row["last_price"], row["bid"], row["ask"], row["volume"])
        if fingerprint in fingerprints:
            findings.append(_finding(row, "DUPLICATE_EVENT", "WARNING", "Repeated contract/timestamp/market-data tuple"))
        fingerprints.add(fingerprint)
        by_contract.setdefault(row["contract"], []).append(row)
        prior_sequence, prior_time = sequence, timestamp

    for contract, events in by_contract.items():
        minutes = sorted({_dt(event["exchange_timestamp"]).replace(second=0, microsecond=0) for event in events})
        for earlier, later in zip(minutes, minutes[1:]):
            if later - earlier > timedelta(minutes=1):
                findings.append(_finding(events[0], "MISSING_PERIOD", "WARNING", f"No events for {int((later-earlier).total_seconds()/60)-1} minute(s)", earlier + timedelta(minutes=1), later))

    nq = {row["open_time"] for row in connection.execute("SELECT open_time FROM canonical_candles WHERE capture_id=? AND root_symbol='NQ' AND timeframe='1m'", (capture_id,))}
    es = {row["open_time"] for row in connection.execute("SELECT open_time FROM canonical_candles WHERE capture_id=? AND root_symbol='ES' AND timeframe='1m'", (capture_id,))}
    for timestamp in sorted(nq ^ es):
        root = "NQ" if timestamp in nq else "ES"
        findings.append({"capture_id": capture_id, "root_symbol": root, "contract": None, "timeframe": "1m", "start_time": timestamp, "end_time": timestamp, "finding_type": "MISSING_NQ_ES_PAIR", "severity": "WARNING", "details": f"{root} exists without paired {'ES' if root == 'NQ' else 'NQ'} 1m candle"})

    for root in ("NQ", "ES", "GC"):
        contracts = connection.execute("SELECT contract,MIN(exchange_timestamp),MAX(exchange_timestamp) FROM historical_events WHERE capture_id=? AND root_symbol=? GROUP BY contract ORDER BY MIN(exchange_timestamp)", (capture_id, root)).fetchall()
        for prior, current in zip(contracts, contracts[1:]):
            findings.append({"capture_id": capture_id, "root_symbol": root, "contract": current[0], "timeframe": None, "start_time": prior[2], "end_time": current[1], "finding_type": "CONTRACT_ROLL_BOUNDARY", "severity": "INFO", "details": f"Observed transition {prior[0]} -> {current[0]}"})

    incomplete = connection.execute("SELECT * FROM canonical_candles WHERE capture_id=? AND completeness_state!='COMPLETE'", (capture_id,)).fetchall()
    for bar in incomplete:
        findings.append({"capture_id": capture_id, "root_symbol": bar["root_symbol"], "contract": bar["contract"], "timeframe": bar["timeframe"], "start_time": bar["open_time"], "end_time": bar["close_time"], "finding_type": "INCOMPLETE_CANDLE", "severity": "WARNING", "details": f"{bar['gap_state']}; sources={bar['source_coverage']}"})

    now = datetime.now(timezone.utc).isoformat()
    for item in findings:
        connection.execute("""INSERT OR IGNORE INTO integrity_findings(capture_id,root_symbol,contract,timeframe,start_time,end_time,finding_type,severity,details,detected_at) VALUES(:capture_id,:root_symbol,:contract,:timeframe,:start_time,:end_time,:finding_type,:severity,:details,:detected_at)""", {**item, "detected_at": now})
    return findings


def _finding(row, kind, severity, details, start=None, end=None):
    return {"capture_id": row["capture_id"], "root_symbol": row["root_symbol"], "contract": row["contract"], "timeframe": None, "start_time": (start or _dt(row["exchange_timestamp"])).isoformat(), "end_time": (end or _dt(row["exchange_timestamp"])).isoformat(), "finding_type": kind, "severity": severity, "details": details}
