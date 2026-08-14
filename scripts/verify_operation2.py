from pathlib import Path

from src.dashboard.queries import DashboardRepository


repo = DashboardRepository(Path("data/otrmarket.db"))
snapshot = repo.snapshot()

print("OTR Operation 2 dashboard verification")
print(f"Database: {'OK' if snapshot['database']['ok'] else 'MISSING'}")
print(f"Markets available: {len(snapshot['markets'])}")
print(f"Paper trades: {len(snapshot['trades'])}")
print(f"Strategy setups: {len(snapshot['setups'])}")
print(f"Candle groups: {len(snapshot['candles'])}")
print(f"Equity points: {len(snapshot['equity_curve'])}")

if not snapshot["database"]["ok"]:
    raise SystemExit(1)
