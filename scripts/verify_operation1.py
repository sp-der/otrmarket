import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage.database import get_connection


def main():
    con = get_connection()
    con.close()
    db = Path("data/otrmarket.db")
    print("OTR Operation 1 verification")
    print("Database:", "OK" if db.exists() else "MISSING")
    con = sqlite3.connect(db)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {"market_quotes", "candles", "strategy_setups", "paper_trades"}
    for table in sorted(required):
        print(f"{table}:", "OK" if table in tables else "MISSING")
    con.close()


if __name__ == "__main__":
    main()
