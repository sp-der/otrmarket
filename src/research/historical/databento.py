"""Research-only Databento DBN acquisition for immutable futures captures."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import zipfile

from .acquisition import CONSTRUCTION_VERSION, _build_import_candles, _digest, _git_commit, expected_minutes
from .catalog import CONTRACT_SPECS, MONTH_CODES, contract_spec, parse_contract
from .store import HistoricalStore, utc_iso


SOURCE = "DATABENTO"
VALIDATION_VERSION = "OTR_DATABENTO_1M_V1"
MONTH_BY_CODE = {"F":"JAN", "G":"FEB", "H":"MAR", "J":"APR", "K":"MAY", "M":"JUN",
                 "N":"JUL", "Q":"AUG", "U":"SEP", "V":"OCT", "X":"NOV", "Z":"DEC"}


class DatabentoImportError(ValueError):
    pass


def verify_package(path: str | Path) -> dict:
    package = Path(path).resolve()
    package_hash = hashlib.sha256(package.read_bytes()).hexdigest()
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        results = []
        for item in manifest["files"]:
            payload = archive.read(item["filename"])
            actual = hashlib.sha256(payload).hexdigest()
            expected = item["hash"].removeprefix("sha256:")
            results.append({"filename": item["filename"], "size": len(payload),
                            "expected_sha256": expected, "actual_sha256": actual,
                            "verified": len(payload) == item["size"] and actual == expected})
        metadata = json.loads(archive.read("metadata.json"))
        conditions = json.loads(archive.read("condition.json"))
    if not all(item["verified"] for item in results):
        raise DatabentoImportError("Databento manifest verification failed")
    degraded = [item["date"] for item in conditions if str(item.get("condition", "")).upper() == "DEGRADED"]
    counts = Counter(str(item.get("condition", "UNKNOWN")).upper() for item in conditions)
    payload = next(item for item in results if ".dbn" in item["filename"])
    return {"package": str(package), "package_sha256": package_hash,
            "job_id": manifest["job_id"], "files": results, "metadata": metadata,
            "condition_counts": dict(counts), "degraded_dates": degraded,
            "payload_filename": payload["filename"], "payload_sha256": payload["actual_sha256"],
            "payload_size": payload["size"], "manifest_verified": True}


def raw_symbol_contract(raw_symbol: str) -> tuple[str, str]:
    match = re.fullmatch(r"(MNQ|ES|GC)([FGHJKMNQUVXZ])(\d)", raw_symbol)
    if not match:
        raise DatabentoImportError(f"Not a supported outright contract: {raw_symbol}")
    instrument, month_code, year_digit = match.groups()
    year = 2020 + int(year_digit)
    return instrument, f"{instrument} {MONTH_BY_CODE[month_code]}{year % 100:02d}"


def _load_store(path: Path):
    try:
        import databento as db  # type: ignore
    except ImportError as exc:
        raise DatabentoImportError("Install pinned research dependency: pip install -r requirements-research.txt") from exc
    return db.DBNStore.from_file(path)


def _instrument_map(metadata) -> dict[int, dict]:
    result = {}
    for raw_symbol, intervals in metadata.mappings.items():
        if "-" in raw_symbol:
            continue
        try:
            instrument, contract = raw_symbol_contract(raw_symbol)
        except DatabentoImportError:
            continue
        spec = contract_spec(instrument)
        for interval in intervals:
            instrument_id = int(interval["symbol"])
            item = {"instrument_id": instrument_id, "raw_symbol": raw_symbol,
                    "instrument": instrument, "contract": contract, "root": spec.root,
                    "size_class": spec.size_class, "mapping_start": interval["start_date"].isoformat(),
                    "mapping_end": interval["end_date"].isoformat()}
            prior = result.get(instrument_id)
            if prior and prior != item:
                raise DatabentoImportError(f"Ambiguous instrument mapping for {instrument_id}")
            result[instrument_id] = item
    return result


def _iso_ns(value: int) -> str:
    return datetime.fromtimestamp(value / 1_000_000_000, timezone.utc).isoformat()


def _price(value: int) -> float:
    return value / 1_000_000_000


def inspect_dbn(payload_path: str | Path) -> dict:
    store = _load_store(Path(payload_path))
    instruments = _instrument_map(store.metadata)
    stats = defaultdict(lambda: {"rows": 0, "volume": 0, "first_ns": None, "last_ns": None,
                                 "zero_volume": 0, "ohlc_errors": 0, "tick_errors": 0})
    duplicate_count = reversal_count = malformed_count = 0
    seen = set()
    prior_timestamp = None
    daily_volume = defaultdict(int)
    for record in store:
        item = instruments.get(record.instrument_id)
        if not item:
            continue
        key = (record.instrument_id, record.ts_event)
        duplicate_count += key in seen
        seen.add(key)
        reversal_count += prior_timestamp is not None and record.ts_event < prior_timestamp
        prior_timestamp = record.ts_event
        values = (record.open, record.high, record.low, record.close)
        bad = not (record.low <= record.open <= record.high and record.low <= record.close <= record.high and record.low <= record.high)
        spec = contract_spec(item["instrument"])
        tick_ns = round(spec.tick_size * 1_000_000_000)
        tick_bad = any(value % tick_ns for value in values)
        malformed_count += bad
        row = stats[record.instrument_id]
        row["rows"] += 1; row["volume"] += int(record.volume)
        row["first_ns"] = record.ts_event if row["first_ns"] is None else min(row["first_ns"], record.ts_event)
        row["last_ns"] = record.ts_event if row["last_ns"] is None else max(row["last_ns"], record.ts_event)
        row["zero_volume"] += record.volume == 0; row["ohlc_errors"] += bad; row["tick_errors"] += tick_bad
        day = datetime.fromtimestamp(record.ts_event / 1e9, timezone.utc).date().isoformat()
        daily_volume[(item["root"], day, record.instrument_id)] += int(record.volume)
    contracts = []
    for instrument_id, row in stats.items():
        item = instruments[instrument_id]
        contracts.append({**item, **{k:v for k,v in row.items() if not k.endswith("_ns")},
                          "first": _iso_ns(row["first_ns"]), "last": _iso_ns(row["last_ns"])})
    leaders = {}
    by_day = defaultdict(list)
    for (root, day, instrument_id), volume in daily_volume.items():
        by_day[(root, day)].append((volume, instrument_id))
    for key, values in by_day.items():
        leaders[key] = max(values, key=lambda value: (value[0], -value[1]))[1]
    transitions = []
    for root in ("NQ", "ES", "GC"):
        prior = None
        for (item_root, day), iid in sorted(leaders.items()):
            if item_root != root: continue
            if prior is not None and prior[1] != iid:
                transitions.append({"root":root,"date":day,"from_contract":instruments[prior[1]]["contract"],
                                    "to_contract":instruments[iid]["contract"],"method":"DAILY_MAX_TOTAL_VOLUME"})
            prior = (day, iid)
    return {"dataset": store.metadata.dataset, "schema": str(store.metadata.schema.value),
            "start": _iso_ns(store.metadata.start), "end_exclusive": _iso_ns(store.metadata.end),
            "symbols": list(store.metadata.symbols), "stype_in": store.metadata.stype_in.value,
            "stype_out": store.metadata.stype_out.value, "total_outright_rows": sum(x["rows"] for x in contracts),
            "contracts": sorted(contracts, key=lambda x:(x["root"],x["raw_symbol"])),
            "duplicates": duplicate_count, "timestamp_reversals": reversal_count,
            "malformed_records": malformed_count, "daily_leaders": leaders,
            "transitions": transitions, "instrument_map": instruments}


def _series_quality(connection: sqlite3.Connection, capture_id: str, root: str) -> dict:
    stamps = {datetime.fromisoformat(row[0]) for row in connection.execute(
        "SELECT open_time FROM research_series_bars WHERE capture_id=? AND root_symbol=?", (capture_id, root))}
    if not stamps:
        return {"minutes":0,"expected":0,"missing":0,"coverage_percentage":0.0}
    expected = set(expected_minutes(min(stamps), max(stamps)))
    present = stamps & expected
    return {"first":utc_iso(min(stamps)),"last":utc_iso(max(stamps)),"minutes":len(stamps),
            "expected":len(expected),"missing":len(expected-stamps),
            "coverage_percentage":100.0*len(present)/len(expected) if expected else 0.0}


def _pair_quality(connection: sqlite3.Connection, capture_id: str) -> dict:
    def stamps(root):
        return {datetime.fromisoformat(r[0]) for r in connection.execute(
            "SELECT open_time FROM research_series_bars WHERE capture_id=? AND root_symbol=?",(capture_id,root))}
    nq, es = stamps("NQ"), stamps("ES")
    union, paired = nq|es, nq&es
    result={"smt_source":"MNQ vs ES","nasdaq_source":"MNQ FAMILY PROXY",
            "union":{"mnq_minutes":len(nq),"es_minutes":len(es),"paired_minutes":len(paired),
                     "missing_mnq":len(es-nq),"missing_es":len(nq-es),
                     "coverage_percentage":100*len(paired)/len(union) if union else 0.0}}
    if nq and es:
        start=max(min(nq),min(es)); end=min(max(nq),max(es)); nq2={x for x in nq if start<=x<=end}; es2={x for x in es if start<=x<=end}; u=nq2|es2; p=nq2&es2
        result["common_overlap"]={"start":utc_iso(start),"end":utc_iso(end),"mnq_minutes":len(nq2),"es_minutes":len(es2),
                                  "paired_minutes":len(p),"missing_mnq":len(es2-nq2),"missing_es":len(nq2-es2),
                                  "coverage_percentage":100*len(p)/len(u) if u else 0.0}
    return result


def import_databento_package(package_path: str | Path, database: str | Path, capture_id: str,
                             *, dry_run: bool = False, batch_size: int = 5000) -> dict:
    package = verify_package(package_path)
    with tempfile.TemporaryDirectory(prefix="otr-dbn-") as directory:
        with zipfile.ZipFile(package["package"]) as archive:
            payload_path = Path(archive.extract(package["payload_filename"], directory))
        report = inspect_dbn(payload_path)
        summary={"capture_id":capture_id,"package":package,"inventory":{k:v for k,v in report.items() if k not in ("daily_leaders","instrument_map")},"dry_run":dry_run}
        if dry_run: return summary
        if report["duplicates"] or report["timestamp_reversals"] or report["malformed_records"]:
            raise DatabentoImportError("Critical decoded-record integrity findings prevent import")
        store=HistoricalStore(database); store.initialize()
        with store.connect() as c:
            if c.execute("SELECT 1 FROM capture_sessions WHERE capture_id=?",(capture_id,)).fetchone():
                raise DatabentoImportError(f"Capture already exists and cannot be overwritten: {capture_id}")
        store.create_capture(capture_id,SOURCE,"IMPORT",report["start"],f"Immutable Databento batch {package['job_id']}")
        imported_at=utc_iso(datetime.now(timezone.utc))
        try:
            dbn=_load_store(payload_path); sequence=0; payload=[]
            with store.connect() as c:
                for iid,item in sorted(report["instrument_map"].items()):
                    spec,expiry=parse_contract(item["contract"]); store._ensure_contract(c,item["contract"],spec,expiry,item["mapping_start"],item["mapping_end"],SOURCE)
                    c.execute("INSERT INTO databento_instruments VALUES(?,?,?,?,?,?,?)",(capture_id,iid,item["raw_symbol"],item["contract"],item["root"],item["mapping_start"],item["mapping_end"]))
                for record in dbn:
                    item=report["instrument_map"].get(record.instrument_id)
                    if not item: continue
                    sequence+=1; stamp=_iso_ns(record.ts_event); vals=tuple(_price(v) for v in (record.open,record.high,record.low,record.close))
                    digest=_digest([item["raw_symbol"],record.instrument_id,stamp,*vals,int(record.volume)])
                    payload.append((capture_id,package["payload_filename"],sequence,item["root"],item["contract"],item["size_class"],"UTC",stamp,stamp,1,*vals,int(record.volume),digest,"VALID","[]",record.instrument_id,item["raw_symbol"],getattr(record,"publisher_id",None)))
                    if len(payload)>=batch_size:
                        _insert_rows(c,payload); payload.clear()
                if payload: _insert_rows(c,payload)
                _build_import_candles(c,capture_id,"DATABENTO GLBX.MDP3")
                # Select one exact contract per root/day by observed total volume; every selected bar retains provenance.
                for (root,day),iid in sorted(report["daily_leaders"].items()):
                    item=report["instrument_map"][iid]
                    c.execute("""INSERT INTO research_series_bars
                      SELECT ?,?,cc.open_time,cc.candle_id,?,cc.contract,'DAILY_MAX_TOTAL_VOLUME'
                      FROM canonical_candles cc WHERE cc.capture_id=? AND cc.contract=? AND cc.timeframe='1m'
                      AND substr(cc.open_time,1,10)=?""",(capture_id,root,iid,capture_id,item["contract"],day))
                counts=dict(c.execute("SELECT timeframe,COUNT(*) FROM canonical_candles WHERE capture_id=? GROUP BY timeframe",(capture_id,)))
                qualities={root:_series_quality(c,capture_id,root) for root in ("NQ","ES","GC")}; pairing=_pair_quality(c,capture_id)
                first=min(v["first"] for v in qualities.values() if v.get("first")); last=max(v["last"] for v in qualities.values() if v.get("last"))
                duration=(datetime.fromisoformat(last)-datetime.fromisoformat(first)).total_seconds()/86400
                critical=sum(x["ohlc_errors"]+x["tick_errors"] for x in report["contracts"])
                common=pairing.get("common_overlap",{}).get("coverage_percentage",0)
                status="VALIDATED" if duration>=90 and critical==0 and common>=99 and min(q["coverage_percentage"] for q in qualities.values())>=99 else "USABLE_WITH_WARNINGS"
                integrity={"duplicates":report["duplicates"],"timestamp_reversals":report["timestamp_reversals"],"malformed":report["malformed_records"],"critical":critical,"series_quality":qualities,"pairing":pairing,"degraded_dates":package["degraded_dates"],"validation_version":VALIDATION_VERSION}
                contracts=sorted({x["contract"] for x in report["contracts"]})
                manifest_payload={"capture_id":capture_id,"source":SOURCE,"imported_at":imported_at,"markets":["ES","GC","NQ"],"contracts":contracts,"start_time":first,"end_time":last,"source_timezone":"UTC","resolution":"1m","raw_row_count":report["total_outright_rows"],"canonical_counts":counts,"coverage_percentage":min(q["coverage_percentage"] for q in qualities.values()),"integrity_summary":integrity,"roll_boundaries":report["transitions"],"git_commit":_git_commit(),"construction_version":CONSTRUCTION_VERSION,"validation_status":status,"holiday_calendar_status":"GENERIC_CME_WEEKLY_HOURS_HOLIDAYS_UNVERIFIED"}
                mdigest=_digest(manifest_payload)
                c.execute("INSERT INTO capture_manifests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(capture_id,SOURCE,imported_at,json.dumps(manifest_payload["markets"]),json.dumps(contracts),first,last,"UTC","1m",report["total_outright_rows"],json.dumps(counts,sort_keys=True),manifest_payload["coverage_percentage"],json.dumps(integrity,sort_keys=True),json.dumps(report["transitions"],sort_keys=True),manifest_payload["git_commit"],CONSTRUCTION_VERSION,status,manifest_payload["holiday_calendar_status"],mdigest))
                c.execute("INSERT INTO provider_capture_metadata VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(capture_id,"DATABENTO",report["dataset"],report["schema"],package["job_id"],package["package_sha256"],package["payload_sha256"],report["start"],report["end_exclusive"],json.dumps(package["condition_counts"],sort_keys=True),json.dumps(package["degraded_dates"]),json.dumps(package["metadata"],sort_keys=True)))
                c.execute("UPDATE capture_sessions SET ended_at=? WHERE capture_id=?",(last,capture_id))
            return {**summary,"canonical_counts":counts,"series_quality":qualities,"pairing":pairing,"duration_days":duration,"validation_status":status,"manifest_digest":mdigest}
        except Exception:
            # An import is transactional after capture creation; remove only an empty failed capture shell.
            with store.connect() as c:
                c.execute("DELETE FROM capture_sessions WHERE capture_id=? AND NOT EXISTS (SELECT 1 FROM raw_import_bars WHERE capture_id=?)",(capture_id,capture_id))
            raise


def _insert_rows(connection: sqlite3.Connection, rows: list[tuple]) -> None:
    for row in rows:
        base, iid, raw_symbol, publisher = row[:-3], row[-3], row[-2], row[-1]
        connection.execute("""INSERT INTO raw_import_bars(capture_id,source_file,source_row_number,root_symbol,contract,size_class,source_timezone,original_timestamp,normalized_timestamp,interval_minutes,open,high,low,close,volume,row_digest,integrity_status,findings_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",base)
        raw_id=connection.execute("SELECT last_insert_rowid()").fetchone()[0]
        connection.execute("INSERT INTO databento_bar_provenance VALUES(?,?,?,?,?)",(raw_id,base[0],iid,raw_symbol,publisher))
