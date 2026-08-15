from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MarketClock:
    """Tracks event-time independently from wall-clock time.

    NinjaTrader Market Replay sends historical event timestamps. OTR should use
    those timestamps for candles/strategy logic while still using wall-clock
    ingress time to decide whether the bridge is actively streaming.
    """

    latest_event: dict[str, datetime] = field(default_factory=dict)
    latest_ingest: dict[str, datetime] = field(default_factory=dict)

    def update(self, symbol: str, event_time: datetime, ingest_time: datetime | None = None) -> None:
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        else:
            event_time = event_time.astimezone(timezone.utc)
        ingest_time = ingest_time or utc_now()
        if ingest_time.tzinfo is None:
            ingest_time = ingest_time.replace(tzinfo=timezone.utc)
        else:
            ingest_time = ingest_time.astimezone(timezone.utc)
        self.latest_event[symbol] = event_time
        self.latest_ingest[symbol] = ingest_time

    def event_time(self, symbol: str) -> datetime | None:
        return self.latest_event.get(symbol)

    def ingress_age_seconds(self, symbol: str) -> float | None:
        value = self.latest_ingest.get(symbol)
        if value is None:
            return None
        return max(0.0, (utc_now() - value).total_seconds())

    def mode(self, symbol: str, replay_threshold_seconds: float = 300.0) -> str:
        event = self.latest_event.get(symbol)
        ingest = self.latest_ingest.get(symbol)
        if event is None or ingest is None:
            return "WAITING"
        if abs((ingest - event).total_seconds()) > replay_threshold_seconds:
            return "REPLAY"
        return "LIVE"
