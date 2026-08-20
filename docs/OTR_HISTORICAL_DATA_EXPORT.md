# OTR Historical Futures Data Export

Phase 5.5 accepts real, exact-contract NQ/MNQ, ES/MES, and GC/MGC one-minute
OHLCV exports only. It never converts a mini history into micro history and it
never writes `data/otrmarket.db`.

## NinjaTrader 8 export

1. Open **Tools → Historical Data** in NinjaTrader 8.
2. Use **Export**, select **Minute** data, and choose a one-minute interval.
3. Export the exact contracts that were front contracts during the requested
   period. For a 6–12 month study this normally means several quarterly NQ and
   ES contracts and the applicable GC delivery contracts. Do not export one
   current contract as though it covered the whole period.
4. Export NQ, ES, and GC separately with timestamp, open, high, low, close, and
   volume. Retain the NinjaTrader/source timezone used by the export.
5. Name files `<instrument>_<month><year>_1m_<start>_<end>.csv`, for example
   `NQ_SEP26_1m_20260601_20260831.csv`.
6. Record the export date, data provider/source, timezone, and any explicit roll
   schedule alongside the files.

The importer accepts headers named `timestamp/time/datetime`, `open`, `high`,
`low`, `close`, and `volume`; comma, tab, or semicolon delimiters; and timestamp
formats `YYYYMMDD HHmmss`, `MM/dd/yyyy HH:mm:ss`, or ISO-style date/time.

Always dry-run first:

```bash
python scripts/otr_historical.py --database data/otr_historical.db \
  import-ninjatrader --file exports/NQ_SEP26_1m_20260601_20260831.csv \
  --symbol NQ --contract "NQ SEP26" --timezone America/New_York \
  --interval 1 --source "NinjaTrader historical export / <provider>" \
  --capture-date 2026-08-20 --timestamp-format us --dry-run
```

Remove `--dry-run` only after verifying the detected columns, timezone, date
range, duplicates, gaps, coverage, and integrity findings. Import each exact
contract separately. Capture IDs are immutable and replay/experiment manifests
must select them explicitly.

For a reproducible multi-market capture, use `import-ninjatrader-batch` with a
JSON manifest containing `capture_id` and a `files` array. Each file entry uses
the same keys as the CLI (`file`, `symbol`, `contract`, `timezone`, `interval`,
`source`, `capture_date`, plus optional `timestamp_format` and `delimiter`).
Relative file paths resolve beside the manifest. This is the preferred path for
an NQ/ES/GC dataset because all replay inputs then share one explicit capture ID.

Useful checks:

```bash
python scripts/otr_historical.py list-captures
python scripts/otr_historical.py coverage --capture-id <capture-id>
python scripts/otr_historical.py export-manifest --capture-id <capture-id>
python scripts/otr_historical.py verify-capture --capture-id <capture-id>
```

The built-in expected-minute calculator uses generic CME/COMEX weekly hours and
the daily maintenance break. It does not invent a holiday calendar. Therefore
imports remain `USABLE_WITH_WARNINGS` until holidays and the contract roll
schedule are explicitly verified; `VALIDATED` is reserved for data satisfying
all documented integrity requirements.
