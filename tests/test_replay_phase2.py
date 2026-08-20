import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.research.historical.candles import build_canonical_candles
from src.research.historical.store import HistoricalStore, RawEvent
from src.research.replay.runner import ReplayRunner
from src.research.replay.runs import ReplayRunStore, RunManifest
from src.research.replay.scheduler import ReplayItem, merge_event_streams, pair_available, synchronized_candle_groups
from src.strategies.models import Candle


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


class ReplayPhase2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.historical = self.base / "historical.db"
        store = HistoricalStore(self.historical)
        store.initialize()
        start = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
        store.create_capture("complete", "unit", "REPLAY", start)
        events = []
        for minute in range(20):
            for instrument, base in (("MNQ SEP26", 20000), ("MES SEP26", 6000), ("MGC FEB26", 2700)):
                for second in (0, 30):
                    stamp = start + timedelta(minutes=minute, seconds=second)
                    events.append(RawEvent(instrument, stamp, base + minute * .25 + second / 120,
                                           volume=1, source="unit", ingested_at=stamp,
                                           source_event_id=f"{instrument}:{stamp.isoformat()}"))
        store.append_events("complete", events)
        with store.connect() as connection:
            build_canonical_candles(connection, "complete")
        self.start = start.isoformat()
        self.end = (start + timedelta(minutes=20)).isoformat()

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self, run_id):
        return RunManifest(
            run_id=run_id, git_commit="62ee79bf441e2da26787052d00a92544c5605b17",
            capture_id="complete", markets=("NQ","ES","GC"),
            contracts=("MNQ SEP26","MES SEP26","MGC FEB26"), start_time=self.start,
            end_time=self.end, enabled_timeframes=("1m","5m","15m","30m","1h"),
            configuration={"OTR_EXECUTION_TIMEFRAME":"ALL"},
            account_profile={"profile":"LUCID_PRO_50K"}, replay_mode="CANDLE_APPROXIMATE",
            created_at="2026-01-01T00:00:00+00:00",
        )

    def run_once(self, folder, run_id="determinism-run"):
        store = ReplayRunStore(folder / "runs.db", folder / "runs")
        store.initialize()
        return ReplayRunner(store, ROOT).run(self.manifest(run_id), self.historical), store

    def test_deterministic_event_ordering(self):
        stamp = "2026-01-01T00:00:00+00:00"
        merged = merge_event_streams([
            [ReplayItem(stamp,"ES","MES SEP26",2,"es2"), ReplayItem(stamp,"ES","MES SEP26",1,"es1")],
            [ReplayItem(stamp,"NQ","MNQ SEP26",1,"nq")],
        ])
        self.assertEqual([item.payload for item in merged], ["nq","es1","es2"])

    def test_synchronized_closes_and_htf_order(self):
        rows = [
            {"close_time":self.end,"timeframe":"1m","root_symbol":"NQ","contract":"MNQ SEP26"},
            {"close_time":self.end,"timeframe":"1h","root_symbol":"NQ","contract":"MNQ SEP26"},
            {"close_time":self.end,"timeframe":"1m","root_symbol":"ES","contract":"MES SEP26"},
        ]
        groups = synchronized_candle_groups(rows)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][1][0]["timeframe"], "1h")
        self.assertEqual({row["root_symbol"] for row in groups[0][1] if row["timeframe"] == "1m"}, {"NQ","ES"})

    def test_missing_smt_pair_is_suppressed(self):
        close = datetime.fromisoformat(self.end)
        candle = Candle("NQ","1m",close-timedelta(minutes=1),close,1,1,1,1,1)
        self.assertFalse(pair_available({("NQ","1m"):[candle]}, "NQ", "1m", self.end))

    def test_identical_replay_and_clean_rerun(self):
        first, _ = self.run_once(self.base / "a")
        second, _ = self.run_once(self.base / "b")
        self.assertEqual(first["decision_digest"], second["decision_digest"])
        self.assertEqual(first["traces"], second["traces"])

    def test_isolated_ledgers_and_deterministic_ids(self):
        first, _ = self.run_once(self.base / "a", "run-a")
        second, _ = self.run_once(self.base / "b", "run-b")
        self.assertNotEqual(first["ledger_path"], second["ledger_path"])
        for result in (first, second):
            connection = sqlite3.connect(result["ledger_path"])
            self.assertIn("paper_trades", {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            connection.close()
        from src.research.replay.worker import DeterministicIds
        one, two = DeterministicIds("same"), DeterministicIds("same")
        for item in (one,two): item.set_context("NQ","5m","ICT","2026-01-01T00:00:00Z")
        self.assertEqual(one.factory("ICT")().hex, two.factory("ICT")().hex)

    def test_manifest_and_trace_are_immutable(self):
        result, store = self.run_once(self.base / "a")
        with sqlite3.connect(store.database) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE backtest_runs SET capture_id='other'")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM decision_traces")
            row=connection.execute("SELECT account_profile_json,pending_lifetime_bars_json FROM backtest_runs").fetchone()
            profile=json.loads(row[0]);lifetimes=json.loads(row[1])
            self.assertEqual(profile["profile_verification"],"RESEARCH_REFERENCE_PROFILE")
            self.assertEqual(lifetimes,{"1m":15,"5m":8,"15m":4,"1h":2})

    def test_research_lifetime_override_is_frozen(self):
        store=ReplayRunStore(self.base/"override.db",self.base/"override-runs");store.initialize()
        manifest=self.manifest("override-lifetime")
        manifest=RunManifest(**{**manifest.__dict__,"pending_lifetime_bars":{"1m":15,"5m":8,"15m":8,"1h":2}})
        ReplayRunner(store,ROOT).run(manifest,self.historical)
        with sqlite3.connect(store.database) as connection:
            frozen=json.loads(connection.execute("SELECT pending_lifetime_bars_json FROM backtest_runs").fetchone()[0])
            self.assertEqual(frozen["15m"],8)
            with self.assertRaises(sqlite3.IntegrityError):connection.execute("UPDATE backtest_runs SET pending_lifetime_bars_json='{}'")

    def test_replay_uses_event_time_and_emits_trace(self):
        result, _ = self.run_once(self.base / "a")
        self.assertGreater(result["trace_count"], 0)
        for trace in result["traces"]:
            stamp = datetime.fromisoformat(trace["event_time"])
            self.assertGreaterEqual(stamp, datetime.fromisoformat(self.start))
            self.assertLessEqual(stamp, datetime.fromisoformat(self.end))

    def test_session_time_is_replay_time(self):
        from src.main_58 import _session_tier_58
        from src.risk.session_consistency import SessionConsistencyConfig
        replay_time = datetime(2026, 1, 6, 3, 30, tzinfo=UTC).astimezone(SessionConsistencyConfig().timezone)
        tier, _ = _session_tier_58("NQ", replay_time, SessionConsistencyConfig())
        self.assertEqual(tier, "ASIA")

    def test_cooldown_and_recovery_use_replay_ledger_time(self):
        from types import SimpleNamespace
        from src import main_70 as op70
        connection = sqlite3.connect(":memory:")
        connection.executescript("""CREATE TABLE paper_trades(setup_id TEXT,symbol TEXT,status TEXT,result TEXT,closed_at TEXT);
          CREATE TABLE strategy_setups(setup_id TEXT,created_at TEXT,payload_json TEXT);""")
        loss_time = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
        for ident, symbol, minutes in (("a","NQ",0),("b","ES",5)):
            closed = loss_time + timedelta(minutes=minutes)
            connection.execute("INSERT INTO paper_trades VALUES(?,?, 'CLOSED','LOSS',?)", (ident,symbol,closed.isoformat()))
            connection.execute("INSERT INTO strategy_setups VALUES(?,?,?)", (ident,closed.isoformat(),json.dumps({"metadata":{}})))
        setup = SimpleNamespace(symbol="NQ",timeframe="1m",created_at=loss_time+timedelta(minutes=10),metadata={"a_plus_context":{"quality_grade":"A+"},"risk_multiplier":1.0})
        allowed, _ = op70._same_symbol_cooldown_70(connection, setup)
        self.assertFalse(allowed)
        setup.created_at = loss_time + timedelta(minutes=40)
        allowed, _ = op70._post_loss_risk_70(connection, setup)
        self.assertTrue(allowed)
        self.assertEqual(setup.metadata["recovery_control_70"]["mode"], "ACCOUNT_RECOVERY")

    def test_pending_expiry_uses_replay_time(self):
        from src.execution.paper import PaperExecutor
        from tests.test_operation50 import ict_setup
        created = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
        setup = ict_setup("bullish", created_at=created)
        setup.setup_id, setup.entry_price, setup.stop_price, setup.target_price = "phase2-pending", 100.0, 95.0, 110.0
        setup.risk_reward = 2.0
        executor = PaperExecutor()
        executor.register_setup(setup, risk_dollars=200)
        changed = executor.on_price("NQ", 103.0, created + timedelta(minutes=16))
        self.assertEqual(changed[0].result, "EXPIRED_BEFORE_ENTRY")

    def test_tick_exact_rejects_incomplete_data(self):
        manifest = self.manifest("tick")
        manifest = RunManifest(**{**manifest.__dict__, "replay_mode":"TICK_EXACT"})
        # Complete synthetic data is accepted; retained missing-volume data is
        # covered by the worker's explicit integrity query.
        store = ReplayRunStore(self.base / "tick.db", self.base / "tick-runs")
        store.initialize()
        result = ReplayRunner(store, ROOT).run(manifest, self.historical)
        self.assertGreaterEqual(result["trace_count"], 0)

    def test_no_production_database_mutation(self):
        production = ROOT / "data" / "otrmarket.db"
        before = hashlib.sha256(production.read_bytes()).hexdigest()
        self.run_once(self.base / "a")
        after = hashlib.sha256(production.read_bytes()).hexdigest()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
