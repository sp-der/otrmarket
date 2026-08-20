#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.historical.coverage import coverage_rows, format_coverage
from src.research.historical.importer import import_retained_market_quotes
from src.research.historical.acquisition import (
    ImportMetadata, export_manifest, import_ninjatrader, import_ninjatrader_batch,
    list_captures, verify_capture,
)
from src.research.historical.store import HistoricalStore
from src.research.historical.databento import import_databento_package, verify_package


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
    nt = commands.add_parser("import-ninjatrader", help="Import immutable real 1m futures OHLCV")
    nt.add_argument("--file", required=True, help="CSV/text file or directory")
    nt.add_argument("--symbol", required=True, choices=("NQ","MNQ","ES","MES","GC","MGC"))
    nt.add_argument("--contract", required=True, help="Exact contract, e.g. NQ SEP26")
    nt.add_argument("--timezone", required=True, dest="source_timezone")
    nt.add_argument("--interval", required=True, type=int, dest="interval_minutes")
    nt.add_argument("--source", required=True)
    nt.add_argument("--capture-date", required=True)
    nt.add_argument("--capture-id")
    nt.add_argument("--timestamp-format", choices=("auto","ninjatrader","us","iso"), default="auto")
    nt.add_argument("--delimiter", choices=("auto","comma","tab","semicolon"), default="auto")
    nt.add_argument("--dry-run", action="store_true")
    batch = commands.add_parser("import-ninjatrader-batch", help="Import a manifest-declared directory/capture")
    batch.add_argument("--manifest", required=True, help="JSON manifest containing capture_id and files")
    batch.add_argument("--dry-run", action="store_true")
    dbn = commands.add_parser("import-databento", help="Import an immutable Databento DBN batch ZIP")
    dbn.add_argument("--package", required=True)
    dbn.add_argument("--capture-id", required=True)
    dbn.add_argument("--dry-run", action="store_true")
    verify_dbn = commands.add_parser("verify-databento-package")
    verify_dbn.add_argument("--package", required=True)
    listing = commands.add_parser("list-captures")
    manifest = commands.add_parser("export-manifest")
    manifest.add_argument("--capture-id", required=True)
    verify = commands.add_parser("verify-capture")
    verify.add_argument("--capture-id", required=True)
    args = parser.parse_args()
    store = HistoricalStore(Path(args.database))
    if args.command == "init":
        store.initialize()
        print(f"Initialized research historical store: {store.path}")
    elif args.command == "import-retained":
        capture_id = args.capture_id or datetime.now(timezone.utc).strftime("retained-%Y%m%dT%H%M%SZ")
        print(import_retained_market_quotes(args.production_db, store.path, capture_id))
    elif args.command == "coverage":
        with store.connect() as connection:
            print(format_coverage(coverage_rows(connection, args.capture_id)))
    elif args.command == "import-ninjatrader":
        path = Path(args.file)
        if path.is_dir():
            files = sorted(item for item in path.iterdir() if item.suffix.lower() in {".csv", ".txt"})
            if not files:
                raise SystemExit("No CSV/text exports found in directory")
            if len(files) > 1:
                raise SystemExit("Directory imports must be unambiguous: import each exact contract separately")
            path = files[0]
        metadata = ImportMetadata(args.symbol, args.contract, args.source_timezone,
                                  args.interval_minutes, args.source, args.capture_date,
                                  args.timestamp_format, args.delimiter, args.capture_id)
        print(json.dumps(import_ninjatrader(path, store.path, metadata, dry_run=args.dry_run),
                         indent=2, default=str))
    elif args.command == "import-ninjatrader-batch":
        manifest_path = Path(args.manifest).resolve()
        definition = json.loads(manifest_path.read_text(encoding="utf-8"))
        items = []
        for item in definition.get("files", []):
            source_file = Path(item["file"])
            if not source_file.is_absolute():
                source_file = manifest_path.parent / source_file
            items.append((source_file, ImportMetadata(
                item["symbol"], item["contract"], item["timezone"], int(item.get("interval", 1)),
                item["source"], item["capture_date"], item.get("timestamp_format", "auto"),
                item.get("delimiter", "auto"))))
        print(json.dumps(import_ninjatrader_batch(items, store.path, definition["capture_id"],
                         dry_run=args.dry_run), indent=2, default=str))
    elif args.command == "import-databento":
        print(json.dumps(import_databento_package(args.package, store.path, args.capture_id,
                         dry_run=args.dry_run), indent=2, default=str))
    elif args.command == "verify-databento-package":
        print(json.dumps(verify_package(args.package), indent=2, default=str))
    elif args.command == "list-captures":
        print(json.dumps(list_captures(store.path), indent=2))
    elif args.command == "export-manifest":
        print(json.dumps(export_manifest(store.path, args.capture_id), indent=2))
    else:
        print(json.dumps(verify_capture(store.path, args.capture_id), indent=2))


if __name__ == "__main__":
    main()
