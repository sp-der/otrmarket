from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from typing import Any

from src.execution.state_machine80 import resolve_transition80

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
    """Claim pending commands, with bounded redelivery until broker ACK."""
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
        # Conditional claim: the WHERE clause repeats the eligibility test so
        # two concurrent pollers cannot both win the same command inside one
        # redelivery window. Only the poller whose UPDATE changed the row may
        # deliver it.
        claimed = connection.execute(
            """
            UPDATE execution_commands
            SET status = ?, claimed_at = ?, updated_at = ?
            WHERE command_id = ?
              AND (
                status = ?
                OR (status = ? AND (claimed_at IS NULL OR claimed_at <= ?))
              )
            """,
            (
                CommandStatus.CLAIMED.value,
                iso(now),
                iso(now),
                data["command_id"],
                CommandStatus.PENDING.value,
                CommandStatus.CLAIMED.value,
                iso(cutoff),
            ),
        )
        if int(claimed.rowcount or 0) != 1:
            continue
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
    """Record one idempotent broker event without allowing state regression."""
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

    # Lost HTTP responses can cause an event batch to be retried. An already
    # recorded event must not mutate the command a second time.
    if connection.execute("SELECT 1 FROM execution_events WHERE event_id=?", (event_id,)).fetchone():
        return get_command(connection, command_id) if command_id else {"event_id": event_id, "duplicate": True}

    command = get_command(connection, command_id) if command_id else None
    if command_id and command is None:
        raise ValueError(f"Unknown execution command: {command_id}")

    target_status = _EVENT_STATUS.get(event_type)
    transition = None
    if command_id and target_status:
        transition = resolve_transition80(str(command.get("status") or ""), target_status)
        event = dict(event)
        metadata = dict(event.get("metadata") or {})
        metadata["transition80"] = {
            "current": transition.current,
            "target": transition.target,
            "apply": transition.apply,
            "duplicate": transition.duplicate,
            "stale": transition.stale,
            "reason": transition.reason,
        }
        event["metadata"] = metadata

    connection.execute(
        """
        INSERT INTO execution_events(
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

    if command_id and target_status and transition and transition.apply:
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
    result = get_command(connection, command_id) if command_id else {"event_id": event_id}
    if isinstance(result, dict) and transition is not None:
        result["transition80"] = {
            "applied": transition.apply,
            "stale": transition.stale,
            "reason": transition.reason,
        }
    return result


def _working_order(item: dict) -> bool:
    state = str(item.get("state") or "").strip().upper()
    return state not in {"CANCELLED", "CANCELED", "REJECTED", "FILLED", "EXPIRED"}


def _is_protective_stop(item: dict, command_id: str) -> bool:
    if str(item.get("command_id") or "").strip() != command_id or not _working_order(item):
        return False
    name = str(item.get("name") or "")
    order_type = str(item.get("order_type") or "").upper()
    return name.endswith("|S") or "STOP" in order_type


def record_bridge_snapshot(
    connection: sqlite3.Connection,
    snapshot: dict,
    *,
    configured_account: str,
    now: datetime | None = None,
) -> dict:
    """Persist broker truth and calculate a fail-closed reconciliation verdict."""
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
    filled_commands: list[tuple[str, str]] = []
    for command_id, contract, direction, quantity, filled_quantity, status in local:
        command_id = str(command_id)
        active_command_ids.add(command_id)
        if status not in {CommandStatus.PARTIAL.value, CommandStatus.FILLED.value}:
            continue
        qty = int(filled_quantity or quantity or 0)
        signed = qty if str(direction).lower() == "bullish" else -qty
        contract_key = str(contract).upper()
        expected_positions[contract_key] = expected_positions.get(contract_key, 0) + signed
        if qty:
            filled_commands.append((command_id, contract_key))

    actual_positions: dict[str, int] = {}
    for item in positions:
        instrument = str(item.get("instrument") or item.get("contract") or "").strip().upper()
        if not instrument:
            continue
        qty = int(item.get("quantity") or 0)
        actual_positions[instrument] = actual_positions.get(instrument, 0) + qty

    if actual_positions != expected_positions:
        reasons.append(
            f"Position mismatch: OTR expects {expected_positions or '{}'}; broker reports {actual_positions or '{}'} .".replace("{} .", "{}.")
        )

    for item in orders:
        if not _working_order(item):
            continue
        command_id = str(item.get("command_id") or "").strip()
        if not command_id or command_id not in active_command_ids:
            reasons.append(
                f"Unmatched working broker order {item.get('broker_order_id') or item.get('order_id') or 'unknown'}."
            )

    protection = {}
    for command_id, contract in filled_commands:
        protected = any(_is_protective_stop(item, command_id) for item in orders)
        protection[command_id] = {"contract": contract, "stop_confirmed": protected}
        if not protected:
            reasons.append(
                f"Protection mismatch: filled command {command_id} has broker exposure but no working protective stop."
            )

    verdict = {
        "ok": not reasons,
        "account": account,
        "reason": "Broker and OTR execution state agree, including protective stops." if not reasons else " ".join(reasons),
        "positions": positions,
        "orders": orders,
        "protection": protection,
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


# ---------------------------------------------------------------------------
# Pre-live audit hardening: broker-ledger risk state, data freshness and
# cancel directives. Everything here is derived from persisted rows, so a
# process restart, dashboard refresh or bridge reconnect cannot reset it.
# ---------------------------------------------------------------------------

UNFILLED_BROKER_STATUSES = {
    CommandStatus.CLAIMED.value,
    CommandStatus.ACKNOWLEDGED.value,
    CommandStatus.WORKING.value,
}


def trading_day_start(now: datetime, *, tz_name: str = "America/New_York", roll_hour: int = 18) -> datetime:
    """Start of the CME-style trading day containing ``now`` (UTC result).

    Futures trading days roll at 18:00 New York time, so 19:00 ET Sunday and
    10:00 ET Monday belong to the same trading day. This is OTR's internal
    execution-day convention, not a verified prop-firm reset rule.
    """
    from zoneinfo import ZoneInfo

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(ZoneInfo(tz_name))
    start = local.replace(hour=roll_hour, minute=0, second=0, microsecond=0)
    if local < start:
        start -= timedelta(days=1)
    return start.astimezone(timezone.utc)


def _point_value(root_symbol: str) -> float | None:
    try:
        from src.research.execution.contracts import micro_spec

        return float(micro_spec(root_symbol).point_value)
    except (ValueError, KeyError):
        return None


def _exit_prices(connection: sqlite3.Connection, command_ids: list[str]) -> dict[str, float]:
    if not command_ids:
        return {}
    placeholders = ",".join("?" for _ in command_ids)
    rows = connection.execute(
        f"""
        SELECT command_id, price FROM execution_events
        WHERE event_type = 'CLOSED' AND price IS NOT NULL AND command_id IN ({placeholders})
        ORDER BY occurred_at ASC
        """,
        command_ids,
    ).fetchall()
    return {str(command_id): float(price) for command_id, price in rows}


def _command_day_time(row, replay_clock: bool) -> datetime | None:
    if replay_clock:
        try:
            payload = json.loads(row[9] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        event_at = _parse((payload.get("metadata") or {}).get("signal_event_at"))
        if event_at is not None:
            return event_at
    return _parse(row[8])


def daily_execution_ledger(
    connection: sqlite3.Connection,
    *,
    account: str,
    now: datetime | None = None,
    tz_name: str = "America/New_York",
    roll_hour: int = 18,
    commission_per_contract_rt: float = 0.0,
    replay_clock: bool = False,
) -> dict:
    """Broker-side daily risk state for one account.

    * realized_pnl: net P/L of commands closed by a broker-reported exit fill.
    * unresolved_loss: commands with broker exposure whose exit price is not
      known (e.g. protective-order rejection, missing CLOSED price). Each one
      is charged its full planned risk -- never assumed flat or profitable.
    * open_risk: planned risk of every command that is still active (pending
      delivery, working at the broker, or filled and protected).
    * trades: commands from this trading day that filled or may still fill.
    """
    ensure_schema(connection)
    now = now or utc_now()
    start = trading_day_start(now, tz_name=tz_name, roll_hour=roll_hour)
    cursor = connection.execute(
        """
        SELECT command_id, status, root_symbol, direction, quantity, filled_quantity,
               average_fill_price, risk_dollars, created_at, payload_json
        FROM execution_commands
        WHERE account = ?
        """,
        (account,),
    )
    rows = [row[:9] + (_command_day_time(row, replay_clock),) for row in cursor.fetchall()]
    # Under the REPLAY clock, ``now`` is the replay stream head and commands
    # are attributed by their signal event time, so a week of replay run in
    # one afternoon is still judged day by day. A command with no usable time
    # is charged to the current day (never silently dropped).
    todays = [row for row in rows if (row[9] or start) >= start]
    closed_ids = [str(row[0]) for row in todays if row[1] == CommandStatus.CLOSED.value]
    exits = _exit_prices(connection, closed_ids)

    realized = 0.0
    unresolved_loss = 0.0
    unresolved: list[str] = []
    trades = 0
    for command_id, status, root, direction, quantity, filled, avg_fill, risk, _created, _day_time in todays:
        command_id = str(command_id)
        filled_qty = int(filled or 0)
        if filled_qty > 0 or status in ACTIVE_STATUSES:
            trades += 1
        if status == CommandStatus.CLOSED.value:
            qty = filled_qty or int(quantity or 0)
            point_value = _point_value(str(root))
            exit_price = exits.get(command_id)
            if avg_fill is None or exit_price is None or point_value is None or qty <= 0:
                unresolved_loss += float(risk or 0.0)
                unresolved.append(command_id)
                continue
            sign = 1.0 if str(direction).lower() == "bullish" else -1.0
            gross = (float(exit_price) - float(avg_fill)) * sign * qty * point_value
            realized += gross - qty * float(commission_per_contract_rt)
        elif status in TERMINAL_STATUSES and filled_qty > 0:
            # e.g. FILLED -> REJECTED (protective bracket refused). Exposure
            # existed; its outcome is unknown, so charge the full risk.
            unresolved_loss += float(risk or 0.0)
            unresolved.append(command_id)

    # Open risk is account-wide, not just today's: a command left working from
    # an earlier session still represents live exposure.
    active = [row for row in rows if row[1] in ACTIVE_STATUSES]
    open_risk = sum(float(row[7] or 0.0) for row in active)
    loss_used = max(0.0, -realized) + unresolved_loss
    return {
        "trading_day_start": iso(start),
        "realized_pnl": round(realized, 2),
        "unresolved_loss": round(unresolved_loss, 2),
        "unresolved_command_ids": unresolved,
        "loss_used": round(loss_used, 2),
        "open_risk": round(open_risk, 2),
        "active_commands": len(active),
        "trades": trades,
    }


def latest_market_data_at(connection: sqlite3.Connection, symbol: str) -> datetime | None:
    """Server-side receipt time of the newest quote for ``symbol``.

    ``ingested_at`` is stamped with the server wall clock on insert, so it
    measures bridge liveness even during Market Replay (whose exchange
    timestamps are historical).
    """
    for column in ("ingested_at", "received_at"):
        try:
            row = connection.execute(
                f"SELECT MAX({column}) FROM market_quotes WHERE symbol = ?",
                (str(symbol).upper(),),
            ).fetchone()
        except sqlite3.Error:
            continue
        if row and row[0]:
            return _parse(str(row[0]))
    return None


def latest_market_event_at(connection: sqlite3.Connection, symbol: str) -> datetime | None:
    """Exchange/NinjaTrader timestamp of the most recently inserted quote.

    Compared with the wall clock this detects a backlog being replayed after
    an engine restart, Market Replay data, or a bridge clock/timezone fault --
    all cases where a signal is being generated from prices that are not
    current.
    """
    try:
        row = connection.execute(
            "SELECT received_at FROM market_quotes WHERE symbol = ? ORDER BY rowid DESC LIMIT 1",
            (str(symbol).upper(),),
        ).fetchone()
    except sqlite3.Error:
        return None
    return _parse(str(row[0])) if row and row[0] else None


def recent_market_event_max(connection: sqlite3.Connection, symbol: str, window: int = 50) -> datetime | None:
    """Latest event time among the last ``window`` quotes received for ``symbol``.

    If the newest quote is materially older than this, the feed went
    backwards: an out-of-order delivery or a Market Replay rewind.
    """
    try:
        rows = connection.execute(
            "SELECT received_at FROM market_quotes WHERE symbol = ? ORDER BY rowid DESC LIMIT ?",
            (str(symbol).upper(), int(window)),
        ).fetchall()
    except sqlite3.Error:
        return None
    parsed = [value for value in (_parse(str(row[0])) for row in rows if row and row[0]) if value is not None]
    return max(parsed) if parsed else None


def cancel_pending_commands(connection: sqlite3.Connection, reason: str, now: datetime | None = None) -> int:
    """Terminally cancel commands that have not been delivered to the broker."""
    ensure_schema(connection)
    now = now or utc_now()
    cursor = connection.execute(
        """
        UPDATE execution_commands
        SET status = ?, terminal_reason = ?, updated_at = ?
        WHERE status = ?
        """,
        (CommandStatus.CANCELLED.value, reason, iso(now), CommandStatus.PENDING.value),
    )
    connection.commit()
    return max(0, int(cursor.rowcount or 0))


def cancel_directives(
    connection: sqlite3.Connection,
    *,
    account: str,
    mode: str,
    kill_switch: bool,
    now: datetime | None = None,
    replay_clock: bool = False,
) -> list[dict]:
    """Unfilled broker entry orders the bridge must cancel.

    An entry is cancelled when the kill switch is active, or when the
    approved setup's pending-entry lifetime (``entry_expires_at``) has passed.
    Without this, a resting DAY limit order outlives the setup it came from
    and can fill hours later against a stale thesis. Filled commands are
    never listed: their protective bracket must stay in place.
    """
    ensure_schema(connection)
    now = now or utc_now()
    placeholders = ",".join("?" for _ in UNFILLED_BROKER_STATUSES)
    rows = connection.execute(
        f"""
        SELECT command_id, payload_json, root_symbol FROM execution_commands
        WHERE account = ? AND mode = ? AND COALESCE(filled_quantity, 0) = 0
          AND status IN ({placeholders})
        """,
        (account, mode, *sorted(UNFILLED_BROKER_STATUSES)),
    ).fetchall()
    directives = []
    heads: dict[str, datetime | None] = {}
    for command_id, payload_json, root_symbol in rows:
        reason = None
        if kill_switch:
            reason = "KILL_SWITCH"
        else:
            try:
                payload = json.loads(payload_json or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            metadata = payload.get("metadata") or {}
            if replay_clock:
                # Replay speed is arbitrary, so the entry deadline is judged
                # in replay event time against the stream head.
                root = str(root_symbol or "").upper()
                if root not in heads:
                    heads[root] = latest_market_event_at(connection, root)
                expires = _parse(metadata.get("entry_expires_event_at"))
                reference = heads[root]
            else:
                expires = _parse(metadata.get("entry_expires_at"))
                reference = now
            if expires is not None and reference is not None and expires <= reference:
                reason = "ENTRY_EXPIRED"
        if reason:
            directives.append({"command_id": str(command_id), "reason": reason})
    return directives
