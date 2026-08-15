from pathlib import Path

root = Path(__file__).resolve().parents[1]
checks = {
    "ephemeral runtime pid": '"/tmp/otrmarket"' in (root / "src/dashboard/server.py").read_text(),
    "raw quote retention": "def prune_market_quotes" in (root / "src/storage/database.py").read_text(),
    "lifetime quote counters": "quote_counters" in (root / "src/storage/database.py").read_text(),
    "startup pruning": "Raw quote retention pruned" in (root / "src/main.py").read_text(),
}

for name, ok in checks.items():
    print(f"{'OK' if ok else 'FAIL'}  {name}")

if not all(checks.values()):
    raise SystemExit(1)

print("Operation 4.5.2 verification: OK")
