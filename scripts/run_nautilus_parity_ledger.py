from __future__ import annotations

import argparse
import json
import sys
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
        report = run_recent_gold_parity(connection, limit=args.limit)
    finally:
        connection.close()

    summary = {
        "requested": report.requested,
        "records": len(report.records),
        "trade_path_matches": report.trade_path_matches,
        "full_matches": report.full_matches,
        "mismatches": [
            {
                "setup_id": item.setup_id,
                "categories": list(item.difference_categories),
                "details": list(item.difference_details),
                "note": item.note,
            }
            for item in report.records
            if not item.matched_full
        ],
        "skipped": [
            {"setup_id": item.setup_id, "error": item.error}
            for item in report.errors
        ],
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
