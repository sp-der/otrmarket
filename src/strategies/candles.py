from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from src.strategies.models import Candle


TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
}


class CandleBuilder:
    """Builds UTC OHLC candles from live quote/trade prices."""

    def __init__(self, timeframes=None, history_limit: int = 1000):
        self.timeframes = tuple(timeframes or TIMEFRAME_SECONDS.keys())
        self.history_limit = history_limit
        self.current: dict[tuple[str, str], dict] = {}
        self.history: dict[tuple[str, str], deque[Candle]] = defaultdict(
            lambda: deque(maxlen=self.history_limit)
        )

    @staticmethod
    def _normalize_timestamp(timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None:
            return timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc)

    @staticmethod
    def _bucket_start(timestamp: datetime, seconds: int) -> datetime:
        epoch = int(timestamp.timestamp())
        bucket_epoch = epoch - (epoch % seconds)
        return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)

    def update(self, symbol: str, price: float, timestamp: datetime) -> list[Candle]:
        timestamp = self._normalize_timestamp(timestamp)
        closed: list[Candle] = []

        for timeframe in self.timeframes:
            seconds = TIMEFRAME_SECONDS[timeframe]
            bucket_start = self._bucket_start(timestamp, seconds)
            key = (symbol, timeframe)
            current = self.current.get(key)

            if current is None:
                self.current[key] = self._new_bucket(bucket_start, price)
                continue

            if bucket_start == current["open_time"]:
                current["high"] = max(current["high"], price)
                current["low"] = min(current["low"], price)
                current["close"] = price
                current["ticks"] += 1
                continue

            if bucket_start > current["open_time"]:
                candle = self._close_bucket(symbol, timeframe, current, seconds)
                self.history[key].append(candle)
                closed.append(candle)
                self.current[key] = self._new_bucket(bucket_start, price)

        return closed

    @staticmethod
    def _new_bucket(open_time: datetime, price: float) -> dict:
        return {
            "open_time": open_time,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "ticks": 1,
        }

    @staticmethod
    def _close_bucket(symbol: str, timeframe: str, bucket: dict, seconds: int) -> Candle:
        close_time = bucket["open_time"] + timedelta(seconds=seconds)
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=bucket["open_time"],
            close_time=close_time,
            open=bucket["open"],
            high=bucket["high"],
            low=bucket["low"],
            close=bucket["close"],
            ticks=bucket["ticks"],
        )

    def get_history(self, symbol: str, timeframe: str) -> list[Candle]:
        return list(self.history.get((symbol, timeframe), ()))

    def seed_history(self, candles: list[Candle]) -> None:
        for candle in sorted(candles, key=lambda item: item.open_time):
            self.history[(candle.symbol, candle.timeframe)].append(candle)

    def rewind_symbol(self, symbol: str, before: datetime) -> None:
        """Reset active buckets and discard future history after a replay rewind."""
        before = self._normalize_timestamp(before)
        for timeframe in self.timeframes:
            key = (symbol, timeframe)
            self.current.pop(key, None)
            kept = [candle for candle in self.history.get(key, ()) if candle.close_time <= before]
            self.history[key] = deque(kept, maxlen=self.history_limit)

    def has_future_history(self, symbol: str, timestamp: datetime) -> bool:
        """Return True when restored candles are ahead of the incoming stream."""
        timestamp = self._normalize_timestamp(timestamp)
        return any(
            history and history[-1].close_time > timestamp
            for (history_symbol, _), history in self.history.items()
            if history_symbol == symbol
        )
