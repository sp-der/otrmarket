from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class VerifyTagUpsert80Tests(unittest.TestCase):
    def test_verify_trigger_survives_outer_paper_upsert(self):
        # Import the late 7.2S wrapper only inside this near-end regression test.
        # Importing it at module discovery time mutates inherited quality/risk
        # globals before older compatibility tests execute in the same process.
        from src import main_72s

        old_mode = os.environ.get("OTR_TRADING_MODE")
        old_run = os.environ.get("OTR_VERIFY_RUN_ID")
        old_wipe = os.environ.get("OTR_VERIFY_WIPE_TOKEN")
        old_get_connection = main_72s.get_connection

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "verify.db"
            seed = sqlite3.connect(path)
            seed.execute(
                """
                CREATE TABLE paper_trades (
                    setup_id TEXT PRIMARY KEY,
                    status TEXT,
                    updated_at TEXT
                )
                """
            )
            seed.commit()
            seed.close()

            try:
                os.environ["OTR_TRADING_MODE"] = "VERIFY"
                os.environ["OTR_VERIFY_RUN_ID"] = "VERIFY-upsert-test"
                os.environ.pop("OTR_VERIFY_WIPE_TOKEN", None)
                main_72s.get_connection = lambda: sqlite3.connect(path)
                main_72s._install_verify_trade_tag_trigger_72s()

                con = sqlite3.connect(path)
                try:
                    con.execute(
                        "INSERT INTO paper_trades(setup_id,status,updated_at) VALUES (?,?,?)",
                        ("gc-1", "PENDING", "2026-08-03T12:00:00+00:00"),
                    )
                    for status, timestamp in (
                        ("OPEN", "2026-08-03T12:01:00+00:00"),
                        ("CLOSED", "2026-08-03T12:05:00+00:00"),
                    ):
                        con.execute(
                            """
                            INSERT INTO paper_trades(setup_id,status,updated_at)
                            VALUES (?,?,?)
                            ON CONFLICT(setup_id) DO UPDATE SET
                                status=excluded.status,
                                updated_at=excluded.updated_at
                            """,
                            ("gc-1", status, timestamp),
                        )
                    con.commit()

                    rows = con.execute(
                        "SELECT run_id,setup_id,build,first_seen_at FROM verify_run_trades"
                    ).fetchall()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0][0], "VERIFY-upsert-test")
                    self.assertEqual(rows[0][1], "gc-1")
                    self.assertEqual(rows[0][2], "7.2S")
                    self.assertEqual(rows[0][3], "2026-08-03T12:00:00+00:00")
                finally:
                    con.close()
            finally:
                main_72s.get_connection = old_get_connection
                for name, value in (
                    ("OTR_TRADING_MODE", old_mode),
                    ("OTR_VERIFY_RUN_ID", old_run),
                    ("OTR_VERIFY_WIPE_TOKEN", old_wipe),
                ):
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
