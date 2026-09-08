from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.integrations.nautilus_shadow.gold_replay import replay_latest_stored_gold
from src.storage.database import get_connection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay stored OTR Gold ticks through NautilusTrader shadow engine")
    parser.add_argument("--limit", type=int, default=5000, help="Maximum ticks from the latest Gold contract")
    parser.add_argument("--contract", default="", help="Optional exact NinjaTrader contract, e.g. 'MGC DEC26'")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    connection = get_connection()
    try:
        report = replay_latest_stored_gold(
            connection,
            limit=args.limit,
            contract=args.contract.strip() or None,
        )
    finally:
        connection.close()
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
