from pathlib import Path
import sqlite3

DB = Path("data/otrmarket.db")
print("OTR Operation 3 verification")
print("Database:", "OK" if DB.exists() else "MISSING (created on first run)")

if DB.exists():
    con = sqlite3.connect(DB)
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in ("market_quotes", "candles", "strategy_setups", "paper_trades"):
            print(f"{table}:", "OK" if table in tables else "MISSING")
    finally:
        con.close()

bridge = Path("ninjatrader/OTRMarketBridge.cs")
print("NinjaTrader bridge source:", "OK" if bridge.exists() else "MISSING")
