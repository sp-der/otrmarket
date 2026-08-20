import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.research.historical.candles import build_canonical_candles
from src.research.historical.catalog import contract_spec, parse_contract
from src.research.historical.integrity import analyze_integrity
from src.research.historical.store import HistoricalStore, RawEvent


UTC = timezone.utc


class HistoricalFoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "historical.db"
        self.store = HistoricalStore(self.path)
        self.store.initialize()
        self.store.create_capture("test", "unit", "REPLAY", datetime(2026, 1, 1, tzinfo=UTC))

    def tearDown(self):
        self.temp.cleanup()

    def _events(self, contract, minutes=60, events_per_minute=2, start=None):
        start = start or datetime(2026, 1, 2, 14, 0, tzinfo=UTC)
        result = []
        for minute in range(minutes):
            for event in range(events_per_minute):
                stamp = start + timedelta(minutes=minute, seconds=event * 20)
                price = 20000 + minute + event * 0.25
                result.append(RawEvent(contract, stamp, price, price - .25, price + .25, 2, source="test", ingested_at=stamp, source_event_id=f"{contract}:{minute}:{event}"))
        return result

    def test_root_mapping_and_mini_micro_metadata(self):
        expected = {
            "NQ": ("NQ", "MINI", 0.25, 20, 5), "MNQ": ("NQ", "MICRO", 0.25, 2, .5),
            "ES": ("ES", "MINI", 0.25, 50, 12.5), "MES": ("ES", "MICRO", 0.25, 5, 1.25),
            "GC": ("GC", "MINI", .10, 100, 10), "MGC": ("GC", "MICRO", .10, 10, 1),
        }
        for instrument, values in expected.items():
            spec = contract_spec(instrument)
            self.assertEqual((spec.root, spec.size_class, spec.tick_size, spec.point_value, spec.tick_value), values)
        with self.store.connect() as connection:
            persisted = connection.execute(
                "SELECT root_symbol,size_class,tick_size,point_value,tick_value FROM instrument_roots WHERE instrument='MES'"
            ).fetchone()
        self.assertEqual(tuple(persisted), ("ES", "MICRO", 0.25, 5.0, 1.25))

    def test_contract_identity_is_preserved(self):
        events = [
            RawEvent("MNQ SEP26", "2026-01-01T00:00:00Z", 1, volume=1, source_event_id="a"),
            RawEvent("NQ SEP26", "2026-01-01T00:00:01Z", 2, volume=1, source_event_id="b"),
        ]
        self.store.append_events("test", events)
        with self.store.connect() as connection:
            rows = connection.execute("SELECT contract,root_symbol,size_class FROM historical_events ORDER BY sequence_no").fetchall()
        self.assertEqual([tuple(row) for row in rows], [("MNQ SEP26", "NQ", "MICRO"), ("NQ SEP26", "NQ", "MINI")])

    def test_deterministic_sequence_and_duplicate_handling(self):
        events = self._events("MNQ SEP26", minutes=1)
        self.assertEqual(self.store.append_events("test", events), (2, 0))
        self.assertEqual(self.store.append_events("test", events), (0, 2))
        with self.store.connect() as connection:
            self.assertEqual([row[0] for row in connection.execute("SELECT sequence_no FROM historical_events ORDER BY sequence_no")], [1, 2])

    def test_events_are_immutable(self):
        self.store.append_events("test", self._events("MNQ SEP26", minutes=1))
        with self.store.connect() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE historical_events SET last_price=0")

    def test_canonical_aggregation_all_timeframes(self):
        self.store.append_events("test", self._events("MNQ SEP26", minutes=60))
        with self.store.connect() as connection:
            build_canonical_candles(connection, "test")
            counts = dict(connection.execute("SELECT timeframe,COUNT(*) FROM canonical_candles GROUP BY timeframe"))
            self.assertEqual(counts, {"1m": 60, "5m": 12, "15m": 4, "30m": 2, "1h": 1})
            one = connection.execute("SELECT open,high,low,close,volume,event_count,completeness_state FROM canonical_candles WHERE timeframe='1m' ORDER BY open_time LIMIT 1").fetchone()
            self.assertEqual(tuple(one), (20000.0, 20000.25, 20000.0, 20000.25, 4, 2, "COMPLETE"))
            five = connection.execute("SELECT open,high,low,close,volume,event_count,completeness_state FROM canonical_candles WHERE timeframe='5m'").fetchone()
            self.assertEqual(tuple(five), (20000.0, 20004.25, 20000.0, 20004.25, 20, 10, "COMPLETE"))

    def test_gap_and_incomplete_candle_detection(self):
        events = self._events("MNQ SEP26", minutes=3)
        events = events[:2] + events[4:]  # remove the middle minute
        self.store.append_events("test", events)
        with self.store.connect() as connection:
            build_canonical_candles(connection, "test")
            findings = analyze_integrity(connection, "test")
            kinds = {finding["finding_type"] for finding in findings}
            self.assertIn("MISSING_PERIOD", kinds)
            self.assertIn("INCOMPLETE_CANDLE", kinds)
            bar = connection.execute("SELECT completeness_state,gap_state FROM canonical_candles WHERE timeframe='5m'").fetchone()
            self.assertEqual(tuple(bar), ("INCOMPLETE", "GAPPED"))

    def test_out_of_order_and_sequence_gap_detection(self):
        start = datetime(2026, 1, 2, 14, 0, tzinfo=UTC)
        self.store.append_events("test", [
            RawEvent("NQ SEP26", start + timedelta(seconds=2), 1, volume=1, sequence_no=1),
            RawEvent("NQ SEP26", start + timedelta(seconds=1), 1, volume=1, sequence_no=3),
        ])
        with self.store.connect() as connection:
            build_canonical_candles(connection, "test")
            kinds = {item["finding_type"] for item in analyze_integrity(connection, "test")}
        self.assertIn("OUT_OF_ORDER_TIMESTAMP", kinds)
        self.assertIn("SEQUENCE_GAP", kinds)

    def test_nq_es_synchronization_detection(self):
        start = datetime(2026, 1, 2, 14, 0, tzinfo=UTC)
        self.store.append_events("test", self._events("MNQ SEP26", minutes=2, events_per_minute=1, start=start))
        self.store.append_events("test", self._events("MES SEP26", minutes=1, events_per_minute=1, start=start))
        with self.store.connect() as connection:
            build_canonical_candles(connection, "test")
            pair_findings = [item for item in analyze_integrity(connection, "test") if item["finding_type"] == "MISSING_NQ_ES_PAIR"]
        self.assertEqual(len(pair_findings), 1)

    def test_contract_expiry_is_estimated_not_claimed_exact(self):
        _, expiry = parse_contract("MGC DEC26")
        self.assertEqual(expiry.isoformat(), "2026-12-31")


if __name__ == "__main__":
    unittest.main()
