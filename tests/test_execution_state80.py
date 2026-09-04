from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from src.execution.live.models import ExecutionIntent
from src.execution.live.store import enqueue_intent, get_command, record_bridge_snapshot, record_event
from src.execution.state_machine80 import resolve_transition80


class ExecutionState80Tests(unittest.TestCase):
    def _intent(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
        return ExecutionIntent(
            command_id="cmd80",
            setup_id="setup80",
            mode="SIM_BRIDGE",
            account="Sim101",
            root_symbol="GC",
            signal_contract="GC 12-26",
            execution_contract="MGC 12-26",
            direction="bullish",
            side="BUY",
            quantity=1,
            order_type="LIMIT",
            entry_price=3500.0,
            stop_price=3498.0,
            target_price=3504.0,
            risk_dollars=20.0,
            per_contract_risk=20.0,
            requested_risk=20.0,
            setup_grade="A",
            quality_score=85.0,
            timeframe="5m",
            strategy="ICT_CONFLUENCE",
            created_at=now,
            expires_at=now + timedelta(minutes=5),
        )

    def test_forward_and_stale_transitions(self):
        self.assertTrue(resolve_transition80("CLAIMED", "WORKING").apply)
        self.assertTrue(resolve_transition80("WORKING", "FILLED").apply)
        stale = resolve_transition80("FILLED", "WORKING")
        self.assertFalse(stale.apply)
        self.assertTrue(stale.stale)
        self.assertFalse(resolve_transition80("CLOSED", "FILLED").apply)
        self.assertTrue(resolve_transition80("FILLED", "REJECTED").apply)

    def test_out_of_order_event_cannot_regress_fill(self):
        connection = sqlite3.connect(":memory:")
        try:
            enqueue_intent(connection, self._intent())
            record_event(connection, {"event_id": "e1", "command_id": "cmd80", "event_type": "FILLED", "filled_quantity": 1, "price": 3500.0})
            record_event(connection, {"event_id": "e2", "command_id": "cmd80", "event_type": "WORKING"})
            self.assertEqual(get_command(connection, "cmd80")["status"], "FILLED")
            # Exact event retry is idempotent too.
            record_event(connection, {"event_id": "e1", "command_id": "cmd80", "event_type": "FILLED", "filled_quantity": 1, "price": 3500.0})
            self.assertEqual(get_command(connection, "cmd80")["status"], "FILLED")
        finally:
            connection.close()

    def test_filled_position_requires_protective_stop(self):
        connection = sqlite3.connect(":memory:")
        try:
            enqueue_intent(connection, self._intent())
            record_event(connection, {"event_id": "fill", "command_id": "cmd80", "event_type": "FILLED", "filled_quantity": 1, "price": 3500.0})
            naked = record_bridge_snapshot(
                connection,
                {
                    "bridge_id": "nt8-test",
                    "account": "Sim101",
                    "positions": [{"instrument": "MGC 12-26", "quantity": 1}],
                    "orders": [],
                },
                configured_account="Sim101",
            )
            self.assertFalse(naked["ok"])
            self.assertIn("protective stop", naked["reason"].lower())

            protected = record_bridge_snapshot(
                connection,
                {
                    "bridge_id": "nt8-test",
                    "account": "Sim101",
                    "positions": [{"instrument": "MGC 12-26", "quantity": 1}],
                    "orders": [{
                        "command_id": "cmd80",
                        "name": "OTR72|cmd80|S",
                        "state": "Working",
                        "order_type": "StopMarket",
                        "broker_order_id": "stop1",
                    }],
                },
                configured_account="Sim101",
            )
            self.assertTrue(protected["ok"], protected["reason"])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
