import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "dashboard" / "static"


class Operation42ScannerUITests(unittest.TestCase):
    def test_scanner_uses_grouped_market_board(self):
        html = (STATIC / "index.html").read_text()
        js = (STATIC / "app.js").read_text()
        self.assertIn('id="scannerCards" class="scanner-board"', html)
        self.assertIn('scannerMarketOrder = ["NQ", "ES", "GC", "BTC-USD"]', js)
        self.assertIn('scannerTimeframeOrder = ["1m", "5m", "15m", "1h"]', js)
        self.assertIn("scannerMarketSection", js)
        self.assertIn("scannerTimeframeCard", js)

    def test_replay_isolation_is_visible_in_scanner(self):
        js = (STATIC / "app.js").read_text()
        self.assertIn('runtime?.mode === "REPLAY" && symbol === "BTC-USD"', js)
        self.assertIn("PAUSED IN REPLAY", js)
        self.assertIn("Live BTC feed isolated from replay strategy", js)

    def test_scanner_assets_are_cache_busted(self):
        html = (STATIC / "index.html").read_text()
        self.assertRegex(html, r'/market/assets/styles\.css\?v=4\.(?:4|5)')
        self.assertRegex(html, r'/market/assets/app\.js\?v=4\.(?:4|5)')

    def test_grouped_scanner_styles_exist(self):
        css = (STATIC / "styles.css").read_text()
        for selector in [
            ".scanner-board",
            ".scanner-market-section",
            ".scanner-timeframe-grid",
            ".scanner-timeframe-card",
            ".scan-rail",
        ]:
            self.assertIn(selector, css)


if __name__ == "__main__":
    unittest.main()
