from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Iterable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .catalog import parse_contract
from .store import HistoricalStore, utc_iso


CONSTRUCTION_VERSION = "OTR_HISTORICAL_1M_V2"
VALIDATION_STATES = ("SMOKE_ONLY", "INCOMPLETE", "USABLE_WITH_WARNINGS", "VALIDATED")
TIMESTAMP_FORMATS = {
    "ninjatrader": ("%Y%m%d %H%M%S", "%Y%m%d %H%M"),
    "us": ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"),
    "iso": ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"),
}
ALIASES = {
    "timestamp": {"timestamp", "time", "datetime", "date time", "date"},
    "open": {"open", "o"}, "high": {"high", "h"}, "low": {"low", "l"},
    "close": {"close", "c", "last"}, "volume": {"volume", "vol", "v"},
}


class ImportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ImportMetadata:
    symbol: str
    contract: str
    source_timezone: str
    interval_minutes: int
    source: str
    capture_date: str
    timestamp_format: str = "auto"
    delimiter: str = "auto"
    capture_id: str | None = None

    def normalized(self) -> "ImportMetadata":
        symbol = self.symbol.strip().upper()
        contract = " ".join(self.contract.strip().upper().split())
        spec, _ = parse_contract(contract)
        if symbol != spec.instrument:
            raise ImportValidationError(f"Symbol {symbol} does not match exact contract {contract}")
        if self.interval_minutes != 1:
            raise ImportValidationError("Phase 5.5 accepts only explicit 1-minute OHLCV exports")
        try:
            ZoneInfo(self.source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ImportValidationError(f"Unknown source timezone: {self.source_timezone}") from exc
        if not self.source.strip() or not self.capture_date.strip():
            raise ImportValidationError("Source and capture date are required")
        return ImportMetadata(symbol, contract, self.source_timezone, 1, self.source.strip(),
                              self.capture_date.strip(), self.timestamp_format, self.delimiter,
                              self.capture_id)


@dataclass(frozen=True)
class ParsedBar:
    row_number: int
    original_timestamp: str
    normalized_timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    findings: tuple[str, ...] = field(default_factory=tuple)


def _delimiter(sample: str, requested: str) -> str:
    if requested != "auto":
        values = {"comma": ",", "tab": "\t", "semicolon": ";", ",": ",", "\t": "\t", ";": ";"}
        if requested not in values:
            raise ImportValidationError(f"Unsupported delimiter: {requested}")
        return values[requested]
    candidates = {char: sample.count(char) for char in (",", "\t", ";")}
    best = max(candidates, key=candidates.get)
    if candidates[best] == 0 or list(candidates.values()).count(candidates[best]) > 1:
        raise ImportValidationError("Ambiguous delimiter; specify comma, tab, or semicolon")
    return best


def _column_map(header: list[str]) -> dict[str, int]:
    normalized = [value.strip().lower().replace("_", " ") for value in header]
    result = {}
    for name, aliases in ALIASES.items():
        matches = [index for index, value in enumerate(normalized) if value in aliases]
        if len(matches) != 1:
            raise ImportValidationError(f"Required column {name!r} was missing or ambiguous")
        result[name] = matches[0]
    return result


def _parse_local_timestamp(value: str, zone: ZoneInfo, parser: str) -> datetime:
    formats = tuple(fmt for values in TIMESTAMP_FORMATS.values() for fmt in values) if parser == "auto" else TIMESTAMP_FORMATS.get(parser)
    if not formats:
        raise ImportValidationError(f"Unsupported timestamp parser: {parser}")
    matches = []
    for fmt in formats:
        try:
            matches.append(datetime.strptime(value.strip(), fmt))
        except ValueError:
            pass
    unique = {item for item in matches}
    if len(unique) != 1:
        raise ImportValidationError(f"Timestamp is unparseable or ambiguous: {value!r}; specify --timestamp-format")
    local = unique.pop().replace(tzinfo=zone, fold=0)
    # A local wall time that round-trips differently is in a DST spring-forward hole.
    roundtrip = local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
    if roundtrip != local.replace(tzinfo=None):
        raise ImportValidationError(f"Nonexistent local time at DST transition: {value!r}")
    return local.astimezone(timezone.utc)


def parse_ninjatrader_file(path: str | Path, metadata: ImportMetadata) -> tuple[dict, Iterator[ParsedBar]]:
    metadata = metadata.normalized()
    source_path = Path(path)
    handle = source_path.open("r", encoding="utf-8-sig", newline="")
    sample = handle.read(8192)
    handle.seek(0)
    delimiter = _delimiter(sample, metadata.delimiter)
    reader = csv.reader(handle, delimiter=delimiter)
    try:
        first_row = next(reader)
    except StopIteration as exc:
        handle.close()
        raise ImportValidationError("Historical export is empty") from exc
    headerless = bool(first_row and re.fullmatch(r"\d{8}\s+\d{4,6}", first_row[0].strip()))
    if headerless:
        if len(first_row) != 6:
            handle.close()
            raise ImportValidationError("Headerless NinjaTrader rows must contain exactly timestamp,OHLCV")
        header = ["timestamp", "open", "high", "low", "close", "volume"]
        columns = {name: index for index, name in enumerate(header)}
        pending_first = first_row
    else:
        header = first_row
        pending_first = None
        try:
            columns = _column_map(header)
        except Exception:
            handle.close()
            raise
    zone = ZoneInfo(metadata.source_timezone)

    def rows() -> Iterator[ParsedBar]:
        try:
            source_rows = itertools.chain((pending_first,), reader) if pending_first is not None else reader
            start_number = 1 if pending_first is not None else 2
            for row_number, row in enumerate(source_rows, start=start_number):
                if not row or not any(cell.strip() for cell in row):
                    continue
                try:
                    original = row[columns["timestamp"]].strip()
                    timestamp = _parse_local_timestamp(original, zone, metadata.timestamp_format)
                    prices = [float(row[columns[key]]) for key in ("open", "high", "low", "close")]
                    volume_float = float(row[columns["volume"]])
                except (IndexError, ValueError) as exc:
                    raise ImportValidationError(f"Malformed row {row_number}: {exc}") from exc
                if not volume_float.is_integer():
                    raise ImportValidationError(f"Volume must be an integer at row {row_number}")
                yield ParsedBar(row_number, original, utc_iso(timestamp), *prices, int(volume_float))
        finally:
            handle.close()

    detected = {"delimiter": {",": "comma", "\t": "tab", ";": "semicolon"}[delimiter],
                "columns": {name: header[index] for name, index in columns.items()},
                "source_timezone": metadata.source_timezone,
                "timestamp_parser": metadata.timestamp_format,
                "headerless_ninjatrader": headerless}
    return detected, rows()


def _tick_aligned(value: float, tick: float) -> bool:
    return abs(value / tick - round(value / tick)) < 1e-7


def validate_bar(bar: ParsedBar, tick_size: float) -> list[str]:
    findings = []
    if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high and bar.low <= bar.high):
        findings.append("MALFORMED_OHLC")
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        findings.append("IMPOSSIBLE_PRICE")
    if bar.volume < 0:
        findings.append("NEGATIVE_VOLUME")
    if any(not _tick_aligned(value, tick_size) for value in (bar.open, bar.high, bar.low, bar.close)):
        findings.append("TICK_SIZE_VIOLATION")
    return findings


def is_expected_futures_minute(timestamp: datetime) -> bool:
    """Generic CME/COMEX weekly calendar, holiday-unadjusted.

    Trading is expected Sunday 17:00 CT through Friday 16:00 CT, excluding
    the 16:00-17:00 CT daily maintenance break. Holiday exceptions are not
    invented and manifests explicitly disclose that limitation.
    """
    local = timestamp.astimezone(ZoneInfo("America/Chicago"))
    weekday, minute = local.weekday(), local.hour * 60 + local.minute
    if weekday == 5:
        return False
    if weekday == 6:
        return minute >= 17 * 60
    if weekday == 4:
        return minute < 16 * 60
    return not (16 * 60 <= minute < 17 * 60)


def expected_minutes(start: datetime, end: datetime) -> Iterator[datetime]:
    cursor = start.replace(second=0, microsecond=0)
    end = end.replace(second=0, microsecond=0)
    while cursor <= end:
        if is_expected_futures_minute(cursor):
            yield cursor
        cursor += timedelta(minutes=1)


def _digest(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


def inspect_import(path: str | Path, metadata: ImportMetadata) -> dict:
    metadata = metadata.normalized()
    detected, stream = parse_ninjatrader_file(path, metadata)
    spec, _ = parse_contract(metadata.contract)
    row_count = 0
    prior = None
    duplicates = reversals = zero_volume = 0
    finding_counts: dict[str, int] = {}
    timestamps = set()
    for bar in stream:
        findings = validate_bar(bar, spec.tick_size)
        for finding in findings:
            finding_counts[finding] = finding_counts.get(finding, 0) + 1
        stamp = datetime.fromisoformat(bar.normalized_timestamp)
        duplicates += int(stamp in timestamps)
        reversals += int(prior is not None and stamp < prior)
        zero_volume += int(bar.volume == 0)
        timestamps.add(stamp)
        prior = stamp
        row_count += 1
    if not row_count:
        raise ImportValidationError("No data rows were found")
    start, end = min(timestamps), max(timestamps)
    expected = set(expected_minutes(start, end))
    actual_expected = timestamps & expected
    missing = sorted(expected - timestamps)
    coverage = 100.0 * len(actual_expected) / len(expected) if expected else 0.0
    critical = sum(finding_counts.get(item, 0) for item in ("MALFORMED_OHLC", "IMPOSSIBLE_PRICE", "TICK_SIZE_VIOLATION", "NEGATIVE_VOLUME"))
    return {
        "detected": detected, "symbol": metadata.symbol, "contract": metadata.contract,
        "root": spec.root, "source": metadata.source, "source_timezone": metadata.source_timezone,
        "start": utc_iso(start), "end": utc_iso(end), "row_count": row_count,
        "expected_bars": len(expected), "actual_expected_bars": len(actual_expected),
        "coverage_percentage": coverage, "missing_minutes": [utc_iso(item) for item in missing],
        "duplicates": duplicates, "timestamp_reversals": reversals,
        "zero_volume_count": zero_volume, "zero_volume_frequency": zero_volume / row_count,
        "finding_counts": finding_counts, "critical_findings": critical,
        "holiday_calendar_status": "GENERIC_CME_WEEKLY_HOURS_HOLIDAYS_UNVERIFIED",
    }


def _validation_status(report: dict, source: str) -> str:
    real_source = bool(source.strip()) and "synthetic" not in source.lower() and "proxy" not in source.lower()
    if not real_source or report["critical_findings"] or report["duplicates"] or report["timestamp_reversals"]:
        return "INCOMPLETE"
    if report["coverage_percentage"] >= 99:
        return "USABLE_WITH_WARNINGS"  # holidays/roll schedule still require explicit verification
    if report["coverage_percentage"] >= 95:
        return "USABLE_WITH_WARNINGS"
    return "INCOMPLETE"


def import_ninjatrader(path: str | Path, database: str | Path, metadata: ImportMetadata,
                       *, dry_run: bool = False, batch_size: int = 5000) -> dict:
    metadata = metadata.normalized()
    report = inspect_import(path, metadata)
    source_path = Path(path).resolve()
    file_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    capture_id = metadata.capture_id or f"nt-{metadata.symbol.lower()}-{file_digest[:16]}"
    summary = {**report, "capture_id": capture_id, "dry_run": dry_run,
               "validation_status": _validation_status(report, metadata.source)}
    if dry_run:
        return summary
    store = HistoricalStore(database)
    store.initialize()
    spec, _ = parse_contract(metadata.contract)
    store.create_capture(capture_id, metadata.source, "IMPORT", report["start"],
                         f"Immutable NinjaTrader 1m OHLCV import; sha256={file_digest}")
    store.ensure_contract(metadata.contract, active_from=report["start"],
                          active_to=report["end"], metadata_source=metadata.source)
    try:
        with store.connect() as connection:
            _, stream = parse_ninjatrader_file(path, metadata)
            payload = []
            for bar in stream:
                findings = validate_bar(bar, spec.tick_size)
                row_digest = _digest([metadata.contract, bar.normalized_timestamp, bar.open,
                                      bar.high, bar.low, bar.close, bar.volume])
                payload.append((capture_id, source_path.name, bar.row_number, spec.root,
                                metadata.contract, spec.size_class, metadata.source_timezone,
                                bar.original_timestamp, bar.normalized_timestamp, 1, bar.open,
                                bar.high, bar.low, bar.close, bar.volume, row_digest,
                                "VALID" if not findings else "SUSPICIOUS", json.dumps(findings)))
                if len(payload) >= batch_size:
                    connection.executemany("""INSERT INTO raw_import_bars(
                  capture_id,source_file,source_row_number,root_symbol,contract,size_class,
                  source_timezone,original_timestamp,normalized_timestamp,interval_minutes,
                  open,high,low,close,volume,row_digest,integrity_status,findings_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
                    payload.clear()
            if payload:
                connection.executemany("""INSERT INTO raw_import_bars(
                  capture_id,source_file,source_row_number,root_symbol,contract,size_class,
                  source_timezone,original_timestamp,normalized_timestamp,interval_minutes,
                  open,high,low,close,volume,row_digest,integrity_status,findings_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
            _build_import_candles(connection, capture_id, metadata.source)
            counts = dict(connection.execute("SELECT timeframe,COUNT(*) FROM canonical_candles WHERE capture_id=? GROUP BY timeframe", (capture_id,)))
            integrity = {**report["finding_counts"], "DUPLICATE_TIMESTAMP": report["duplicates"],
                         "TIMESTAMP_REVERSAL": report["timestamp_reversals"],
                         "MISSING_EXPECTED_MINUTE": len(report["missing_minutes"])}
            manifest_payload = {
                "capture_id": capture_id, "source": metadata.source,
                "imported_at": utc_iso(datetime.now(timezone.utc)), "markets": [spec.root],
                "contracts": [metadata.contract], "start_time": report["start"], "end_time": report["end"],
                "source_timezone": metadata.source_timezone, "resolution": "1m",
                "raw_row_count": report["row_count"], "canonical_counts": counts,
                "coverage_percentage": report["coverage_percentage"], "integrity_summary": integrity,
                "roll_boundaries": [], "git_commit": _git_commit(),
                "construction_version": CONSTRUCTION_VERSION,
                "validation_status": summary["validation_status"],
                "holiday_calendar_status": report["holiday_calendar_status"],
            }
            manifest_digest = _digest(manifest_payload)
            connection.execute("""INSERT INTO capture_manifests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (capture_id, metadata.source, manifest_payload["imported_at"], json.dumps([spec.root]),
                 json.dumps([metadata.contract]), report["start"], report["end"], metadata.source_timezone,
                 "1m", report["row_count"], json.dumps(counts, sort_keys=True),
                 report["coverage_percentage"], json.dumps(integrity, sort_keys=True), "[]",
                 manifest_payload["git_commit"], CONSTRUCTION_VERSION, summary["validation_status"],
                 report["holiday_calendar_status"], manifest_digest))
            connection.execute("UPDATE capture_sessions SET ended_at=? WHERE capture_id=?", (report["end"], capture_id))
        summary["canonical_counts"] = counts
        summary["manifest_digest"] = manifest_digest
        return summary
    except Exception:
        # Capture creation is the sole mutable setup record; remove an empty/failed import atomically.
        with store.connect() as connection:
            connection.execute("DELETE FROM capture_sessions WHERE capture_id=? AND NOT EXISTS (SELECT 1 FROM raw_import_bars WHERE capture_id=?)", (capture_id, capture_id))
        raise


def import_ninjatrader_batch(items: Iterable[tuple[str | Path, ImportMetadata]], database: str | Path,
                             capture_id: str, *, dry_run: bool = False,
                             batch_size: int = 5000) -> dict:
    """Import a manifest-declared multi-contract dataset as one immutable capture."""
    prepared = []
    for path, metadata in items:
        normalized = metadata.normalized()
        report = inspect_import(path, normalized)
        prepared.append((Path(path).resolve(), normalized, report))
    if not prepared:
        raise ImportValidationError("Batch manifest contains no files")
    contracts = [metadata.contract for _, metadata, _ in prepared]
    if len(contracts) != len(set(contracts)):
        raise ImportValidationError("Each exact contract may appear only once in a batch capture")
    summary = {"capture_id": capture_id, "files": [{k: v for k, v in report.items() if k != "missing_minutes"}
                                                     for _, _, report in prepared], "dry_run": dry_run}
    if dry_run:
        return summary
    store = HistoricalStore(database)
    store.initialize()
    start = min(report["start"] for _, _, report in prepared)
    end = max(report["end"] for _, _, report in prepared)
    sources = sorted({metadata.source for _, metadata, _ in prepared})
    store.create_capture(capture_id, " + ".join(sources), "IMPORT", start,
                         "Manifest-declared immutable multi-contract NinjaTrader OHLCV import")
    try:
        with store.connect() as connection:
            for path, metadata, report in prepared:
                spec, expiry = parse_contract(metadata.contract)
                store._ensure_contract(connection, metadata.contract, spec, expiry, report["start"],
                                       report["end"], metadata.source)
                _, stream = parse_ninjatrader_file(path, metadata)
                payload = []
                for bar in stream:
                    findings = validate_bar(bar, spec.tick_size)
                    payload.append((capture_id, path.name, bar.row_number, spec.root, metadata.contract,
                                    spec.size_class, metadata.source_timezone, bar.original_timestamp,
                                    bar.normalized_timestamp, 1, bar.open, bar.high, bar.low, bar.close,
                                    bar.volume, _digest([metadata.contract, bar.normalized_timestamp,
                                    bar.open, bar.high, bar.low, bar.close, bar.volume]),
                                    "VALID" if not findings else "SUSPICIOUS", json.dumps(findings)))
                    if len(payload) >= batch_size:
                        connection.executemany("""INSERT INTO raw_import_bars(
                          capture_id,source_file,source_row_number,root_symbol,contract,size_class,
                          source_timezone,original_timestamp,normalized_timestamp,interval_minutes,
                          open,high,low,close,volume,row_digest,integrity_status,findings_json
                          ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
                        payload.clear()
                if payload:
                    connection.executemany("""INSERT INTO raw_import_bars(
                      capture_id,source_file,source_row_number,root_symbol,contract,size_class,
                      source_timezone,original_timestamp,normalized_timestamp,interval_minutes,
                      open,high,low,close,volume,row_digest,integrity_status,findings_json
                      ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", payload)
            _build_import_candles(connection, capture_id, " + ".join(sources))
            counts = dict(connection.execute("SELECT timeframe,COUNT(*) FROM canonical_candles WHERE capture_id=? GROUP BY timeframe", (capture_id,)))
            roots = sorted({parse_contract(metadata.contract)[0].root for _, metadata, _ in prepared})
            roll_boundaries = [boundary for root in roots for boundary in observed_roll_boundaries(connection, root, capture_id)]
            total_expected = sum(report["expected_bars"] for _, _, report in prepared)
            total_actual = sum(report["actual_expected_bars"] for _, _, report in prepared)
            coverage = 100.0 * total_actual / total_expected if total_expected else 0.0
            integrity = {
                "DUPLICATE_TIMESTAMP": sum(report["duplicates"] for _, _, report in prepared),
                "TIMESTAMP_REVERSAL": sum(report["timestamp_reversals"] for _, _, report in prepared),
                "MISSING_EXPECTED_MINUTE": sum(len(report["missing_minutes"]) for _, _, report in prepared),
                "CRITICAL": sum(report["critical_findings"] for _, _, report in prepared),
            }
            status = "INCOMPLETE" if integrity["CRITICAL"] or integrity["DUPLICATE_TIMESTAMP"] or coverage < 95 else "USABLE_WITH_WARNINGS"
            imported_at = utc_iso(datetime.now(timezone.utc))
            payload = {"capture_id":capture_id,"source":" + ".join(sources),"imported_at":imported_at,
                       "markets":roots,"contracts":contracts,"start_time":start,"end_time":end,
                       "source_timezone":"MULTIPLE" if len({m.source_timezone for _,m,_ in prepared}) > 1 else prepared[0][1].source_timezone,
                       "resolution":"1m","raw_row_count":sum(r["row_count"] for _,_,r in prepared),
                       "canonical_counts":counts,"coverage_percentage":coverage,"integrity_summary":integrity,
                       "roll_boundaries":roll_boundaries,"git_commit":_git_commit(),
                       "construction_version":CONSTRUCTION_VERSION,"validation_status":status,
                       "holiday_calendar_status":"GENERIC_CME_WEEKLY_HOURS_HOLIDAYS_UNVERIFIED"}
            digest = _digest(payload)
            connection.execute("INSERT INTO capture_manifests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (capture_id,payload["source"],imported_at,json.dumps(roots),json.dumps(contracts),start,end,
                 payload["source_timezone"],"1m",payload["raw_row_count"],json.dumps(counts,sort_keys=True),
                 coverage,json.dumps(integrity,sort_keys=True),json.dumps(roll_boundaries,sort_keys=True),
                 payload["git_commit"],CONSTRUCTION_VERSION,status,payload["holiday_calendar_status"],digest))
            connection.execute("UPDATE capture_sessions SET ended_at=? WHERE capture_id=?", (end,capture_id))
        return {**summary,"canonical_counts":counts,"coverage_percentage":coverage,
                "validation_status":status,"manifest_digest":digest,"roll_boundaries":roll_boundaries}
    except Exception:
        raise


def _build_import_candles(connection: sqlite3.Connection, capture_id: str, source: str) -> None:
    query = "SELECT * FROM raw_import_bars WHERE capture_id=? ORDER BY contract,normalized_timestamp,raw_bar_id"
    one_key = None
    one_rows = []
    def flush_one(key, grouped):
        if not grouped:
            return
        row = grouped[0]
        opened = key[1]
        clean = len(grouped) == 1 and row["integrity_status"] == "VALID"
        connection.execute("""INSERT INTO canonical_candles(capture_id,contract,root_symbol,timeframe,
          open_time,close_time,open,high,low,close,volume,event_count,completeness_state,source_coverage,gap_state)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (capture_id,row["contract"],row["root_symbol"],"1m",utc_iso(opened),utc_iso(opened+timedelta(minutes=1)),
           row["open"],max(item["high"] for item in grouped),min(item["low"] for item in grouped),grouped[-1]["close"],
           sum(item["volume"] for item in grouped),len(grouped),"COMPLETE" if clean else "INCOMPLETE",
           json.dumps([source]),"NONE" if clean else "DUPLICATE_OR_INVALID"))
        candle_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute("INSERT INTO canonical_bar_provenance VALUES(?,?,?,?,?,?,?)",
                           (candle_id,capture_id,source,row["contract"],grouped[0]["raw_bar_id"],grouped[-1]["raw_bar_id"],CONSTRUCTION_VERSION))
    for row in connection.execute(query, (capture_id,)):
        opened = datetime.fromisoformat(row["normalized_timestamp"])
        key = (row["contract"], opened)
        if one_key is not None and key != one_key:
            flush_one(one_key, one_rows)
            one_rows = []
        one_key = key
        one_rows.append(row)
    if one_key is not None:
        flush_one(one_key, one_rows)
    for timeframe, minutes in (("5m",5),("15m",15),("30m",30),("1h",60)):
        bucket_key = None
        bars = []
        def flush_bucket(contract, opened, bars):
            if not bars:
                return
            times = {datetime.fromisoformat(item["normalized_timestamp"]) for item in bars}
            required = {opened + timedelta(minutes=i) for i in range(minutes)}
            complete = len(bars) == minutes and times == required and all(item["integrity_status"] == "VALID" for item in bars)
            root = bars[0]["root_symbol"]
            connection.execute("""INSERT INTO canonical_candles(capture_id,contract,root_symbol,timeframe,
              open_time,close_time,open,high,low,close,volume,event_count,completeness_state,source_coverage,gap_state)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (capture_id,contract,root,timeframe,utc_iso(opened),utc_iso(opened+timedelta(minutes=minutes)),
               bars[0]["open"],max(item["high"] for item in bars),min(item["low"] for item in bars),bars[-1]["close"],
               sum(item["volume"] for item in bars),len(bars),"COMPLETE" if complete else "INCOMPLETE",
               json.dumps([source]),"NONE" if complete else "GAPPED"))
            candle_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO canonical_bar_provenance VALUES(?,?,?,?,?,?,?)",
                               (candle_id,capture_id,source,contract,bars[0]["raw_bar_id"],bars[-1]["raw_bar_id"],CONSTRUCTION_VERSION))
        for row in connection.execute(query, (capture_id,)):
            stamp = datetime.fromisoformat(row["normalized_timestamp"])
            opened = stamp.replace(minute=(stamp.minute // minutes) * minutes, second=0, microsecond=0)
            key = (row["contract"], opened)
            if bucket_key is not None and key != bucket_key:
                flush_bucket(bucket_key[0], bucket_key[1], bars)
                bars = []
            bucket_key = key
            bars.append(row)
        if bucket_key is not None:
            flush_bucket(bucket_key[0], bucket_key[1], bars)


def list_captures(database: str | Path) -> list[dict]:
    store = HistoricalStore(database)
    with store.connect() as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='capture_manifests'").fetchone():
            return []
        rows = connection.execute("SELECT * FROM capture_manifests ORDER BY imported_at,capture_id").fetchall()
        return [dict(row) for row in rows]


def export_manifest(database: str | Path, capture_id: str) -> dict:
    rows = [item for item in list_captures(database) if item["capture_id"] == capture_id]
    if not rows:
        raise KeyError(capture_id)
    result = rows[0]
    for field in ("markets_json","contracts_json","canonical_counts_json","integrity_summary_json","roll_boundaries_json"):
        result[field.removesuffix("_json")] = json.loads(result.pop(field))
    return result


def verify_capture(database: str | Path, capture_id: str) -> dict:
    manifest = export_manifest(database, capture_id)
    store = HistoricalStore(database)
    with store.connect() as connection:
        raw = connection.execute("SELECT COUNT(*) FROM raw_import_bars WHERE capture_id=?", (capture_id,)).fetchone()[0]
        candles = dict(connection.execute("SELECT timeframe,COUNT(*) FROM canonical_candles WHERE capture_id=? GROUP BY timeframe", (capture_id,)))
    matches = raw == manifest["raw_row_count"] and candles == manifest["canonical_counts"]
    return {"capture_id": capture_id, "integrity_verified": matches, "raw_rows": raw,
            "canonical_counts": candles, "manifest_digest": manifest["manifest_digest"]}


def paired_coverage(connection: sqlite3.Connection, capture_id: str | None = None) -> dict:
    has_series = capture_id and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_series_bars'"
    ).fetchone() and connection.execute(
        "SELECT 1 FROM research_series_bars WHERE capture_id=? LIMIT 1", (capture_id,)
    ).fetchone()
    if has_series:
        nq = {row[0] for row in connection.execute(
            "SELECT open_time FROM research_series_bars WHERE capture_id=? AND root_symbol='NQ'", (capture_id,))}
        es = {row[0] for row in connection.execute(
            "SELECT open_time FROM research_series_bars WHERE capture_id=? AND root_symbol='ES'", (capture_id,))}
        union, paired = nq | es, nq & es
        nq_contracts = [row[0] for row in connection.execute(
            "SELECT DISTINCT contract FROM research_series_bars WHERE capture_id=? AND root_symbol='NQ' ORDER BY contract", (capture_id,))]
        es_contracts = [row[0] for row in connection.execute(
            "SELECT DISTINCT contract FROM research_series_bars WHERE capture_id=? AND root_symbol='ES' ORDER BY contract", (capture_id,))]
        start = max(min(nq), min(es)) if nq and es else None
        end = min(max(nq), max(es)) if nq and es else None
        nq_overlap = {x for x in nq if start <= x <= end} if start and end else set()
        es_overlap = {x for x in es if start <= x <= end} if start and end else set()
        overlap_union, overlap_pair = nq_overlap | es_overlap, nq_overlap & es_overlap
        return {"nq_minutes": len(nq), "es_minutes": len(es), "paired_minutes": len(paired),
                "missing_nq_minutes": len(es-nq), "missing_es_minutes": len(nq-es),
                "pair_coverage_percentage": 100.0*len(paired)/len(union) if union else 0.0,
                "common_overlap_coverage_percentage": 100.0*len(overlap_pair)/len(overlap_union) if overlap_union else 0.0,
                "common_overlap_start": start, "common_overlap_end": end,
                "pair_label":"MNQ/ES PAIRED COVERAGE", "smt_source":"MNQ vs ES",
                "nasdaq_source":"MNQ FAMILY PROXY", "nq_contracts":nq_contracts,"es_contracts":es_contracts}
    clause, args = (" AND capture_id=?", (capture_id,)) if capture_id else ("", ())
    nq = {row[0] for row in connection.execute("SELECT open_time FROM canonical_candles WHERE timeframe='1m' AND root_symbol='NQ'"+clause, args)}
    es = {row[0] for row in connection.execute("SELECT open_time FROM canonical_candles WHERE timeframe='1m' AND root_symbol='ES'"+clause, args)}
    union = nq | es
    paired = nq & es
    nq_contracts = [row[0] for row in connection.execute("SELECT DISTINCT contract FROM canonical_candles WHERE timeframe='1m' AND root_symbol='NQ'"+clause+" ORDER BY contract", args)]
    es_contracts = [row[0] for row in connection.execute("SELECT DISTINCT contract FROM canonical_candles WHERE timeframe='1m' AND root_symbol='ES'"+clause+" ORDER BY contract", args)]
    nq_instrument = "MNQ" if nq_contracts and all(item.startswith("MNQ ") for item in nq_contracts) else "NQ"
    es_instrument = "MES" if es_contracts and all(item.startswith("MES ") for item in es_contracts) else "ES"
    return {"nq_minutes": len(nq), "es_minutes": len(es), "paired_minutes": len(paired),
            "missing_nq_minutes": len(es - nq), "missing_es_minutes": len(nq - es),
            "pair_coverage_percentage": 100.0 * len(paired) / len(union) if union else 0.0,
            "pair_label": f"{nq_instrument}/{es_instrument} PAIRED COVERAGE",
            "smt_source": f"{nq_instrument} vs {es_instrument}",
            "nq_contracts": nq_contracts, "es_contracts": es_contracts}


def observed_roll_boundaries(connection: sqlite3.Connection, root_symbol: str,
                             capture_id: str | None = None) -> list[dict]:
    """Report observed contract transitions without creating a synthetic continuous series."""
    clause = " AND capture_id=?" if capture_id else ""
    args = (root_symbol, capture_id) if capture_id else (root_symbol,)
    rows = connection.execute(f"""SELECT contract,MIN(open_time) start_time,MAX(close_time) end_time
      FROM canonical_candles WHERE root_symbol=? AND timeframe='1m'{clause}
      GROUP BY contract ORDER BY start_time,contract""", args).fetchall()
    result = []
    for prior, current in zip(rows, rows[1:]):
        result.append({"root_symbol": root_symbol, "from_contract": prior[0],
                       "to_contract": current[0], "from_end": prior[2],
                       "to_start": current[1], "overlap": current[1] <= prior[2],
                       "method": "OBSERVED_COVERAGE_ONLY_ROLL_SCHEDULE_REQUIRED"})
    return result
