from __future__ import annotations

import json
import os
import sqlite3

from src.storage.database import get_connection, get_engine_state, set_engine_state


RESET_STATE_KEY = "last_evaluation_reset_token"
EXCLUDED_SETUP_IDS_KEY = "eval_reset_excluded_setup_ids_72"
_PATCH_MARKER = "_otr_eval_history72_installed"


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
    rows = connection.execute(
        f"""
        SELECT setup_id, status, opened_at, closed_at, {result_dollars}
        FROM paper_trades
        ORDER BY COALESCE(opened_at, closed_at) ASC
        """
    ).fetchall()
    return [
        (row[1], row[2], row[3], row[4])
        for row in rows
        if str(row[0]) not in excluded
    ]


def _evaluation_rows_72(self, connection: sqlite3.Connection):  # noqa: ARG001
    columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_trades)").fetchall()}
    if "risk_dollars" not in columns:
        return []
    excluded = excluded_setup_ids(connection)
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
    return [row for row in rows if str(row["setup_id"]) not in excluded]


def install_eval_history_filter() -> None:
    """Install the prior-run filter in both dashboard and strategy processes."""
    import src.risk.operating_mode as operating_mode
    from src.risk.evaluation import EvaluationRiskGuard

    if getattr(operating_mode, _PATCH_MARKER, False):
        return
    operating_mode._trade_rows = _operating_trade_rows_72
    EvaluationRiskGuard._rows = _evaluation_rows_72
    setattr(operating_mode, _PATCH_MARKER, True)
