from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
import sqlite3
import sys

from src.storage.database import get_connection, get_engine_state, set_engine_state


RESET_STATE_KEY = "last_evaluation_reset_token"
EXCLUDED_SETUP_IDS_KEY = "eval_reset_excluded_setup_ids_72"
_PATCH_MARKER = "_otr_eval_history72_installed"
_REFERENCE_TIME_72: ContextVar[datetime | None] = ContextVar(
    "otr_eval_reference_time_72",
    default=None,
)


def _decode_ids(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        values = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if str(value).strip()}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_reference_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def excluded_setup_ids(connection: sqlite3.Connection) -> set[str]:
    return _decode_ids(get_engine_state(connection, EXCLUDED_SETUP_IDS_KEY, "[]"))


def _current_trade_ids(connection: sqlite3.Connection) -> set[str]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_trades'"
    ).fetchone()
    if not exists:
        return set()
    return {
        str(row[0])
        for row in connection.execute("SELECT setup_id FROM paper_trades").fetchall()
        if row[0]
    }


def apply_nondestructive_eval_reset() -> bool:
    """Start a fresh eval accounting run without deleting prior trade history.

    The legacy supervisor reset deleted paper/setup/intelligence rows. Operation
    7.2G instead snapshots existing paper setup IDs into engine_state. Risk and
    operating-mode readers ignore those IDs for the new run while dashboard
    trade history remains intact. Live scanner diagnostics are safe to clear
    because they are transient state rather than historical results.

    A reset refuses to archive an OPEN/PENDING paper position. In that case the
    reset token is suppressed for this boot so the legacy destructive hook can
    never run accidentally.
    """
    token = os.getenv("OTR_RESET_EVAL_TOKEN", "").strip()
    if not token:
        return False

    connection = get_connection()
    try:
        previous = get_engine_state(connection, RESET_STATE_KEY, "") or ""
        if previous == token:
            print(
                "Fresh-eval reset token already applied; preserving trade history and current counters",
                flush=True,
            )
            return False

        active_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM paper_trades WHERE status IN ('PENDING','OPEN')"
            ).fetchone()[0]
        )
        if active_count:
            print(
                "NON-DESTRUCTIVE EVAL RESET SKIPPED: "
                f"{active_count} OPEN/PENDING paper position(s) must be resolved first; history was not changed.",
                flush=True,
            )
            return False

        prior_ids = excluded_setup_ids(connection)
        current_ids = _current_trade_ids(connection)
        merged_ids = prior_ids | current_ids
        set_engine_state(connection, EXCLUDED_SETUP_IDS_KEY, json.dumps(sorted(merged_ids)))

        diagnostics = int(
            connection.execute("SELECT COUNT(*) FROM strategy_diagnostics").fetchone()[0]
        )
        connection.execute("DELETE FROM strategy_diagnostics")
        connection.commit()
        set_engine_state(connection, RESET_STATE_KEY, token)

        print(
            "Fresh evaluation counter reset complete: "
            f"{len(current_ids)} existing trade(s) preserved as prior-run history; "
            f"{len(merged_ids)} total prior-run setup id(s) excluded from new eval counters; "
            f"{diagnostics} live scanner row(s) cleared. "
            "Trade/setup/intelligence/shadow history preserved.",
            flush=True,
        )
        return True
    finally:
        # server.py still contains the older destructive reset hook. Blank the
        # inherited token before runpy reaches it, even if this reset was skipped.
        os.environ["OTR_RESET_EVAL_TOKEN"] = ""
        connection.close()


def _operating_trade_rows_72(connection: sqlite3.Connection):
    columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    if not columns:
        return []
    result_dollars = "result_dollars" if "result_dollars" in columns else "NULL AS result_dollars"
    excluded = excluded_setup_ids(connection)
    reference = _REFERENCE_TIME_72.get()
    rows = connection.execute(
        f"""
        SELECT setup_id, status, opened_at, closed_at, {result_dollars}
        FROM paper_trades
        ORDER BY COALESCE(opened_at, closed_at) ASC
        """
    ).fetchall()

    visible = []
    for row in rows:
        if str(row[0]) in excluded:
            continue
        status, opened_value, closed_value, dollars = row[1], row[2], row[3], row[4]
        opened = _parse_dt(opened_value)
        closed = _parse_dt(closed_value)
        first_seen = opened or closed
        if reference is not None and first_seen is not None and first_seen > reference:
            continue
        if (
            reference is not None
            and status == "CLOSED"
            and opened is not None
            and opened <= reference
            and closed is not None
            and closed > reference
        ):
            visible.append(("OPEN", opened_value, None, None))
            continue
        visible.append((status, opened_value, closed_value, dollars))
    return visible


def _evaluation_rows_72(self, connection: sqlite3.Connection):  # noqa: ARG001
    columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    if "risk_dollars" not in columns:
        return []
    excluded = excluded_setup_ids(connection)
    reference = _REFERENCE_TIME_72.get()
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT setup_id, status, opened_at, closed_at, result,
               result_r, risk_dollars, result_dollars, updated_at
        FROM paper_trades
        WHERE risk_dollars IS NOT NULL AND risk_dollars > 0
        ORDER BY COALESCE(closed_at, opened_at, updated_at) ASC
        """
    ).fetchall()

    visible = []
    for row in rows:
        if str(row["setup_id"]) in excluded:
            continue
        data = dict(row)
        opened = _parse_dt(data.get("opened_at"))
        closed = _parse_dt(data.get("closed_at"))
        updated = _parse_dt(data.get("updated_at"))
        first_seen = opened or updated or closed
        if reference is not None and first_seen is not None and first_seen > reference:
            continue
        if (
            reference is not None
            and data.get("status") == "CLOSED"
            and opened is not None
            and opened <= reference
            and closed is not None
            and closed > reference
        ):
            data["status"] = "OPEN"
            data["closed_at"] = None
            data["result"] = None
            data["result_r"] = None
            data["result_dollars"] = None
        visible.append(data)
    return visible


def _session_day_stats_72(connection: sqlite3.Connection, reference_time: datetime, tz) -> dict:
    """Session-consistency stats as they existed at the replay timestamp."""
    reference_time = _normalize_reference_time(reference_time) or datetime.now(timezone.utc)
    current_day = reference_time.astimezone(tz).date()
    connection.row_factory = sqlite3.Row
    columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    if not columns:
        return {"trades": 0, "losses": 0, "wins": 0, "realized_pnl": 0.0}

    result_dollars_expr = "result_dollars" if "result_dollars" in columns else "NULL AS result_dollars"
    excluded = excluded_setup_ids(connection)
    rows = connection.execute(
        f"""
        SELECT setup_id, status, result, opened_at, closed_at, {result_dollars_expr}
        FROM paper_trades
        ORDER BY COALESCE(closed_at, opened_at) ASC
        """
    ).fetchall()

    trades = 0
    losses = 0
    wins = 0
    realized = 0.0
    for row in rows:
        if str(row["setup_id"]) in excluded:
            continue
        opened_at = _parse_dt(row["opened_at"])
        if (
            opened_at
            and opened_at <= reference_time
            and opened_at.astimezone(tz).date() == current_day
        ):
            trades += 1

        closed_at = _parse_dt(row["closed_at"])
        if (
            not closed_at
            or closed_at > reference_time
            or closed_at.astimezone(tz).date() != current_day
            or row["status"] != "CLOSED"
        ):
            continue
        result = str(row["result"] or "").upper()
        losses += int(result == "LOSS")
        wins += int(result == "WIN")
        realized += float(row["result_dollars"] or 0.0)

    return {
        "trades": trades,
        "losses": losses,
        "wins": wins,
        "realized_pnl": realized,
    }


def install_eval_history_filter() -> None:
    """Install prior-run and replay-time filters in dashboard/strategy processes."""
    import src.risk.operating_mode as operating_mode
    import src.risk.session_consistency as session_consistency
    from src.risk.evaluation import EvaluationRiskGuard

    if getattr(operating_mode, _PATCH_MARKER, False):
        return

    operating_mode._trade_rows = _operating_trade_rows_72
    session_consistency._day_stats = _session_day_stats_72
    EvaluationRiskGuard._rows = _evaluation_rows_72

    original_snapshot = EvaluationRiskGuard.snapshot

    def replay_time_snapshot(self, connection, reference_time=None):
        reference = _normalize_reference_time(reference_time)
        token = _REFERENCE_TIME_72.set(reference)
        try:
            return original_snapshot(self, connection, reference_time)
        finally:
            _REFERENCE_TIME_72.reset(token)

    EvaluationRiskGuard.snapshot = replay_time_snapshot

    original_operating_mode = operating_mode.evaluate_operating_mode

    def replay_time_operating_mode(connection, setup, config=None):
        reference = _normalize_reference_time(getattr(setup, "created_at", None))
        token = _REFERENCE_TIME_72.set(reference)
        try:
            return original_operating_mode(connection, setup, config)
        finally:
            _REFERENCE_TIME_72.reset(token)

    operating_mode.evaluate_operating_mode = replay_time_operating_mode
    loaded_main_71 = sys.modules.get("src.main_71")
    if (
        loaded_main_71 is not None
        and getattr(loaded_main_71, "evaluate_operating_mode", None) is original_operating_mode
    ):
        loaded_main_71.evaluate_operating_mode = replay_time_operating_mode

    setattr(operating_mode, _PATCH_MARKER, True)
