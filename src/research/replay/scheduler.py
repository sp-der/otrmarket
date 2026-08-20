from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from heapq import merge


ROOT_PRIORITY = {"NQ": 0, "ES": 1, "GC": 2}
TF_PRIORITY = {"1h": 0, "30m": 1, "15m": 2, "5m": 3, "1m": 4}


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReplayItem:
    timestamp: str
    root_symbol: str
    contract: str
    source_sequence: int
    payload: object

    def key(self):
        return (parse_time(self.timestamp), ROOT_PRIORITY[self.root_symbol], self.contract, self.source_sequence)


def merge_event_streams(streams) -> list[ReplayItem]:
    prepared = [sorted(stream, key=lambda item: item.key()) for stream in streams]
    return list(merge(*prepared, key=lambda item: item.key()))


def synchronized_candle_groups(rows) -> list[tuple[str, list]]:
    """Publish all same-close candles together; HTF precedes execution TF."""
    groups = {}
    for row in rows:
        groups.setdefault(row["close_time"], []).append(row)
    return [(close, sorted(items, key=lambda r: (TF_PRIORITY[r["timeframe"]], ROOT_PRIORITY[r["root_symbol"]], r["contract"]))) for close, items in sorted(groups.items(), key=lambda pair: parse_time(pair[0]))]


def pair_available(histories, symbol: str, timeframe: str, close_time: str) -> bool:
    if symbol not in {"NQ", "ES"}:
        return True
    pair = "ES" if symbol == "NQ" else "NQ"
    values = histories.get((pair, timeframe), ())
    return bool(values and values[-1].close_time.isoformat() == parse_time(close_time).isoformat())
