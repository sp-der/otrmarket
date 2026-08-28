from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any

from .models import CommandStatus, ExecutionIntent, TERMINAL_STATUSES, iso, utc_now


ACTIVE_STATUSES = {
    CommandStatus.PENDING.value,
    CommandStatus.CLAIMED.value,
    CommandStatus.ACKNOWLEDGED.value,
    CommandStatus.WORKING.value,
    CommandStatus.PARTIAL.value,
    CommandStatus.FILLED.value,
}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS execution_commands (
            command_id TEXT PRIMARY KEY,
            setup_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
            account TEXT NOT NULL,
            root_symbol TEXT NOT NULL,
            signal_contract TEXT,
            execution_contract TEXT NOT NULL,
            direction TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            order_type TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            target_price REAL NOT NULL,
            risk_dollars REAL NOT NULL,
            per_contract_risk REAL NOT NULL,
            requested_risk REAL NOT NULL,
            setup_grade TEXT,
            quality_score REAL,
            timeframe TEXT,
            strategy TEXT,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            claimed_at TEXT,
            acknowledged_at TEXT,
            updated_at TEXT NOT NULL,
            broker_order_id TEXT,
            filled_quantity INTEGER NOT NULL DEFAULT 0,
            average_fill_price REAL,
            terminal_reason TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_execution_commands_status
        ON execution_commands(status, expires_at);

        CREATE TABLE IF NOT EXISTS execution_events (
            event_id TEXT PRIMARY KEY,
            command_id TEXT,
            event_type TEXT NOT NULL,
            broker_order_id TEXT,
            quantity INTEGER,
            filled_quantity INTEGER,
            price REAL,
            message TEXT,
            payload_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            FOREIGN KEY(command_id) REFERENCES execution_commands(command_id)
        );

        CREATE INDEX IF NOT EXISTS idx_execution_events_command
        ON execution_events(command_id, occurred_at);

        CREATE TABLE IF NOT EXISTS execution_bridge_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS execution_audits (
            setup_id TEXT PRIMARY KEY,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.commit()


def set_state(connection: sqlite3.Connection, key: str, value: Any, now: datetime | None = None) -> None:
    ensure_schema(connection)
    now = now or utc_now()
    encoded = json.dumps(value, sort_keys=True)
    connection.execute(
        """
        INSERT INTO execution_bridge_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, encoded, iso(now)),
    )
    connection.commit()


def get_state(connection: sqlite3.Connection, key: str, default: Any = None) -> tuple[Any, datetime | None]:
    ensure_schema(connection)
    row = connection.execute(
        "SELECT value, updated_at FROM execution_bridge_state WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return default, None
    try:
        value = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        value = row[0]
    return value, _parse(row[1])


def record_audit(
    connection: sqlite3.Connection,
    setup_id: str,
    decision: str,
    reason: str,
    payload: dict | None = None,
    now: datetime | None = None,
) -> None:
    ensure_schema(connection)
    now = now or utc_now()
    connection.execute(
        """
        INSERT INTO execution_audits(setup_id, decision, reason, payload_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(setup_id) DO UPDATE SET
            decision=excluded.decision,
            reason=excluded.reason,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (
            setup_id,
            decision,
            reason,
            json.dumps(payload or {}, sort_keys=True),
            iso(now),
            iso(now),
        ),
    )
    connection.commit()


def enqueue_intent(connection: sqlite3.Connection, intent: ExecutionIntent) -> dict:
    """Idempotently persist an executable intent.

    setup_id is unique so a strategy retry or process restart cannot create a
    second broker command for the same approved setup.
    """
    ensure_schema(connection)
    now = utc_now()
    connection.execute(
        """
        INSERT OR IGNORE INTO execution_commands(
            command_id, setup_id, status, mode, account, root_symbol,
            signal_contract, execution_contract, direction, side, quantity,
            order_type, entry_price, stop_price, target_price, risk_dollars,
            per_contract_risk, requested_risk, setup_grade, quality_score,
            timeframe, strategy, payload_json, created_at, expires_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent.command_id,
            intent.setup_id,
            CommandStatus.PENDING.value,
            intent.mode,
            intent.account,
            intent.root_symbol,
            intent.signal_contract,
            intent.execution_contract,
            intent.direction,
            intent.side,
            intent.quantity,
            intent.order_type,
            intent.entry_price,
            intent.stop_price,
            intent.target_price,
            intent.risk_dollars,
            intent.per_contract_risk,
            intent.requested_risk,
            intent.setup_grade,
            intent.quality_score,
            intent.timeframe,
            intent.strategy,
            json.dumps(intent.to_dict(), sort_keys=True),
            iso(intent.created_at),
            iso(intent.expires_at),
            iso(now),
        ),
    )
    connection.commit()
    return get_command(connection, intent.command_id) or {}


def _row_dict(cursor: sqlite3.Cursor, row) -> dict:
    columns = [item[0] for item in cursor.description]
    data = dict(zip(columns, row))
    if "payload_json" in data:
        try:
            data["payload"] = json.loads(data.pop("payload_json"))
        except (TypeError, json.JSONDecodeError):
            data["payload"] = {}
    return data


def get_command(connection: sqlite3.Connection, command_id: str) -> dict | None:
    ensure_schema(connection)
    cursor = connection.execute(
        "SELECT * FROM execution_commands WHERE command_id = ?",
        (command_id,),
    )
    row = cursor.fetchone()
    return _row_dict(cursor, row) if row is not None else None


def command_for_setup(connection: sqlite3.Connection, setup_id: str) -> dict | None:
    ensure_schema(connection)
    cursor = connection.execute(
        "SELECT * FROM execution_commands WHERE setup_id = ?",
        (setup_id,),
    )
    row = cursor.fetchone()
    return _row_dict(cursor, row) if row is not None else None


def expire_commands(connection: sqlite3.Connection, now: datetime | None = None) -> int:
    ensure_schema(connection)
    now = now or utc_now()
    cursor = connection.execute(
        """
        UPDATE execution_commands
        SET status = ?, terminal_reason = ?, updated_at = ?
        WHERE status IN (?, ?) AND expires_at < ?
        """,
        (
            CommandStatus.EXPIRED.value,
            "Command TTL expired before broker acknowledgement.",
            iso(now),
            CommandStatus.PENDING.value,
            CommandStatus.CLAIMED.value,
            iso(now),
        ),
    )
    connection.commit()
    return max(0, int(cursor.rowcount or 0))


def poll_commands(
    connection: sqlite3.Connection,
    *,
    account: str,
    mode: str,
    max_items: int = 10,
    redelivery_seconds: int = 5,
    now: datetime | None = None,
) -> list[dict]:
    """Claim pending commands, with bounded redelivery until broker ACK.

    The command_id is deterministic and the Ninja bridge must treat it as an
    idempotency key. Once an ACK event arrives, the command is no longer polled.
    """
    ensure_schema(connection)
    now = now or utc_now()
    expire_commands(connection, now)
    cutoff = now - timedelta(seconds=max(2, redelivery_seconds))
    cursor = connection.execute(
        """
        SELECT * FROM execution_commands
        WHERE account = ? AND mode = ? AND expires_at >= ?
          AND (
            status = ?
            OR (status = ? AND (claimed_at IS NULL OR claimed_at <= ?))
          )
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (
            account,
            mode,
            iso(now),
            CommandStatus.PENDING.value,
            CommandStatus.CLAIMED.value,
            iso(cutoff),
            max(1, min(int(max_items), 100)),
        ),
    )
    rows = cursor.fetchall()
    columns = [item[0] for item in cursor.description]
    results = []
    for row in rows:
        data = dict(zip(columns, row))
        connection.execute(
            """
            UPDATE execution_commands
            SET status = ?, claimed_at = ?, updated_at = ?
            WHERE command_id = ? AND status IN (?, ?)
            """,
            (
                CommandStatus.CLAIMED.value,
                iso(now),
                iso(now),
                data["command_id"],
                CommandStatus.PENDING.value,
                CommandStatus.CLAIMED.value,
            ),
        )
        try:
            payload = json.loads(data["payload_json"])
        except (TypeError, json.JSONDecodeError):
            payload = {}
        payload["status"] = CommandStatus.CLAIMED.value
        results.append(payload)
    connection.commit()
    return results


_EVENT_STATUS = {
    "ACKNOWLEDGED": CommandStatus.ACKNOWLEDGED.value,
    "SUBMITTED": CommandStatus.WORKING.value,
    "WORKING": CommandStatus.WORKING.value,
    "PARTIAL": CommandStatus.PARTIAL.value,
    "FILLED": CommandStatus.FILLED.value,
    "CLOSED": CommandStatus.CLOSED.value,
    "CANCELLED": CommandStatus.CANCELLED.value,
    "REJECTED": CommandStatus.REJECTED.value,
}


def record_event(connection: sqlite3.Connection, event: dict, now: datetime | None = None) -> dict:
    ensure_schema(connection)
    now = now or utc_now()
    event_id = str(event.get("event_id") or "").strip()
    event_type = str(event.get("event_type") or "").strip().upper()
    command_id = str(event.get("command_id") or "").strip() or None
    occurred_at = str(event.get("occurred_at") or iso(now))
    if not event_id:
        raise ValueError("Execution event_id is required for idempotency.")
    if not event_type:
        raise ValueError("Execution event_type is required.")
    if command_id and get_command(connection, command_id) is None:
        raise ValueError(f"Unknown execution command: {command_id}")

    connection.execute(
        """
        INSERT OR IGNORE INTO execution_events(
            event_id, command_id, event_type, broker_order_id, quantity,
            filled_quantity, price, message, payload_json, occurred_at, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            command_id,
            event_type,
            event.get("broker_order_id"),
            event.get("quantity"),
            event.get("filled_quantity"),
            event.get("price"),
            event.get("message"),
            json.dumps(event, sort_keys=True),
            occurred_at,
            iso(now),
        ),
    )

    target_status = _EVENT_STATUS.get(event_type)
    if command_id and target_status:
        fields = ["status = ?", "updated_at = ?"]
        values: list[Any] = [target_status, iso(now)]
        if event_type == "ACKNOWLEDGED":
            fields.append("acknowledged_at = ?")
            values.append(occurred_at)
        if event.get("broker_order_id"):
            fields.append("broker_order_id = ?")
            values.append(str(event["broker_order_id"]))
        if event.get("filled_quantity") is not None:
            fields.append("filled_quantity = ?")
            values.append(int(event["filled_quantity"]))
        if event.get("price") is not None and event_type in {"PARTIAL", "FILLED"}:
            fields.append("average_fill_price = ?")
            values.append(float(event["price"]))
        if target_status in TERMINAL_STATUSES and event.get("message"):
            fields.append("terminal_reason = ?")
            values.append(str(event["message"]))
        values.append(command_id)
        connection.execute(
            f"UPDATE execution_commands SET {', '.join(fields)} WHERE command_id = ?",
            values,
        )
    connection.commit()
    return get_command(connection, command_id) if command_id else {"event_id": event_id}


def record_bridge_snapshot(
    connection: sqlite3.Connection,
    snapshot: dict,
    *,
    configured_account: str,
    now: datetime | None = None,
) -> dict:
    """Persist broker truth and calculate a conservative reconciliation verdict."""
    ensure_schema(connection)
    now = now or utc_now()
    account = str(snapshot.get("account") or "").strip()
    positions = snapshot.get("positions") or []
    orders = snapshot.get("orders") or []

    reasons: list[str] = []
    if not account:
        reasons.append("Broker snapshot did not include an account.")
    elif account != configured_account:
        reasons.append(f"Broker account {account!r} does not match configured account {configured_account!r}.")

    cursor = connection.execute(
        """
        SELECT command_id, execution_contract, direction, quantity, filled_quantity, status
        FROM execution_commands
        WHERE status IN (?, ?, ?, ?, ?)
        """,
        (
            CommandStatus.ACKNOWLEDGED.value,
            CommandStatus.WORKING.value,
            CommandStatus.PARTIAL.value,
            CommandStatus.FILLED.value,
            CommandStatus.CLAIMED.value,
        ),
    )
    local = cursor.fetchall()

    expected_positions: dict[str, int] = {}
    active_command_ids = set()
    for command_id, contract, direction, quantity, filled_quantity, status in local:
        active_command_ids.add(str(command_id))
        if status not in {CommandStatus.PARTIAL.value, CommandStatus.FILLED.value}:
            continue
        qty = int(filled_quantity or quantity or 0)
        signed = qty if str(direction).lower() == "bullish" else -qty
        expected_positions[str(contract).upper()] = expected_positions.get(str(contract).upper(), 0) + signed

    actual_positions: dict[str, int] = {}
    for item in positions:
        instrument = str(item.get("instrument") or item.get("contract") or "").strip().upper()
        if not instrument:
            continue
        qty = int(item.get("quantity") or 0)
        actual_positions[instrument] = actual_positions.get(instrument, 0) + qty

    if actual_positions != expected_positions:
        reasons.append(
            f"Position mismatch: OTR expects {expected_positions or '{}'}; broker reports {actual_positions or '{}'}."
        )

    for item in orders:
        command_id = str(item.get("command_id") or "").strip()
        state = str(item.get("state") or "").strip().upper()
        if state in {"CANCELLED", "CANCELED", "REJECTED", "FILLED"}:
            continue
        if not command_id or command_id not in active_command_ids:
            reasons.append(
                f"Unmatched working broker order {item.get('broker_order_id') or item.get('order_id') or 'unknown'}."
            )

    verdict = {
        "ok": not reasons,
        "account": account,
        "reason": "Broker and OTR execution state agree." if not reasons else " ".join(reasons),
        "positions": positions,
        "orders": orders,
        "bridge_id": snapshot.get("bridge_id"),
        "timestamp": snapshot.get("timestamp") or iso(now),
    }
    set_state(connection, "broker_snapshot", snapshot, now)
    set_state(connection, "reconciliation", verdict, now)
    set_state(connection, "bridge_heartbeat", {"bridge_id": snapshot.get("bridge_id"), "account": account}, now)
    return verdict


def execution_status(connection: sqlite3.Connection) -> dict:
    ensure_schema(connection)
    cursor = connection.execute(
        "SELECT status, COUNT(*) FROM execution_commands GROUP BY status"
    )
    counts = {str(status): int(count) for status, count in cursor.fetchall()}
    reconciliation, reconciliation_at = get_state(connection, "reconciliation", None)
    heartbeat, heartbeat_at = get_state(connection, "bridge_heartbeat", None)
    kill_switch, kill_switch_at = get_state(connection, "kill_switch", False)
    audit = connection.execute(
        """
        SELECT setup_id, decision, reason, updated_at
        FROM execution_audits ORDER BY updated_at DESC LIMIT 1
        """
    ).fetchone()
    return {
        "commands": counts,
        "active_commands": sum(counts.get(status, 0) for status in ACTIVE_STATUSES),
        "reconciliation": reconciliation,
        "reconciliation_at": iso(reconciliation_at),
        "bridge_heartbeat": heartbeat,
        "bridge_heartbeat_at": iso(heartbeat_at),
        "kill_switch": bool(kill_switch),
        "kill_switch_at": iso(kill_switch_at),
        "latest_audit": (
            {
                "setup_id": audit[0],
                "decision": audit[1],
                "reason": audit[2],
                "updated_at": audit[3],
            }
            if audit else None
        ),
    }
