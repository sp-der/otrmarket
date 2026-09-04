from pathlib import Path
import unittest


class OverviewDecisionChart80Tests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.static = self.root / "src" / "dashboard" / "static"

    def test_verify_card_is_replaced_by_gold_decision_chart_surface(self):
        css = (self.static / "overview-chart80.css").read_text(encoding="utf-8")
        script = (self.static / "overview-chart80.js").read_text(encoding="utf-8")
        server = (self.root / "src" / "dashboard" / "server_80.py").read_text(encoding="utf-8")

        self.assertIn(".prop-guard-panel{display:none!important}", css)
        self.assertIn("Gold Decision Chart", script)
        self.assertIn("NINJATRADER BRIDGE · OTR VIEW", script)
        self.assertIn("/market/api/chart?symbol=GC", script)
        self.assertIn("/market/api/otr8", script)
        self.assertIn("OTR 8.0 DECISION TAPE", script)
        self.assertIn("overview-chart80.css?v=8.0-live1", server)
        self.assertIn("overview-chart80.js?v=8.0-live1", server)

    def test_timeframe_selector_includes_context_only_4h_and_trade_levels(self):
        script = (self.static / "overview-chart80.js").read_text(encoding="utf-8")
        self.assertIn('<option value="1m">', script)
        self.assertIn('<option value="5m" selected>', script)
        self.assertIn('<option value="15m">', script)
        self.assertIn('<option value="1h">', script)
        self.assertIn('<option value="4h">', script)
        self.assertIn("ENTRY", script)
        self.assertIn("SL", script)
        self.assertIn("TP", script)

    def test_gold_chart_is_larger_and_user_resizable(self):
        css = (self.static / "overview-chart-resize80.css").read_text(encoding="utf-8")
        script = (self.static / "overview-chart-resize80.js").read_text(encoding="utf-8")
        server = (self.root / "src" / "dashboard" / "server_80.py").read_text(encoding="utf-8")

        self.assertIn("--otr8-chart-height:520px", css)
        self.assertIn(".otr8-chart-splitter", css)
        self.assertIn(".otr8-chart-height-handle", css)
        self.assertIn("otr8-chart-splitter", script)
        self.assertIn("otr8-chart-height-handle", script)
        self.assertIn("localStorage", script)
        self.assertIn("ResizeObserver", script)
        self.assertIn("overview-chart-resize80.css?v=8.0-live2", server)
        self.assertIn("overview-chart-resize80.js?v=8.0-live2", server)


if __name__ == "__main__":
    unittest.main()
