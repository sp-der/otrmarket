from __future__ import annotations

from src.strategies.gold_momentum72r import GoldMomentumPullbackEngine72R


VERIFY_MODES = {"VERIFY", "VERIFICATION", "TEST"}


class CandidateCollector80:
    """Collect raw strategy candidates without legacy collision deduplication."""

    def __init__(self, engine, *, continuation=None) -> None:
        self.engine = engine
        self.continuation = continuation

    def _momentum(self) -> GoldMomentumPullbackEngine72R:
        momentum = getattr(self.engine, "_gold_momentum_pullback_72r", None)
        if momentum is None:
            momentum = GoldMomentumPullbackEngine72R()
            setattr(self.engine, "_gold_momentum_pullback_72r", momentum)
        return momentum

    @staticmethod
    def _annotate(setup, strategy: str, *, source: str = "CANDLE_CLOSE"):
        if setup is None:
            return None
        setup.metadata.setdefault("strategy", strategy)
        setup.metadata.setdefault("candidate_source_80", source)
        if strategy == "ICT_CONFLUENCE":
            setup.metadata.setdefault("checklist_total", 6)
            setup.metadata.setdefault("checklist_score", 6)
        return setup

    def collect(self, symbol: str, timeframe: str, histories, mode: str) -> list:
        candidates = []

        ict = self._annotate(
            self.engine.ict.on_candle(symbol, timeframe, histories),
            "ICT_CONFLUENCE",
        )
        if ict is not None:
            candidates.append(ict)

        rejection = self._annotate(
            self.engine.rejection_block.on_candle(symbol, timeframe, histories),
            "REJECTION_BLOCK_10_10",
        )
        if rejection is not None:
            candidates.append(rejection)

        reversal_engine = getattr(self.engine, "reversal", None)
        if reversal_engine is not None:
            reversal = self._annotate(
                reversal_engine.on_candle(symbol, timeframe, histories),
                "MSS_REVERSAL",
            )
            if reversal is not None:
                candidates.append(reversal)

        # Preserve the established stale-thesis continuation before trying the
        # newer momentum-recognition fallback.
        if not candidates and self.continuation is not None:
            continuation = self._annotate(
                self.continuation.on_candle(symbol, timeframe, histories),
                "TREND_CONTINUATION_REARM",
            )
            if continuation is not None:
                candidates.append(continuation)

        normalized_mode = str(mode or "").strip().upper()
        if (
            not candidates
            and normalized_mode in VERIFY_MODES
            and symbol == "GC"
            and timeframe in {"5m", "15m"}
        ):
            momentum = self._momentum()
            setup = self._annotate(
                momentum.on_candle(symbol, timeframe, histories, normalized_mode),
                "GOLD_MOMENTUM_PULLBACK_72R",
            )
            diagnostic = momentum.diagnostic(symbol, timeframe)
            if diagnostic and diagnostic.get("stage") in {"WAIT_ENTRY_FVG", "WAIT_PULLBACK", "SETUP_READY"}:
                self.engine.diagnostics[(symbol, timeframe)] = diagnostic
            if setup is not None:
                candidates.append(setup)

        try:
            self.engine._refresh_diagnostic(symbol, timeframe)
            self.engine._refresh_events()
        except Exception:
            pass
        if candidates:
            self.engine.last_setup = candidates[-1]
        return candidates
