"""Causal, research-only contract selection derived from immutable source bars."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from .catalog import parse_contract


SELECTOR_VERSION = "PREVIOUS_UTC_DAY_VOLUME_V1"


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _expiry(contract: str) -> date:
    _, expiry = parse_contract(contract)
    if expiry is None:
        raise ValueError(f"Exact expiry is required for causal selection: {contract}")
    return expiry


def causal_decisions(daily_volume: dict[tuple[str, str, int, str], int]) -> list[dict]:
    """Choose today's contract using only completed information through yesterday.

    The first date uses the nearest listed expiry (metadata-only bootstrap). Later
    decisions use the prior UTC day's total volume and never roll backward.
    """
    by_root_day: dict[tuple[str, str], list[tuple[str, int, int]]] = defaultdict(list)
    for (root, day, instrument_id, contract), volume in daily_volume.items():
        by_root_day[(root, day)].append((contract, instrument_id, int(volume)))
    decisions = []
    for root in sorted({key[0] for key in by_root_day}):
        days = sorted(day for item_root, day in by_root_day if item_root == root)
        selected = None
        for index, day in enumerate(days):
            available = by_root_day[(root, day)]
            if index == 0:
                contract, iid, _ = min(available, key=lambda x: (_expiry(x[0]), x[0], x[1]))
                evidence = {"method": "NEAREST_EXPIRY_METADATA_BOOTSTRAP", "available_contracts": sorted(x[0] for x in available)}
                evidence_end = None
            else:
                prior_day = days[index - 1]
                prior = by_root_day[(root, prior_day)]
                eligible = [x for x in prior if selected is None or _expiry(x[0]) >= _expiry(selected[0])]
                leader = max(eligible or prior, key=lambda x: (x[2], -int(_expiry(x[0]).strftime("%Y%m%d")), x[0]))
                # If yesterday's winner has no bar today, retain current when possible,
                # otherwise advance to the nearest available non-backward contract.
                today_by_contract = {x[0]: x for x in available}
                if leader[0] in today_by_contract:
                    contract, iid, _ = today_by_contract[leader[0]]
                elif selected and selected[0] in today_by_contract:
                    contract, iid, _ = today_by_contract[selected[0]]
                else:
                    forward = [x for x in available if selected is None or _expiry(x[0]) >= _expiry(selected[0])]
                    contract, iid, _ = min(forward or available, key=lambda x: (_expiry(x[0]), x[0], x[1]))
                evidence = {"method": "PREVIOUS_COMPLETED_UTC_DAY_VOLUME", "evidence_day": prior_day,
                            "volumes": {x[0]: x[2] for x in sorted(prior)}, "leader": leader[0]}
                evidence_end = f"{prior_day}T23:59:59.999999+00:00"
            selected = (contract, iid)
            decisions.append({"root_symbol": root, "effective_date": day,
                              "decision_timestamp": f"{day}T00:00:00+00:00",
                              "selected_contract": contract, "instrument_id": iid,
                              "evidence_end_time": evidence_end, "evidence": evidence,
                              "selector_version": SELECTOR_VERSION})
    return decisions


def build_causal_series(database: str | Path, capture_id: str) -> dict:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute("SELECT 1 FROM causal_roll_decisions WHERE capture_id=?", (capture_id,)).fetchone():
            raise ValueError(f"Causal derived series already exists and is immutable: {capture_id}")
        volumes = {}
        for row in connection.execute("""SELECT r.root_symbol,substr(r.normalized_timestamp,1,10) day,
          p.instrument_id,r.contract,SUM(r.volume) volume
          FROM raw_import_bars r JOIN databento_bar_provenance p ON p.raw_bar_id=r.raw_bar_id
          WHERE r.capture_id=? GROUP BY r.root_symbol,day,p.instrument_id,r.contract""", (capture_id,)):
            volumes[(row[0], row[1], int(row[2]), row[3])] = int(row[4])
        decisions = causal_decisions(volumes)
        with connection:
            for item in decisions:
                connection.execute("INSERT INTO causal_roll_decisions VALUES(?,?,?,?,?,?,?,?,?)",
                    (capture_id, item["root_symbol"], item["effective_date"], item["decision_timestamp"],
                     item["selected_contract"], item["instrument_id"], item["evidence_end_time"],
                     json.dumps(item["evidence"], sort_keys=True), SELECTOR_VERSION))
                connection.execute("""INSERT INTO causal_research_series_bars
                  SELECT ?,?,cc.open_time,cc.candle_id,?,cc.contract,?,?
                  FROM canonical_candles cc WHERE cc.capture_id=? AND cc.contract=? AND cc.timeframe='1m'
                   AND substr(cc.open_time,1,10)=?""",
                  (capture_id,item["root_symbol"],item["instrument_id"],item["effective_date"],SELECTOR_VERSION,
                   capture_id,item["selected_contract"],item["effective_date"]))
        transitions = []
        prior = {}
        for item in decisions:
            old = prior.get(item["root_symbol"])
            if old and old != item["selected_contract"]:
                transitions.append({"root":item["root_symbol"],"effective_date":item["effective_date"],
                                    "from_contract":old,"to_contract":item["selected_contract"],
                                    "decision_timestamp":item["decision_timestamp"],
                                    "evidence_end_time":item["evidence_end_time"]})
            prior[item["root_symbol"]] = item["selected_contract"]
        count = connection.execute("SELECT COUNT(*) FROM causal_research_series_bars WHERE capture_id=?", (capture_id,)).fetchone()[0]
        payload = {"capture_id":capture_id,"selector_version":SELECTOR_VERSION,"decision_count":len(decisions),
                   "bar_count":count,"transitions":transitions}
        payload["digest"] = _digest(payload)
        return payload
    finally:
        connection.close()
