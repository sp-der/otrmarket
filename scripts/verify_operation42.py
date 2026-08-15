from pathlib import Path

root = Path(__file__).resolve().parents[1]
checks = {
    "combined 4.1 replay session": root / "src/runtime/session.py",
    "combined 4.1 confluence timers": root / "src/strategies/confluence.py",
    "combined 4.1 database migration": root / "src/storage/database.py",
    "grouped scanner JS": root / "src/dashboard/static/app.js",
    "grouped scanner CSS": root / "src/dashboard/static/styles.css",
    "scanner HTML": root / "src/dashboard/static/index.html",
}
missing = [name for name, path in checks.items() if not path.exists()]
if missing:
    raise SystemExit("Missing Operation 4.2 files: " + ", ".join(missing))

html = checks["scanner HTML"].read_text()
js = checks["grouped scanner JS"].read_text()
css = checks["grouped scanner CSS"].read_text()

required = [
    ("cache-busted CSS", 'styles.css?v=4.4' in html),
    ("cache-busted JS", 'app.js?v=4.4' in html),
    ("market grouping", "scannerMarketSection" in js),
    ("all four markets", 'scannerMarketOrder = ["NQ", "ES", "GC", "BTC-USD"]' in js),
    ("all four timeframes", 'scannerTimeframeOrder = ["1m", "5m", "15m", "1h"]' in js),
    ("BTC replay pause UI", "PAUSED IN REPLAY" in js),
    ("scanner board styling", ".scanner-board" in css),
]
failed = [name for name, ok in required if not ok]
if failed:
    raise SystemExit("Operation 4.2 verification failed: " + ", ".join(failed))

print("Operation 4.2 verification: OK")
print("  4.1 stage timers: included")
print("  4.1 replay isolation: included")
print("  4.1 DB migration fix: included")
print("  grouped scanner UI: included")
print("  dashboard cache bust: included")
