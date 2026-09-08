from __future__ import annotations

import sqlite3
import unittest

from src.dashboard import app as dashboard
from src.dashboard import server_81
from src.integrations.nautilus_shadow.ledger import ensure_parity_ledger


class NautilusParitySurface81Tests(unittest.TestCase):
    def test_parity_surface_registers_once(self):
        summary_path = "/market/api/otr81/nautilus-parity"
        run_path = "/market/api/otr81/nautilus-parity/run"
        page_path = "/market/nautilus-parity"

        server_81._install_nautilus_parity_api_81()
        server_81._install_nautilus_parity_api_81()

        paths = [getattr(route, "path", None) for route in dashboard.app.routes]
        self.assertEqual(paths.count(summary_path), 1)
        self.assertEqual(paths.count(run_path), 1)
        self.assertEqual(paths.count(page_path), 1)

    def test_parity_snapshot_reports_persisted_matches(self):
        connection = sqlite3.connect(":memory:")
        try:
            ensure_parity_ledger(connection)
            connection.execute(
                """
                INSERT INTO nautilus_shadow_parity (
                    setup_id, observed_at, signal_contract, execution_contract, quantity,
                    paper_risk_dollars, shadow_risk_dollars, matched_trade_path, matched_full,
                    difference_categories_json, difference_details_json,
                    paper_snapshot_json, shadow_snapshot_json, shadow_orders_json, note
                ) VALUES (
                    'gc-test','2026-09-08T04:00:00+00:00','GC DEC26','MGC DEC26',2,
                    40,40,1,1,'[]','[]','{}','{}','[]','clean match'
                )
                """
            )
            connection.commit()
            snapshot = server_81._nautilus_parity_snapshot_81(connection)
            self.assertFalse(snapshot["authoritative"])
            self.assertEqual(snapshot["records"], 1)
            self.assertEqual(snapshot["trade_path_matches"], 1)
            self.assertEqual(snapshot["full_matches"], 1)
            self.assertEqual(snapshot["recent"][0]["execution_contract"], "MGC DEC26")
            self.assertEqual(snapshot["recent"][0]["categories"], [])
        finally:
            connection.close()

    def test_parity_page_states_shadow_only_role(self):
        html = server_81._parity_page_html_81()
        self.assertIn("OTR remains authoritative", html)
        self.assertIn("Run parity", html)
        self.assertIn("/market/api/otr81/nautilus-parity/run", html)


if __name__ == "__main__":
    unittest.main()
