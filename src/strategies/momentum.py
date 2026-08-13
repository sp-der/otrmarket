from collections import deque
from datetime import datetime, timezone

WINDOWS = {
    "1s": 1,
    "5s": 5,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "5m": 300,
}


class MomentumTracker:
    def __init__(self, retention_seconds=600):
        self.retention_seconds = retention_seconds
        self.history = {}

    def add_price(self, symbol: str, price: float):
        now = datetime.now(timezone.utc).timestamp()
        history = self.history.setdefault(symbol, deque())
        history.append((now, price))
        cutoff = now - self.retention_seconds
        while history and history[0][0] < cutoff:
            history.popleft()

    def _price_at_or_before(self, symbol: str, target_time: float):
        history = self.history.get(symbol)
        if not history:
            return None
        candidate = None
        for timestamp, price in history:
            if timestamp <= target_time:
                candidate = price
            else:
                break
        return candidate

    def returns(self, symbol: str):
        results = {name: None for name in WINDOWS}
        history = self.history.get(symbol)
        if not history:
            return results
        current_time, current_price = history[-1]
        for name, seconds in WINDOWS.items():
            old_price = self._price_at_or_before(symbol, current_time - seconds)
            if old_price is None or old_price == 0:
                continue
            results[name] = ((current_price - old_price) / old_price) * 100
        return results
