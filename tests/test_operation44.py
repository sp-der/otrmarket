import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.strategies.confluence import ConfluenceEngine, PendingContext
from src.strategies.models import Candle, Displacement, FairValueGap


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "dashboard" / "static"


def candles(count: int, symbol: str = "NQ", timeframe: str = "1m"):
    start = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
    result = []
    for i in range(count):
        open_time = start + timedelta(minutes=i)
        close_time = open_time + timedelta(minutes=1)
        result.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=open_time,
                close_time=close_time,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                ticks=10,
            )
        )
    return result


def fvg(symbol: str = "NQ", timeframe: str = "1m"):
    t = datetime(2026, 8, 13, 4, 7, tzinfo=timezone.utc)
    return FairValueGap(
        symbol=symbol,
        timeframe=timeframe,
        direction="bullish",
        lower=100.0,
        upper=101.0,
        formed_at=t,
        candle1_time=t - timedelta(minutes=2),
        candle3_time=t,
    )


def context(stage: str = "WAIT_ENTRY_FVG"):
    t = datetime(2026, 8, 13, 4, 0, tzinfo=timezone.utc)
    return PendingContext(
        symbol="NQ",
        timeframe="1m",
        direction="bullish",
        pd_array=fvg(),
        stage=stage,
        started_bar_count=1,
        stage_bar_count=6,
        trigger_type="smt",
        displacement=Displacement(
            symbol="NQ",
            timeframe="1m",
            direction="bullish",
            candle_time=t,
            low=95.0,
            high=105.0,
            body_ratio=2.0,
            range_ratio=1.8,
        ),
    )


class Operation44Tests(unittest.TestCase):
    def test_outside_zone_fvg_becomes_qualifying_fvg_wait(self):
        engine = ConfluenceEngine(entry_fvg_expiry_bars=8)
        ctx = context()
        engine.contexts[("NQ", "1m")] = ctx
        history = {("NQ", "1m"): candles(7)}

        with patch("src.strategies.confluence.detect_fvg", return_value=fvg()), patch(
            "src.strategies.confluence.fvg_in_retracement_zone", return_value=False
        ):
            engine.on_candle("NQ", "1m", history)

        self.assertEqual(ctx.stage, "WAIT_QUALIFYING_FVG")
        self.assertEqual(ctx.stage_bar_count, 6, "rejected FVG must not reset the displacement timer")
        self.assertTrue(ctx.entry_fvg_seen)
        self.assertFalse(ctx.retracement_seen)
        diag = engine.diagnostic("NQ", "1m")
        self.assertEqual(diag["stage"], "WAIT_QUALIFYING_FVG")
        self.assertTrue(diag["entry_fvg"])
        self.assertFalse(diag["retracement"])
        self.assertIn("outside the 50-79%", diag["note"])

    def test_progress_persists_while_scanning_for_another_fvg(self):
        engine = ConfluenceEngine(entry_fvg_expiry_bars=8)
        ctx = context("WAIT_QUALIFYING_FVG")
        ctx.entry_fvg_seen = True
        engine.contexts[("NQ", "1m")] = ctx
        history = {("NQ", "1m"): candles(8)}

        with patch("src.strategies.confluence.detect_fvg", return_value=None):
            engine.on_candle("NQ", "1m", history)

        diag = engine.diagnostic("NQ", "1m")
        self.assertEqual(diag["stage"], "WAIT_QUALIFYING_FVG")
        self.assertTrue(diag["entry_fvg"])
        self.assertFalse(diag["retracement"])
        self.assertIn("Scanning for another", diag["note"])

    def test_qualifying_fvg_with_bad_structure_moves_to_valid_rr_wait(self):
        engine = ConfluenceEngine(entry_fvg_expiry_bars=8)
        ctx = context("WAIT_QUALIFYING_FVG")
        ctx.entry_fvg_seen = True
        engine.contexts[("NQ", "1m")] = ctx
        history = {("NQ", "1m"): candles(8)}

        with patch("src.strategies.confluence.detect_fvg", return_value=fvg()), patch(
            "src.strategies.confluence.fvg_in_retracement_zone", return_value=True
        ), patch.object(engine, "_build_setup", return_value=None):
            engine.on_candle("NQ", "1m", history)

        self.assertEqual(ctx.stage, "WAIT_VALID_RR")
        self.assertTrue(ctx.entry_fvg_seen)
        self.assertTrue(ctx.retracement_seen)
        self.assertEqual(ctx.stage_bar_count, 6)
        diag = engine.diagnostic("NQ", "1m")
        self.assertTrue(diag["entry_fvg"])
        self.assertTrue(diag["retracement"])
        self.assertFalse(diag["rr"])
        self.assertIn("does not produce a valid trade", diag["note"])

    def test_candidate_stages_share_original_post_displacement_timer(self):
        engine = ConfluenceEngine(entry_fvg_expiry_bars=8)
        ctx = context("WAIT_QUALIFYING_FVG")
        ctx.entry_fvg_seen = True
        expired, elapsed, limit = engine._stage_expired(ctx, 15)
        self.assertTrue(expired)
        self.assertEqual(elapsed, 9)
        self.assertEqual(limit, 8)

    def test_scanner_labels_outside_zone_explicitly(self):
        js = (STATIC / "app.js").read_text()
        html = (STATIC / "index.html").read_text()
        self.assertIn('WAIT_QUALIFYING_FVG: "OUTSIDE ZONE"', js)
        self.assertIn('WAIT_VALID_RR: "WAIT R:R"', js)
        self.assertIn('WAIT_QUALIFYING_FVG: "Waiting for qualifying FVG"', js)
        self.assertRegex(html, r'styles\.css\?v=4\.(?:4|5)')
        self.assertRegex(html, r'app\.js\?v=4\.(?:4|5)')


if __name__ == "__main__":
    unittest.main()
