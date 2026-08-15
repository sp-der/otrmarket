from __future__ import annotations

from src.strategies.confluence import ConfluenceEngine
from src.strategies.rejection_block import RejectionBlockEngine


class MultiStrategyEngine:
    """Run independent setup engines behind the existing OTR runtime contract.

    The legacy ICT confluence model remains untouched. Rejection Block 10/10
    scans the same completed-candle histories independently. If both engines
    produce a setup for the same symbol/timeframe on the same evaluation, OTR
    emits one trade candidate instead of accidentally stacking duplicate risk.
    """

    def __init__(self) -> None:
        self.ict = ConfluenceEngine()
        self.rejection_block = RejectionBlockEngine()
        self.engines = (self.ict, self.rejection_block)
        self.diagnostics: dict[tuple[str, str], dict] = {}
        self.last_setup = None
        self.events: list[str] = []

    def clear_symbol(self, symbol: str) -> None:
        for engine in self.engines:
            engine.clear_symbol(symbol)
        for key in [key for key in self.diagnostics if key[0] == symbol]:
            self.diagnostics.pop(key, None)
        if self.last_setup and self.last_setup.symbol == symbol:
            self.last_setup = None

    @staticmethod
    def _legacy_progress(diag: dict | None) -> float:
        if not diag:
            return -1.0
        keys = ("pd_array", "signal", "displacement", "entry_fvg", "retracement", "rr")
        return sum(int(bool(diag.get(key))) for key in keys) / 6.0

    @staticmethod
    def _rb_progress(diag: dict | None) -> float:
        if not diag:
            return -1.0
        return float(diag.get("checklist_score", 0)) / max(
            1.0, float(diag.get("checklist_total", 10))
        )

    def _refresh_diagnostic(self, symbol: str, timeframe: str) -> None:
        ict_diag = self.ict.diagnostic(symbol, timeframe)
        rb_diag = self.rejection_block.diagnostic(symbol, timeframe)

        if ict_diag:
            ict_diag = dict(ict_diag)
            ict_diag["strategy_name"] = "ICT_CONFLUENCE"
            ict_diag["strategy_score"] = round(self._legacy_progress(ict_diag) * 100)
            if not str(ict_diag.get("note", "")).startswith("ICT "):
                ict_diag["note"] = f"ICT {ict_diag.get('note', '')}".strip()

        chosen = (
            rb_diag
            if self._rb_progress(rb_diag) >= self._legacy_progress(ict_diag)
            else ict_diag
        )
        if chosen:
            self.diagnostics[(symbol, timeframe)] = dict(chosen)

    def diagnostic(self, symbol: str, timeframe: str) -> dict | None:
        value = self.diagnostics.get((symbol, timeframe))
        return dict(value) if value else None

    def _refresh_events(self) -> None:
        combined = []
        for engine in self.engines:
            combined.extend(getattr(engine, "events", []))
        self.events = sorted(combined)[-24:]

    def on_candle_all(self, symbol: str, timeframe: str, histories) -> list:
        ict_setup = self.ict.on_candle(symbol, timeframe, histories)
        rb_setup = self.rejection_block.on_candle(symbol, timeframe, histories)

        candidates = []
        if ict_setup:
            ict_setup.metadata.setdefault("strategy", "ICT_CONFLUENCE")
            ict_setup.metadata.setdefault("checklist_total", 6)
            ict_setup.metadata.setdefault("checklist_score", 6)
            candidates.append(ict_setup)
        if rb_setup:
            candidates.append(rb_setup)

        ready = candidates
        if len(candidates) > 1:
            rb = next(
                (
                    setup
                    for setup in candidates
                    if setup.metadata.get("strategy") == "REJECTION_BLOCK_10_10"
                ),
                candidates[0],
            )
            rb.metadata["also_detected_by"] = [
                setup.metadata.get("strategy", "UNKNOWN")
                for setup in candidates
                if setup is not rb
            ]
            rb.metadata["strategy_collision_deduped"] = True
            ready = [rb]

        if ready:
            self.last_setup = ready[-1]

        self._refresh_diagnostic(symbol, timeframe)
        self._refresh_events()
        return ready

    def on_candle(self, symbol: str, timeframe: str, histories):
        ready = self.on_candle_all(symbol, timeframe, histories)
        return ready[0] if ready else None
