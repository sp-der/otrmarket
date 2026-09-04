import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import src.storage.database as db
from src.storage.database_concurrency80 import install


ROOT = Path(__file__).resolve().parents[1]


class Operation80SQLiteConcurrencyTests(unittest.TestCase):
    def setUp(self):
        install()
        self.old_path = db.DB_PATH
        self.tempdir = tempfile.TemporaryDirectory()
        db.DB_PATH = Path(self.tempdir.name) / "otrmarket.db"

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.tempdir.cleanup()

    def test_real_80_entrypoints_install_guard_before_inherited_modules(self):
        for module in ("src.dashboard.server_80", "src.main_80"):
            output = subprocess.check_output(
                [
                    sys.executable,
                    "-c",
                    f"import {module}; import src.storage.database as d; print(d.get_connection.__module__)",
                ],
                cwd=ROOT,
                text=True,
            ).strip().splitlines()[-1]
            self.assertEqual(output, "src.storage.database_concurrency80")

    def test_get_connection_is_readable_while_writer_holds_reserved_lock(self):
        seed = db.get_connection()
        seed.close()

        writer = db.get_connection()
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO engine_state(key, value, updated_at) VALUES ('lock-test', '1', 'now')"
        )
        try:
            started = time.monotonic()
            reader = db.get_connection()
            try:
                self.assertEqual(reader.execute("SELECT 1").fetchone()[0], 1)
                reader.execute("SELECT COUNT(*) FROM market_quotes").fetchone()
                timeout_ms = int(reader.execute("PRAGMA busy_timeout").fetchone()[0])
            finally:
                reader.close()
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(timeout_ms, 1000)
            self.assertLess(elapsed, 1.0)
        finally:
            writer.rollback()
            writer.close()

    def test_concurrent_bridge_style_writes_and_dashboard_reads(self):
        old_prune = os.environ.get("OTR_QUOTE_PRUNE_EVERY")
        os.environ["OTR_QUOTE_PRUNE_EVERY"] = "999999"
        errors = []
        start = threading.Event()

        def writer(worker_id: int):
            try:
                con = db.get_connection()
                start.wait()
                for batch in range(20):
                    rows = [
                        (
                            f"2026-08-03T00:{worker_id:02d}:{batch:02d}+00:00",
                            None,
                            "ninjatrader:GC DEC26",
                            "GC",
                            3500.0 + worker_id + batch / 10.0,
                            3499.9,
                            3500.1,
                        )
                        for _ in range(5)
                    ]
                    db.save_quotes_batch(con, rows)
                con.close()
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        def reader():
            try:
                start.wait()
                for _ in range(80):
                    con = db.get_connection()
                    con.execute("SELECT COUNT(*) FROM market_quotes").fetchone()
                    con.execute("SELECT COUNT(*) FROM strategy_diagnostics").fetchone()
                    con.close()
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        try:
            threads = [threading.Thread(target=writer, args=(i,)) for i in range(2)]
            threads += [threading.Thread(target=reader) for _ in range(4)]
            for thread in threads:
                thread.start()
            start.set()
            for thread in threads:
                thread.join(timeout=20)

            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            con = db.get_connection()
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM market_quotes").fetchone()[0], 200)
            finally:
                con.close()
        finally:
            if old_prune is None:
                os.environ.pop("OTR_QUOTE_PRUNE_EVERY", None)
            else:
                os.environ["OTR_QUOTE_PRUNE_EVERY"] = old_prune

    def test_prune_uses_nonblocking_wal_policy(self):
        self.assertEqual(db.prune_market_quotes.__module__, "src.storage.database_concurrency80")


if __name__ == "__main__":
    unittest.main()