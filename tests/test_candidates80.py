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
        return SimpleNamespace(
            setup_id=setup_id,
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


if __name__ == "__main__":
    unittest.main()
