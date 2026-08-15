from src.storage.database import get_connection


def column_exists(connection, table, column):
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})"))


con = get_connection()
try:
    print("OTR Operation 4 verification")
    required = ["market_quotes", "candles", "strategy_setups", "paper_trades", "strategy_diagnostics", "engine_state"]
    for table in required:
        ok = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        print(f"{table}: {'OK' if ok else 'MISSING'}")
        if not ok:
            raise SystemExit(1)
    if not column_exists(con, "market_quotes", "ingested_at"):
        print("market_quotes.ingested_at: MISSING")
        raise SystemExit(1)
    print("market_quotes.ingested_at: OK")
    print("Replay clock schema: OK")
finally:
    con.close()
