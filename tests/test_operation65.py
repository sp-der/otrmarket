from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Operation65RegressionTests(unittest.TestCase):
    def test_intrabar_runtime_contracts_in_isolated_process(self):
        code = textwrap.dedent(
            r'''
            from datetime import datetime, timedelta, timezone

            import src.main_65 as op65
            from src.execution import paper as paper_module

            assert op65.INTRABAR_TIMEFRAMES == {"1m", "5m"}
            assert op65.INTRABAR_MIN_CONFIRMATIONS == 3
            assert op65.INTRABAR_MIN_STABILITY_SECONDS == 0.75
            assert op65.runtime.process_price is op65._process_price_65
            assert paper_module._MAX_PREENTRY_TARGET_PROGRESS == 0.75

            base = datetime(2026, 8, 19, 6, 30, tzinfo=timezone.utc)
            key = ("NQ", "1m")
            original = op65.runtime.candles.current.get(key)
            op65.runtime.candles.current[key] = {
                "open_time": base,
                "open": 25000.0,
                "high": 25020.0,
                "low": 24995.0,
                "close": 25015.0,
                "ticks": 42,
            }
            candle = op65._synthetic_candle("NQ", "1m", base + timedelta(seconds=10))
            assert candle is not None
            assert candle.open_time == base
            assert candle.close_time == base + timedelta(seconds=10)
            assert candle.close == 25015.0
            assert candle.ticks == 42

            if original is None:
                op65.runtime.candles.current.pop(key, None)
            else:
                op65.runtime.candles.current[key] = original

            op65._intrabar_candidates.clear()
            fingerprint = ("NQ", "1m", "bullish", "bucket", "smt", 1, 2, 3)
            ready1, count1, age1 = op65._stability_ready(key, fingerprint, base)
            ready2, count2, age2 = op65._stability_ready(key, fingerprint, base + timedelta(seconds=0.4))
            ready3, count3, age3 = op65._stability_ready(key, fingerprint, base + timedelta(seconds=0.8))
            assert not ready1 and count1 == 1 and age1 == 0.0
            assert not ready2 and count2 == 2
            assert ready3 and count3 == 3 and age3 >= 0.75

            # Operation 7.2H installs an instance wrapper and a planner whose
            # Railway/Rich logger owns a non-pickleable RLock. The 6.5 probe
            # must clone durable ICT state without copying those attachments.
            import src.main_72 as op72

            live_ict = op72.runtime.strategy.ict
            planner = getattr(live_ict, "_early_entry_planner_72h", None)
            assert planner is not None
            probe = op65._clone_intrabar_ict_65(live_ict)
            assert probe is not live_ict
            assert type(probe) is type(live_ict)
            assert "_early_entry_planner_72h" not in vars(probe)
            assert "on_candle" not in vars(probe)

            planner_probe = op65._clone_early_entry_planner_65(planner)
            assert planner_probe is not planner
            assert planner_probe.logger is None
            assert planner_probe.arms == planner.arms
            assert planner_probe.arms is not planner.arms

            print("operation65-intrabar-ok")
            '''
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        self.assertIn("operation65-intrabar-ok", completed.stdout)

    def test_dashboard_history_is_realized_only_after_all_ui_layers(self):
        cleanup = (ROOT / "src/dashboard/static/trade-history-cleanup.js").read_text()
        server = (ROOT / "src/dashboard/server.py").read_text()
        trading_days = (ROOT / "src/dashboard/static/trading-days.js").read_text()

        self.assertIn('new Set(["WIN", "LOSS"])', cleanup)
        self.assertIn("isRealizedTrade65", cleanup)
        self.assertIn("Missed / Rejected Attempts", cleanup)
        self.assertIn("MAX_AUDIT_ROWS_65 = 30", cleanup)
        self.assertIn("realizedOnlyRenderer65", cleanup)
        self.assertIn('window.addEventListener("load"', cleanup)
        self.assertIn("window.setTimeout(finalizeDashboard65, 0)", cleanup)
        self.assertNotIn('window.addEventListener("DOMContentLoaded", installRealizedTradeHistory65', cleanup)
        # The only MutationObserver is the terminology sanitizer added later to
        # keep dynamic dashboard copy free of legacy "paper" wording. It must
        # never reinstall or own the trade renderer; the renderer still locks
        # once after all deferred UI layers have loaded.
        self.assertIn("installTradingCopySanitizer65", cleanup)
        self.assertIn("const observer = new MutationObserver(", cleanup)
        self.assertIn("sanitizeTradingCopy65(document.body)", cleanup)
        self.assertNotIn("MutationObserver(() => {\n    installRealizedTradeHistory65", cleanup)
        self.assertIn("nonExecutedTradesBody65", cleanup)
        self.assertIn("finalDashboardRenderTrades65(realizedTrades)", cleanup)
        self.assertIn("renderTrades = function timedRenderTrades", trading_days)
        self.assertIn('"src.main_65"', server)
        self.assertIn('"src.main_64"', server)

    def test_runtime_build_manifest_exposes_verified_rules(self):
        cleanup = (ROOT / "src/dashboard/static/trade-history-cleanup.js").read_text()
        server = (ROOT / "src/dashboard/server.py").read_text()
        evaluation = (ROOT / "src/risk/evaluation.py").read_text()
        confluence = (ROOT / "src/strategies/confluence.py").read_text()
        op59 = (ROOT / "src/main_59.py").read_text()
        op61 = (ROOT / "src/main_61.py").read_text()
        op64 = (ROOT / "src/main_64.py").read_text()
        op65 = (ROOT / "src/main_65.py").read_text()

        self.assertIn("runtime-build.json", cleanup)
        self.assertIn("Active Build / Trading Rules", cleanup)
        self.assertIn("_write_runtime_manifest", server)
        self.assertIn("RAILWAY_GIT_COMMIT_SHA", server)
        self.assertIn('os.environ["OTR_ACTIVE_ENGINE_MODULE"] = engine_module', server)

        self.assertIn("risk_per_trade: float = 250.0", evaluation)
        self.assertIn("internal_daily_stop: float = 750.0", evaluation)
        self.assertIn("entry_fvg_expiry_bars: int = 15", confluence)
        self.assertIn("if b_plus_count >= 2", op59)
        self.assertIn("rr >= 1.50", op61)
        self.assertIn("score < 80", op64)
        self.assertIn("rr < 1.75", op64)
        self.assertIn('INTRABAR_TIMEFRAMES = {"1m", "5m"}', op65)
        self.assertIn("INTRABAR_EVAL_INTERVAL_SECONDS = 0.25", op65)
        self.assertIn("INTRABAR_MIN_STABILITY_SECONDS = 0.75", op65)
        self.assertIn("INTRABAR_MIN_CONFIRMATIONS = 3", op65)

    def test_operation65_preserves_stable_state_and_no_chase(self):
        text = (ROOT / "src/main_65.py").read_text()
        self.assertIn("_clone_intrabar_ict_65", text)
        self.assertIn("_clone_early_entry_planner_65", text)
        self.assertIn("type(probe).on_candle", text)
        self.assertIn("durable_state_untouched", text)
        self.assertIn("no_chase_preserved", text)
        self.assertIn("runtime.paper.register_setup", text)
        self.assertIn("runtime.process_price = _process_price_65", text)


if __name__ == "__main__":
    unittest.main()
