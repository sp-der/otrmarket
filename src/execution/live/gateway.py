from __future__ import annotations

import sqlite3

from src.storage.database import get_connection

from .config import ExecutionConfig
from .models import ExecutionMode
from .safety import evaluate_intent
from .sizing import build_execution_intent
from .store import enqueue_intent, ensure_schema, record_audit


def _signal_contract(connection: sqlite3.Connection, symbol: str) -> str:
    try:
        row = connection.execute(
            """
            SELECT source FROM market_quotes
            WHERE symbol = ? AND source LIKE 'ninjatrader:%'
            ORDER BY id DESC LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    except sqlite3.Error:
        row = None
    if not row or not row[0]:
        return ""
    source = str(row[0])
    return source.split(":", 1)[1].strip() if ":" in source else ""


class ExecutionGateway:
    """Mirror approved OTR setups into a fail-closed broker command queue."""

    def __init__(self, config: ExecutionConfig | None = None):
        self.config = config or ExecutionConfig.from_env()

    def handle_approved_setup(self, setup, *, risk_dollars: float | None, guard_reason: str | None = None, connection: sqlite3.Connection | None = None) -> dict:
        owns_connection = connection is None
        connection = connection or get_connection()
        try:
            ensure_schema(connection)
            signal_contract = _signal_contract(connection, setup.symbol)

            if self.config.mode == ExecutionMode.PAPER:
                result = {"allowed": False, "code": "PAPER_ONLY", "reason": "Operation 7.2 broker gateway is present but locked in PAPER mode.", "mode": self.config.mode.value}
                record_audit(connection, str(setup.setup_id), result["code"], result["reason"], {"mode": self.config.mode.value, "risk_dollars": float(risk_dollars or 0.0), "guard_reason": guard_reason})
                return result

            try:
                intent = build_execution_intent(setup, risk_dollars=float(risk_dollars or 0.0), config=self.config, signal_contract=signal_contract)
            except ValueError as exc:
                result = {"allowed": False, "code": "INTENT_REJECTED", "reason": str(exc), "mode": self.config.mode.value}
                record_audit(connection, str(setup.setup_id), result["code"], result["reason"], result)
                return result

            decision = evaluate_intent(connection, intent, self.config)
            audit_payload = {"decision": decision.to_dict(), "intent": intent.to_dict(), "guard_reason": guard_reason}
            if not decision.allowed:
                record_audit(connection, str(setup.setup_id), decision.code, decision.reason, audit_payload)
                return {"allowed": False, "code": decision.code, "reason": decision.reason, "mode": self.config.mode.value}

            command = enqueue_intent(connection, intent)
            record_audit(connection, str(setup.setup_id), "QUEUED", "Approved setup was queued for the broker bridge.", audit_payload)
            return {
                "allowed": True,
                "code": "QUEUED",
                "reason": "Approved setup queued for broker bridge.",
                "mode": self.config.mode.value,
                "command_id": command.get("command_id"),
                "execution_contract": intent.execution_contract,
                "quantity": intent.quantity,
                "risk_dollars": intent.risk_dollars,
            }
        finally:
            if owns_connection:
                connection.close()
