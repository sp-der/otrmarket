import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.research.historical.acquisition import (
    ImportMetadata, ImportValidationError, expected_minutes, export_manifest,
    import_ninjatrader, import_ninjatrader_batch, inspect_import, is_expected_futures_minute,
    observed_roll_boundaries, paired_coverage, verify_capture,
)
from src.research.historical.store import HistoricalStore


class HistoricalAcquisitionPhase55Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "historical.db"
        self.production = self.root / "otrmarket.db"
        self.production.write_bytes(b"protected-production-ledger")
        self.before = hashlib.sha256(self.production.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.production.read_bytes()).hexdigest(), self.before)
        self.temp.cleanup()

    def _write(self, name="nq.csv", rows=None, delimiter=","):
        rows = rows or [
            ("03/08/2026 18:00:00", 20000, 20001, 19999, 20000.25, 10),
            ("03/08/2026 18:01:00", 20000.25, 20001, 20000, 20000.5, 11),
        ]
        path = self.root / name
        lines = [delimiter.join(("timestamp","open","high","low","close","volume"))]
        lines += [delimiter.join(map(str, row)) for row in rows]
        path.write_text("\n".join(lines)+"\n", encoding="utf-8")
        return path

    def _meta(self, **changes):
        values = dict(symbol="NQ", contract="NQ JUN26", source_timezone="America/New_York",
                      interval_minutes=1, source="NinjaTrader historical export",
                      capture_date="2026-08-20", timestamp_format="us", delimiter="auto",
                      capture_id="nt-test")
        values.update(changes)
        return ImportMetadata(**values)

    def test_ninjatrader_csv_parsing_timestamp_and_volume(self):
        report = inspect_import(self._write(), self._meta())
        self.assertEqual(report["row_count"], 2)
        self.assertEqual(report["start"], "2026-03-08T22:00:00+00:00")
        self.assertEqual(report["detected"]["delimiter"], "comma")
        self.assertEqual(report["zero_volume_count"], 0)

    def test_common_delimiters_and_ninjatrader_timestamp(self):
        path = self._write("es.txt", [("20260308 180000", 5000, 5001, 4999, 5000.25, 7)], "\t")
        report = inspect_import(path, self._meta(symbol="ES", contract="ES JUN26", timestamp_format="ninjatrader"))
        self.assertEqual(report["detected"]["delimiter"], "tab")

    def test_dst_boundaries(self):
        spring = self._write(rows=[("03/08/2026 02:30:00",20000,20001,19999,20000.25,1)])
        with self.assertRaisesRegex(ImportValidationError, "Nonexistent local time"):
            inspect_import(spring, self._meta())
        fall = self._write(rows=[("11/01/2026 01:30:00",20000,20001,19999,20000.25,1)])
        self.assertEqual(inspect_import(fall, self._meta())["start"], "2026-11-01T05:30:00+00:00")

    def test_ohlc_tick_negative_volume_and_impossible_price_validation(self):
        rows = [("03/08/2026 18:00:00",20000.1,19999,20001,0,-1)]
        report = inspect_import(self._write(rows=rows), self._meta())
        for finding in ("MALFORMED_OHLC","IMPOSSIBLE_PRICE","NEGATIVE_VOLUME","TICK_SIZE_VIOLATION"):
            self.assertEqual(report["finding_counts"][finding], 1)

    def test_duplicate_and_timestamp_reversal_detection(self):
        rows = [
            ("03/08/2026 18:01:00",20000,20001,19999,20000.25,1),
            ("03/08/2026 18:00:00",20000,20001,19999,20000.25,1),
            ("03/08/2026 18:00:00",20000,20001,19999,20000.25,1),
        ]
        report = inspect_import(self._write(rows=rows), self._meta())
        self.assertEqual(report["duplicates"], 1)
        self.assertEqual(report["timestamp_reversals"], 1)
        result = import_ninjatrader(self._write("duplicates.csv", rows), self.db,
                                    self._meta(capture_id="duplicates"))
        self.assertEqual(result["validation_status"], "INCOMPLETE")
        with HistoricalStore(self.db).connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM raw_import_bars").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT completeness_state FROM canonical_candles WHERE timeframe='1m' AND open_time LIKE '%22:00:%'").fetchone()[0], "INCOMPLETE")

    def test_missing_periods_and_session_aware_coverage(self):
        rows = [
            ("03/08/2026 18:00:00",20000,20001,19999,20000.25,1),
            ("03/08/2026 18:02:00",20000,20001,19999,20000.25,1),
        ]
        report = inspect_import(self._write(rows=rows), self._meta())
        self.assertEqual(len(report["missing_minutes"]), 1)
        chicago = ZoneInfo("America/Chicago")
        self.assertFalse(is_expected_futures_minute(datetime(2026,3,9,16,30,tzinfo=chicago)))
        self.assertTrue(is_expected_futures_minute(datetime(2026,3,8,17,0,tzinfo=chicago)))
        self.assertFalse(is_expected_futures_minute(datetime(2026,3,7,12,0,tzinfo=chicago)))

    def test_import_raw_manifest_canonical_and_aggregations(self):
        rows=[]
        for minute in range(60):
            rows.append((f"03/08/2026 18:{minute:02d}:00",20000,20001,19999,20000.25,minute))
        result = import_ninjatrader(self._write(rows=rows), self.db, self._meta())
        self.assertEqual(result["canonical_counts"], {"1h":1,"15m":4,"1m":60,"30m":2,"5m":12})
        manifest = export_manifest(self.db, "nt-test")
        self.assertEqual(manifest["contracts"], ["NQ JUN26"])
        self.assertEqual(manifest["raw_row_count"], 60)
        self.assertTrue(verify_capture(self.db, "nt-test")["integrity_verified"])
        with HistoricalStore(self.db).connect() as connection:
            bar = connection.execute("SELECT volume FROM canonical_candles WHERE capture_id='nt-test' AND timeframe='5m' ORDER BY open_time LIMIT 1").fetchone()
            self.assertEqual(bar[0], 10)
            provenance = connection.execute("SELECT COUNT(*) FROM canonical_bar_provenance WHERE capture_id='nt-test'").fetchone()[0]
            self.assertEqual(provenance, 79)

    def test_incomplete_higher_timeframe_not_synthesized(self):
        result = import_ninjatrader(self._write(), self.db, self._meta())
        with HistoricalStore(self.db).connect() as connection:
            bar = connection.execute("SELECT completeness_state,gap_state,event_count FROM canonical_candles WHERE timeframe='5m'").fetchone()
        self.assertEqual(tuple(bar), ("INCOMPLETE","GAPPED",2))
        self.assertEqual(result["canonical_counts"]["1m"], 2)

    def test_dry_run_does_not_create_database(self):
        result = import_ninjatrader(self._write(), self.db, self._meta(), dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse(self.db.exists())

    def test_raw_and_manifest_are_immutable(self):
        import_ninjatrader(self._write(), self.db, self._meta())
        with HistoricalStore(self.db).connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE raw_import_bars SET close=1")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE capture_manifests SET validation_status='VALIDATED'")

    def test_contract_separation_and_roll_observation(self):
        import_ninjatrader(self._write("nq_jun.csv"), self.db, self._meta(capture_id="jun"))
        import_ninjatrader(self._write("nq_sep.csv"), self.db, self._meta(contract="NQ SEP26", capture_id="sep"))
        with HistoricalStore(self.db).connect() as connection:
            contracts = [row[0] for row in connection.execute("SELECT contract FROM contracts WHERE contract LIKE 'NQ %' ORDER BY contract")]
            rolls = observed_roll_boundaries(connection, "NQ")
        self.assertEqual(contracts, ["NQ JUN26","NQ SEP26"])
        self.assertEqual(rolls[0]["method"], "OBSERVED_COVERAGE_ONLY_ROLL_SCHEDULE_REQUIRED")

    def test_malformed_import_and_unknown_economics_rejected(self):
        bad = self.root / "bad.csv"
        bad.write_text("time,open,high\n20260101 000000,1,2\n")
        with self.assertRaises(ImportValidationError):
            inspect_import(bad, self._meta())
        with self.assertRaises(ValueError):
            self._meta(symbol="BTC", contract="BTC SEP26").normalized()

    def test_large_batched_import(self):
        rows=[]
        for minute in range(6001):
            stamp = datetime(2026,3,8,18,0)+__import__('datetime').timedelta(minutes=minute)
            rows.append((stamp.strftime("%m/%d/%Y %H:%M:%S"),20000,20001,19999,20000.25,1))
        result = import_ninjatrader(self._write(rows=rows), self.db, self._meta(), batch_size=500)
        self.assertEqual(result["row_count"], 6001)

    def test_nq_es_paired_coverage(self):
        import_ninjatrader(self._write("nq.csv"), self.db, self._meta(capture_id="nq"))
        import_ninjatrader(self._write("es.csv"), self.db, self._meta(symbol="ES",contract="ES JUN26",capture_id="es"))
        with HistoricalStore(self.db).connect() as connection:
            paired = paired_coverage(connection)
        self.assertEqual(paired["paired_minutes"], 2)
        self.assertEqual(paired["pair_coverage_percentage"], 100)

    def test_manifest_batch_preserves_markets_contracts_and_pairing(self):
        nq = self._write("nq.csv")
        es = self._write("es.csv")
        gc = self._write("gc.csv", [("03/08/2026 18:00:00",3000,3000.1,2999.9,3000,2),
                                    ("03/08/2026 18:01:00",3000,3000.1,2999.9,3000,3)])
        result = import_ninjatrader_batch([
            (nq,self._meta(capture_id=None)),
            (es,self._meta(symbol="ES",contract="ES JUN26",capture_id=None)),
            (gc,self._meta(symbol="GC",contract="GC JUN26",capture_id=None)),
        ], self.db, "portfolio")
        manifest = export_manifest(self.db, "portfolio")
        self.assertEqual(manifest["markets"], ["ES","GC","NQ"])
        self.assertEqual(set(manifest["contracts"]), {"NQ JUN26","ES JUN26","GC JUN26"})
        with HistoricalStore(self.db).connect() as connection:
            self.assertEqual(paired_coverage(connection, "portfolio")["pair_coverage_percentage"], 100)
        self.assertEqual(result["validation_status"], "USABLE_WITH_WARNINGS")

    def test_retained_capture_cannot_be_upgraded(self):
        source = Path("data/otr_historical.db")
        if not source.exists():
            self.skipTest("retained Phase 1 store absent")
        connection = sqlite3.connect(source)
        row = connection.execute("SELECT capture_id FROM capture_sessions WHERE capture_id='retained-operation70-phase1'").fetchone()
        connection.close()
        self.assertEqual(row[0], "retained-operation70-phase1")


if __name__ == "__main__":
    unittest.main()
