#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.historical.coverage import coverage_rows, format_coverage
from src.research.historical.importer import import_retained_market_quotes
from src.research.historical.store import HistoricalStore


def main() -> None:
    parser = argparse.ArgumentParser(description="OTR research-only historical futures store")
    parser.add_argument("--database", default="data/otr_historical.db")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init")
    importer = commands.add_parser("import-retained")
    importer.add_argument("--production-db", default="data/otrmarket.db")
    importer.add_argument("--capture-id", default=None)
    report = commands.add_parser("coverage")
    report.add_argument("--capture-id", default=None)
    args = parser.parse_args()
    store = HistoricalStore(Path(args.database))
    if args.command == "init":
        store.initialize()
        print(f"Initialized research historical store: {store.path}")
    elif args.command == "import-retained":
        capture_id = args.capture_id or datetime.now(timezone.utc).strftime("retained-%Y%m%dT%H%M%SZ")
        print(import_retained_market_quotes(args.production_db, store.path, capture_id))
    else:
        with store.connect() as connection:
            print(format_coverage(coverage_rows(connection, args.capture_id)))


if __name__ == "__main__":
    main()
