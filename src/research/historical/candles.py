from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json


TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _floor(value: datetime, minutes: int) -> datetime:
    epoch = int(value.timestamp())
    seconds = minutes * 60
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def build_canonical_candles(connection, capture_id: str) -> int:
    """Create immutable 1m bars from events, then derive every HTF from 1m."""
    existing = connection.execute(
        "SELECT COUNT(*) FROM canonical_candles WHERE capture_id=?", (capture_id,)
    ).fetchone()[0]
    if existing:
        raise ValueError(f"Capture {capture_id} already has canonical candles")

    rows = connection.execute(
        """SELECT * FROM historical_events WHERE capture_id=?
           ORDER BY contract, exchange_timestamp, sequence_no""", (capture_id,)
    ).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["contract"], _floor(_dt(row["exchange_timestamp"]), 1))].append(row)

    one_minute = []
    for (contract, opened), events in sorted(grouped.items()):
        prices = [float(event["last_price"]) for event in events]
        volumes = [event["volume"] for event in events]
        sources = sorted({event["source"] for event in events})
        unknown_volume = any(volume is None for volume in volumes)
        gap = any(event["data_gap"] for event in events)
        integrity_bad = any(event["integrity_status"] != "VALID" for event in events)
        completeness = "INCOMPLETE" if gap or integrity_bad or unknown_volume else "COMPLETE"
        one_minute.append({
            "capture_id": capture_id, "contract": contract, "root_symbol": events[0]["root_symbol"],
            "timeframe": "1m", "open_time": opened.isoformat(),
            "close_time": (opened + timedelta(minutes=1)).isoformat(),
            "open": prices[0], "high": max(prices), "low": min(prices), "close": prices[-1],
            "volume": None if unknown_volume else sum(int(v) for v in volumes),
            "event_count": len(events), "completeness_state": completeness,
            "source_coverage": json.dumps(sources, separators=(",", ":")),
            "gap_state": "GAPPED" if gap else "NO_GAP_DETECTED",
        })
    _insert(connection, one_minute)

    all_bars = list(one_minute)
    for timeframe, minutes in TIMEFRAMES.items():
        if timeframe == "1m":
            continue
        buckets = defaultdict(list)
        for bar in one_minute:
            buckets[(bar["contract"], _floor(_dt(bar["open_time"]), minutes))].append(bar)
        derived = []
        for (contract, opened), bars in sorted(buckets.items()):
            bars.sort(key=lambda item: item["open_time"])
            expected = minutes
            observed_offsets = {_dt(bar["open_time"]) for bar in bars}
            continuous = all(opened + timedelta(minutes=i) in observed_offsets for i in range(expected))
            complete = continuous and all(bar["completeness_state"] == "COMPLETE" for bar in bars)
            sources = sorted({source for bar in bars for source in json.loads(bar["source_coverage"])})
            derived.append({
                "capture_id": capture_id, "contract": contract, "root_symbol": bars[0]["root_symbol"],
                "timeframe": timeframe, "open_time": opened.isoformat(),
                "close_time": (opened + timedelta(minutes=minutes)).isoformat(),
                "open": bars[0]["open"], "high": max(b["high"] for b in bars),
                "low": min(b["low"] for b in bars), "close": bars[-1]["close"],
                "volume": None if any(b["volume"] is None for b in bars) else sum(b["volume"] for b in bars),
                "event_count": sum(b["event_count"] for b in bars),
                "completeness_state": "COMPLETE" if complete else "INCOMPLETE",
                "source_coverage": json.dumps(sources, separators=(",", ":")),
                "gap_state": "NO_GAP_DETECTED" if continuous and all(b["gap_state"] == "NO_GAP_DETECTED" for b in bars) else "GAPPED",
            })
        _insert(connection, derived)
        all_bars.extend(derived)
    return len(all_bars)


def _insert(connection, bars):
    connection.executemany(
        """INSERT INTO canonical_candles(
          capture_id,contract,root_symbol,timeframe,open_time,close_time,open,high,low,close,
          volume,event_count,completeness_state,source_coverage,gap_state
        ) VALUES(:capture_id,:contract,:root_symbol,:timeframe,:open_time,:close_time,:open,:high,:low,:close,
          :volume,:event_count,:completeness_state,:source_coverage,:gap_state)""", bars,
    )
