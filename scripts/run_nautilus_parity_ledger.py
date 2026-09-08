from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.nautilus_shadow.ledger import run_recent_gold_parity
from src.storage.database import get_connection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run recent closed OTR Gold paper trades through the Nautilus shadow bracket engine"
    )
    parser.add_argument("--limit", type=int, default=10, help="Maximum recent closed GC trades to compare")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    connection = get_connection()
    try:
        records = run_recent_gold_parity(connection, limit=args.limit)
    finally:
        connection.close()

    summary = {
        "records": len(records),
        "trade_path_matches": sum(1 for item in records if item.matched_trade_path),
        "full_matches": sum(1 for item in records if item.matched_full),
        "mismatches": [
            {
                "setup_id": item.setup_id,
                "categories": list(item.difference_categories),
                "details": list(item.difference_details),
                "note": item.note,
            }
            for item in records
            if not item.matched_full
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
