import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.execution.paper import PaperExecutor
from src.main_multi import _a_plus_quality_gate
from src.strategies.models import Candle, Displacement, FairValueGap, StrategySetup


BASE = datetime(2026, 8, 16, 14, 0, tzinfo=timezone.utc)


def db():
    con = sqlite3.connect(":memory:")
    con.execute(
        """
        CREATE TABLE paper_trades (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT,
            status TEXT,
            closed_at TEXT,
            result TEXT
        )
        """
    )
    return con


def candles(symbol, timeframe, closes, minutes):
    results = []
    opened = closes[0]
    for index, close in enumerate(closes):
        open_time = BASE + timedelta(minutes=minutes * index)
        close_time = open_time + timedelta(minutes=minutes)
        high = max(opened, close) + 0.5
        low = min(opened, close) - 0.5
        results.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=close_time,
                open=opened,
                high=high,
                low=low,
                close=close,
            )
        )
        opened = close
    return results


def ict_setup(direction="bullish", created_at=None):
    created_at = created_at or (BASE + timedelta(minutes=35))
    displacement_time = created_at - timedelta(minutes=2)
    fvg_time = created_at - timedelta(minutes=1)
    if direction == "bullish":
        entry, stop, target = 105.0, 100.0, 115.0
        lower, upper = 104.0, 106.0
    else:
        entry, stop, target = 95.0, 100.0, 85.0
        lower, upper = 94.0, 96.0

    return StrategySetup(
        setup_id="quality",
        symbol="NQ",
        timeframe="1m",
        direction=direction,
        created_at=created_at,
        pd_array=FairValueGap(
            symbol="NQ",
            timeframe="1m",
            direction=direction,
            lower=lower,
            upper=upper,
            formed_at=created_at - timedelta(minutes=10),
            candle1_time=created_at - timedelta(minutes=12),
            candle3_time=created_at - timedelta(minutes=10),
        ),
        trigger_type="smt",
        trigger_details={},
        displacement=Displacement(
            symbol="NQ",
            timeframe="1m",
            direction=direction,
            candle_time=displacement_time,
            low=100.0,
            high=110.0,
            body_ratio=2.0,
            range_ratio=1.6,
        ),
        entry_fvg=FairValueGap(
            symbol="NQ",
            timeframe="1m",
            direction=direction,
            lower=lower,
            upper=upper,
            formed_at=fvg_time,
            candle1_time=fvg_time - timedelta(minutes=2),
            candle3_time=fvg_time,
        ),
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        risk_reward=2.0,
        metadata={
            "strategy": "ICT_CONFLUENCE",
            "entry_type": "FVG_MIDPOINT",
            "risk_multiplier": 0.8,
        },
    )


class Operation50ContextQualityTests(unittest.TestCase):
    def setUp(self):
        from src import main_multi

        main_multi.runtime.paper.positions.clear()

    def test_aligned_five_minute_context_passes(self):
        setup = ict_setup("bullish")
        htf = candles("NQ", "5m", [100, 101, 102, 103, 104, 106, 108], 5)
        histories = {("NQ", "5m"): htf}
        allowed, reason = _a_plus_quality_gate(db(), setup, histories)
        self.assertTrue(allowed, reason)
        self.assertEqual(setup.metadata["a_plus_context"]["higher_timeframe_bias"], "bullish")

    def test_countertrend_five_minute_context_blocks(self):
        setup = ict_setup("bullish")
        htf = candles("NQ", "5m", [110, 109, 108, 107, 106, 104, 102], 5)
        histories = {("NQ", "5m"): htf}
        allowed, reason = _a_plus_quality_gate(db(), setup, histories)
        self.assertFalse(allowed)
        self.assertIn("bearish", reason)

    def test_borderline_displacement_is_research_only(self):
        setup = ict_setup("bullish")
        setup.displacement = SimpleNamespace(
            direction="bullish",
            candle_time=setup.created_at - timedelta(minutes=2),
            body_ratio=1.55,
            range_ratio=1.31,
        )
        htf = candles("NQ", "5m", [100, 101, 102, 103, 104, 106, 108], 5)
        allowed, reason = _a_plus_quality_gate(db(), setup, {("NQ", "5m"): htf})
        self.assertFalse(allowed)
        self.assertIn("not A+ strength", reason)


class Operation50PendingLifecycleTests(unittest.TestCase):
    def _setup(self):
        setup = ict_setup("bullish", created_at=BASE)
        setup.setup_id = "pending"
        setup.entry_price = 100.0
        setup.stop_price = 95.0
        setup.target_price = 110.0
        setup.risk_reward = 2.0
        return setup

    def test_pending_entry_expires_in_replay_market_time(self):
        executor = PaperExecutor()
        setup = self._setup()
        executor.register_setup(setup, risk_dollars=200)
        changed = executor.on_price(
            "NQ",
            103.0,
            BASE + timedelta(minutes=7),
        )
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].result, "EXPIRED_BEFORE_ENTRY")
        self.assertEqual(executor.pending_count, 0)

    def test_move_that_runs_seventy_five_percent_to_target_is_stale(self):
        executor = PaperExecutor()
        setup = self._setup()
        executor.register_setup(setup, risk_dollars=200)
        changed = executor.on_price(
            "NQ",
            107.5,
            BASE + timedelta(minutes=2),
        )
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].result, "STALE_MOVE_BEFORE_ENTRY")
        self.assertEqual(executor.pending_count, 0)

    def test_clean_retrace_can_still_fill(self):
        executor = PaperExecutor()
        setup = self._setup()
        executor.register_setup(setup, risk_dollars=200)
        changed = executor.on_price(
            "NQ",
            100.0,
            BASE + timedelta(minutes=2),
        )
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].status, "OPEN")


if __name__ == "__main__":
    unittest.main()
