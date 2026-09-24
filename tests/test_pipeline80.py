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


    def test_operation81_promotes_runner_up_after_top_candidate_geometry_preflight_fails(self):
        class _FailTopPaper(_Paper):
            def __init__(self):
                super().__init__()
                self.attempts = []

            def register_setup(self, setup, *, risk_dollars=None, guard_reason=None):
                self.attempts.append(setup.setup_id)
                if setup.setup_id == "second":
                    raise ValueError("invalid trade geometry")
                return super().register_setup(
                    setup,
                    risk_dollars=risk_dollars,
                    guard_reason=guard_reason,
                )

        paper = _FailTopPaper()
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
        pipeline = OTRPipeline80(
            runtime=runtime,
            session_gate=lambda connection, setup: SimpleNamespace(
                allowed=True, reason="open", details={}
            ),
            quality_gate=lambda connection, setup, histories: (True, "quality passed"),
            setup_risk=lambda decision, setup: (250.0, 1.0),
            arbiter=_Arbiter(),
            regime_engine=_Regime(),
        )
        pipeline.promote_runner_up = True
        first, second = self._setup("first"), self._setup("second")
        connection = sqlite3.connect(":memory:")
        try:
            handled = pipeline.process_candidates(connection, [first, second], {})
        finally:
            connection.close()

        self.assertEqual(paper.attempts, ["second", "first"])
        self.assertEqual(paper.registered, ["first"])
        self.assertEqual(second.status, "RISK_REJECTED")
        self.assertEqual(first.metadata["trade_plan_80"]["metadata"]["promoted_rank"], 2)
        self.assertEqual([item.setup_id for item in handled], ["second", "first"])

    def test_operation81_promotes_runner_up_when_top_candidate_cannot_size_whole_mgc(self):
        class _CannotSizeTopPaper(_Paper):
            def __init__(self):
                super().__init__()
                self.attempts = []

            def register_setup(self, setup, *, risk_dollars=None, guard_reason=None):
                self.attempts.append(setup.setup_id)
                if setup.setup_id == "second":
                    return SimpleNamespace(
                        setup=setup,
                        status="INVALIDATED",
                        result="CANNOT_SIZE_MGC",
                    )
                return super().register_setup(
                    setup,
                    risk_dollars=risk_dollars,
                    guard_reason=guard_reason,
                )

        paper = _CannotSizeTopPaper()
        persisted = []
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
            save_setup=lambda connection, setup: None,
            upsert_paper_trade=lambda connection, position, updated_at: persisted.append(
                (position.setup.setup_id, position.status, position.result)
            ),
            console=_Console(),
        )
        pipeline = OTRPipeline80(
            runtime=runtime,
            session_gate=lambda connection, setup: SimpleNamespace(
                allowed=True, reason="open", details={}
            ),
            quality_gate=lambda connection, setup, histories: (True, "quality passed"),
            setup_risk=lambda decision, setup: (250.0, 1.0),
            arbiter=_Arbiter(),
            regime_engine=_Regime(),
        )
        pipeline.promote_runner_up = True
        first, second = self._setup("first"), self._setup("second")
        connection = sqlite3.connect(":memory:")
        try:
            pipeline.process_candidates(connection, [first, second], {})
        finally:
            connection.close()

        self.assertEqual(paper.attempts, ["second", "first"])
        self.assertEqual(paper.registered, ["first"])
        self.assertEqual(second.status, "RISK_REJECTED")
        self.assertEqual(persisted[0], ("second", "INVALIDATED", "CANNOT_SIZE_MGC"))

    def test_operation81_does_not_promote_around_account_guard_block(self):
        paper = _Paper()
        calls = {"guard": 0}

        def guard(_connection, _created):
            calls["guard"] += 1
            return SimpleNamespace(
                allowed=False,
                status="BLOCKED",
                risk_dollars=0.0,
                reason="daily account guard",
                snapshot={"profile": "VERIFY", "phase": "LOCKED"},
            )

        runtime = SimpleNamespace(
            strategy=SimpleNamespace(),
            paper=paper,
            evaluation_guard=SimpleNamespace(decide=guard),
            save_setup=lambda connection, setup: None,
            upsert_paper_trade=lambda connection, position, updated_at: None,
            console=_Console(),
        )
        pipeline = OTRPipeline80(
            runtime=runtime,
            session_gate=lambda connection, setup: SimpleNamespace(
                allowed=True, reason="open", details={}
            ),
            quality_gate=lambda connection, setup, histories: (True, "quality passed"),
            setup_risk=lambda decision, setup: (0.0, 0.0),
            arbiter=_Arbiter(),
            regime_engine=_Regime(),
        )
        pipeline.promote_runner_up = True
        first, second = self._setup("first"), self._setup("second")
        connection = sqlite3.connect(":memory:")
        try:
            pipeline.process_candidates(connection, [first, second], {})
        finally:
            connection.close()

        self.assertEqual(calls["guard"], 1)
        self.assertEqual(paper.registered, [])
        self.assertEqual(second.status, "GUARD_BLOCKED")
        self.assertEqual(first.status, "ARBITER_BLOCKED")

if __name__ == "__main__":
    unittest.main()
