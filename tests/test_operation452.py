import os
import tempfile
import unittest
from pathlib import Path

import src.storage.database as db


ROOT = Path(__file__).resolve().parents[1]


class Operation452StorageTests(unittest.TestCase):
    def test_supervisor_runtime_metadata_is_ephemeral(self):
        server = (ROOT / "src/dashboard/server.py").read_text()
        app = (ROOT / "src/dashboard/app.py").read_text()
        self.assertIn('"/tmp/otrmarket"', server)
        self.assertIn('"/tmp/otrmarket"', app)
        self.assertNotIn('ENGINE_LOG = DATA_DIR / "engine.log"', server)
        self.assertIn("Engine logs stream directly into Railway deploy logs", server)

    def test_quote_retention_preserves_lifetime_counter(self):
        old_path = db.DB_PATH
        old_env = {
            key: os.environ.get(key)
            for key in ("OTR_QUOTE_RETENTION_NQ", "OTR_QUOTE_PRUNE_EVERY")
        }
        try:
            with tempfile.TemporaryDirectory() as td:
                db.DB_PATH = Path(td) / "otrmarket.db"
                os.environ["OTR_QUOTE_RETENTION_NQ"] = "1000"
                os.environ["OTR_QUOTE_PRUNE_EVERY"] = "999999"
                con = db.get_connection()
                rows = [
                    (
                        f"2026-08-13T10:{i // 60:02d}:{i % 60:02d}+00:00",
                        None,
                        "ninjatrader:NQ SEP26",
                        "NQ",
                        30000.0 + i / 100,
                        29999.75,
                        30000.0,
                    )
                    for i in range(1200)
                ]
                db.save_quotes_batch(con, rows)
                latest_id = con.execute("SELECT MAX(id) FROM market_quotes").fetchone()[0]
                db.set_engine_state(con, "last_ninjatrader_quote_id", str(latest_id))
                deleted = db.prune_market_quotes(con)
                retained = con.execute(
                    "SELECT COUNT(*) FROM market_quotes WHERE symbol='NQ'"
                ).fetchone()[0]
                lifetime = con.execute(
                    "SELECT total_quotes FROM quote_counters WHERE symbol='NQ'"
                ).fetchone()[0]
                con.close()
                self.assertGreaterEqual(deleted["NQ"], 200)
                self.assertLessEqual(retained, 1000)
                self.assertEqual(lifetime, 1200)
        finally:
            db.DB_PATH = old_path
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_main_prunes_on_startup(self):
        text = (ROOT / "src/main.py").read_text()
        self.assertIn("prune_market_quotes(connection)", text)
        self.assertIn("Raw quote retention pruned", text)


if __name__ == "__main__":
    unittest.main()
