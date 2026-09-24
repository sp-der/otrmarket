from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.otr8.candidates import CandidateCollector80


class _Engine:
    def __init__(self):
        self.ict = SimpleNamespace(on_candle=lambda *args: self._setup("ict", "ICT_CONFLUENCE"))
        self.rejection_block = SimpleNamespace(on_candle=lambda *args: self._setup("rb", "REJECTION_BLOCK_10_10"))
        self.reversal = SimpleNamespace(on_candle=lambda *args: None)
        self.diagnostics = {}
        self.last_setup = None

    @staticmethod
    def _setup(setup_id, strategy):
        price = {
            "ict": 3500.0,
            "rb": 3501.0,
            "cont": 3502.0,
            "mom": 3503.0,
            "early": 3504.0,
        }.get(setup_id, 3599.0)
        return SimpleNamespace(
            setup_id=setup_id,
            symbol="GC",
            timeframe="5m",
            direction="bullish",
            entry_price=price,
            stop_price=price - 2.0,
            target_price=price + 4.0,
            metadata={"strategy": strategy},
        )

    def _refresh_diagnostic(self, *args):
        pass

    def _refresh_events(self):
        pass


class CandidateCollector80Tests(unittest.TestCase):
    def test_preserves_simultaneous_strategy_candidates_for_arbiter(self):
        engine = _Engine()
        candidates = CandidateCollector80(engine).collect("GC", "5m", {}, "VERIFY")
        self.assertEqual([item.setup_id for item in candidates], ["ict", "rb"])
        self.assertEqual(engine.last_setup.setup_id, "rb")


    def test_operation81_opt_in_collects_all_mature_families_before_arbiter(self):
        engine = _Engine()
        calls = {"continuation": 0, "momentum": 0, "early": 0}

        def continuation(*_args):
            calls["continuation"] += 1
            return engine._setup("cont", "TREND_CONTINUATION_REARM")

        def momentum(*_args):
            calls["momentum"] += 1
            return engine._setup("mom", "GOLD_MOMENTUM_PULLBACK_72R")

        collector = CandidateCollector80(
            engine,
            continuation=SimpleNamespace(on_candle=continuation),
        )
        collector.collect_all_families = True
        collector._momentum = lambda: SimpleNamespace(
            on_candle=momentum,
            diagnostic=lambda *_args: {},
        )

        def early(*_args):
            calls["early"] += 1
            return engine._setup("early", "ICT_CONFLUENCE")

        collector._early_arm_candidate = early

        candidates = collector.collect("GC", "5m", {}, "VERIFY")

        self.assertEqual(
            [item.setup_id for item in candidates],
            ["ict", "rb", "cont", "mom", "early"],
        )
        self.assertEqual(calls, {"continuation": 1, "momentum": 1, "early": 1})

    def test_operation80_default_keeps_fallback_families_dormant_when_primary_exists(self):
        engine = _Engine()
        calls = {"continuation": 0}

        def continuation(*_args):
            calls["continuation"] += 1
            return engine._setup("cont", "TREND_CONTINUATION_REARM")

        collector = CandidateCollector80(
            engine,
            continuation=SimpleNamespace(on_candle=continuation),
        )

        candidates = collector.collect("GC", "5m", {}, "VERIFY")

        self.assertEqual([item.setup_id for item in candidates], ["ict", "rb"])
        self.assertEqual(calls["continuation"], 0)

if __name__ == "__main__":
    unittest.main()
