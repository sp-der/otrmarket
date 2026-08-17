from __future__ import annotations

from src.strategies.manager import MultiStrategyEngine
from src.strategies.reversal import ReversalEngine


class AdaptiveStrategyEngine(MultiStrategyEngine):
    """Operation 5.8 coordinator: continuation + rejection block + MSS reversal."""

    def __init__(self) -> None:
        super().__init__()
        self.reversal = ReversalEngine()
        self.engines = (self.ict, self.rejection_block, self.reversal)

    @staticmethod
    def _reversal_progress(diag: dict | None) -> float:
        if not diag:
            return -1.0
        return float(diag.get("checklist_score", 0)) / max(1.0, float(diag.get("checklist_total", 6)))

    def _refresh_diagnostic(self, symbol: str, timeframe: str) -> None:
        items = []
        ict = self.ict.diagnostic(symbol, timeframe)
        rb = self.rejection_block.diagnostic(symbol, timeframe)
        reversal = self.reversal.diagnostic(symbol, timeframe)
        if ict:
            item = dict(ict)
            item["strategy_name"] = "ICT_CONFLUENCE"
            item["strategy_score"] = round(self._legacy_progress(item) * 100)
            item["note"] = f"ICT {item.get('note', '')}".strip()
            items.append((self._legacy_progress(item), item))
        if rb:
            item = dict(rb)
            item["strategy_name"] = "REJECTION_BLOCK_10_10"
            item["strategy_score"] = round(self._rb_progress(item) * 100)
            items.append((self._rb_progress(item), item))
        if reversal:
            item = dict(reversal)
            item["strategy_name"] = "MSS_REVERSAL"
            item["strategy_score"] = round(self._reversal_progress(item) * 100)
            items.append((self._reversal_progress(item), item))
        if items:
            self.diagnostics[(symbol, timeframe)] = max(items, key=lambda pair: pair[0])[1]

    def on_candle_all(self, symbol: str, timeframe: str, histories) -> list:
        candidates = list(super().on_candle_all(symbol, timeframe, histories))
        reversal = self.reversal.on_candle(symbol, timeframe, histories)
        if reversal:
            reversal.metadata.setdefault("strategy", "MSS_REVERSAL")
            candidates.append(reversal)

        if len(candidates) > 1:
            rejection = next((s for s in candidates if s.metadata.get("strategy") == "REJECTION_BLOCK_10_10"), None)
            chosen = rejection or max(candidates, key=lambda s: float(getattr(s, "risk_reward", 0) or 0))
            chosen.metadata["also_detected_by"] = [s.metadata.get("strategy", "UNKNOWN") for s in candidates if s is not chosen]
            chosen.metadata["strategy_collision_deduped"] = True
            candidates = [chosen]

        if candidates:
            self.last_setup = candidates[-1]
        self._refresh_diagnostic(symbol, timeframe)
        self._refresh_events()
        return candidates
