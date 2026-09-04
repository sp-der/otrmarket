from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from src.otr8.models import CandidateAssessment80, RegimeSnapshot80
from src.otr8.pipeline import OTRPipeline80


class _Console:
    def log(self, message):
        pass


class _Paper:
    def __init__(self):
        self.registered = []

    def register_setup(self, setup, *, risk_dollars=None, guard_reason=None):
        self.registered.append(setup.setup_id)
        return SimpleNamespace(setup=setup, status="PENDING", result=None)


class _Arbiter:
    def choose(self, candidates, histories, regimes):
        chosen = candidates[1]
        assessments = []
        for idx, setup in enumerate(candidates):
            score = 80.0 + idx * 10.0
            setup.metadata["setup_arbiter_80"] = {
                "selected": setup is chosen,
                "score": score,
            }
            if setup is not chosen:
                setup.metadata["setup_arbiter_80"].update(
                    winner_setup_id=chosen.setup_id,
                    winner_score=90.0,
                    reason="Higher-quality candidate owns the GC slot.",
                )
            assessments.append(
                CandidateAssessment80(
                    setup_id=setup.setup_id,
                    strategy=setup.metadata["strategy"],
                    timeframe=setup.timeframe,
                    direction=setup.direction,
                    score=score,
                    risk_reward=setup.risk_reward,
                    narrative_score=40,
                    higher_timeframe_score=10,
                    regime_score=10,
                    quality_score=10,
                    strategy_score=5,
                    details={"quality_grade": "A"},
                )
            )
        return chosen, assessments


class _Regime:
    def classify(self, histories, symbol, timeframe, market_time=None):
        return RegimeSnapshot80(
            symbol=symbol,
            timeframe=timeframe,
            regime="TREND_EXPANSION",
            direction="bullish",
            confidence=86,
            directional_efficiency=0.7,
            range_expansion=1.4,
            overlap_ratio=0.2,
            alternation_ratio=0.2,
            higher_timeframe_direction="bullish",
            legacy_regime="TRENDING_UP",
        )


class Pipeline80Tests(unittest.TestCase):
    def _setup(self, setup_id):
        return SimpleNamespace(
            setup_id=setup_id,
            symbol="GC",
            timeframe="5m",
            direction="bullish",
            created_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
            entry_price=3500.0,
            stop_price=3498.0,
            target_price=3504.0,
            risk_reward=2.0,
            trigger_type="liquidity_sweep",
            metadata={"strategy": "ICT_CONFLUENCE", "a_plus_context": {"quality_grade": "A"}},
            status="PENDING",
        )

    def test_only_arbiter_winner_reaches_executor(self):
        paper = _Paper()
        saved = []
        runtime = SimpleNamespace(
            strategy=SimpleNamespace(),
            paper=paper,
            evaluation_guard=SimpleNamespace(
                decide=lambda connection, created: SimpleNamespace(
                    allowed=True,
                    status="VERIFY",
                    risk_dollars=250.0,
                    reason="approved",
                    snapshot={"profile": "VERIFY", "phase": "VERIFY"},
                )
            ),
            save_setup=lambda connection, setup: saved.append((setup.setup_id, setup.status)),
            upsert_paper_trade=lambda connection, position, updated_at: None,
            console=_Console(),
        )
        session = lambda connection, setup: SimpleNamespace(allowed=True, reason="open", details={})
        quality = lambda connection, setup, histories: (True, "quality passed")
        pipeline = OTRPipeline80(
            runtime=runtime,
            session_gate=session,
            quality_gate=quality,
            setup_risk=lambda decision, setup: (250.0, 1.0),
            arbiter=_Arbiter(),
            regime_engine=_Regime(),
        )
        first, second = self._setup("first"), self._setup("second")
        connection = sqlite3.connect(":memory:")
        try:
            handled = pipeline.process_candidates(connection, [first, second], {}, source="CANDLE_CLOSE")
        finally:
            connection.close()
        self.assertEqual(paper.registered, ["second"])
        self.assertEqual(first.status, "ARBITER_BLOCKED")
        self.assertIn("trade_plan_80", second.metadata)
        self.assertEqual(second.metadata["trade_plan_80"]["risk_dollars"], 250.0)
        self.assertEqual(len(handled), 2)


if __name__ == "__main__":
    unittest.main()
