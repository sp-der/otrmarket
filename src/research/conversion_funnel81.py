from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.risk.evaluation import _session_bucket


NY = ZoneInfo("America/New_York")


def _table_exists(connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (name,),
        ).fetchone()
    )


def _parse_time(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reference_time(connection) -> datetime:
    values: list[datetime] = []
    if _table_exists(connection, "market_quotes"):
        try:
            row = connection.execute(
                "SELECT received_at FROM market_quotes WHERE symbol='GC' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            parsed = _parse_time(row[0] if row else None)
            if parsed:
                values.append(parsed)
        except Exception:
            pass
    if _table_exists(connection, "decision_traces_80"):
        row = connection.execute(
            "SELECT created_at FROM decision_traces_80 WHERE symbol='GC' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        parsed = _parse_time(row[0] if row else None)
        if parsed:
            values.append(parsed)
    if _table_exists(connection, "paper_trades"):
        row = connection.execute(
            "SELECT COALESCE(closed_at,opened_at,updated_at) FROM paper_trades WHERE symbol='GC' "
            "ORDER BY COALESCE(closed_at,opened_at,updated_at) DESC LIMIT 1"
        ).fetchone()
        parsed = _parse_time(row[0] if row else None)
        if parsed:
            values.append(parsed)
    return max(values) if values else datetime.now(timezone.utc)


def _scope(reference: datetime) -> dict:
    session = _session_bucket(reference)
    if session:
        return {
            "kind": "session",
            "name": session["name"],
            "date": session["date"],
            "start_et": session["start_et"],
            "end_et": session["end_et"],
            "reference_time": reference.isoformat(),
        }
    return {
        "kind": "trading_day",
        "name": "TRADING_DAY",
        "date": reference.astimezone(NY).date().isoformat(),
        "start_et": None,
        "end_et": None,
        "reference_time": reference.isoformat(),
    }


def _in_scope(value, scope: dict) -> bool:
    parsed = _parse_time(value)
    if parsed is None:
        return False
    if scope["kind"] == "session":
        bucket = _session_bucket(parsed)
        return bool(
            bucket
            and bucket.get("name") == scope.get("name")
            and bucket.get("date") == scope.get("date")
        )
    return parsed.astimezone(NY).date().isoformat() == scope.get("date")


def _stage(trace: dict, stage_name: str, outcome: str | None = None) -> dict | None:
    for item in trace.get("stages", []) if isinstance(trace, dict) else []:
        if str(item.get("stage") or "").upper() != stage_name.upper():
            continue
        if outcome is not None and str(item.get("outcome") or "").upper() != outcome.upper():
            continue
        return item
    return None


def _last_reason(trace: dict, fallback: str = "") -> str:
    stages = trace.get("stages", []) if isinstance(trace, dict) else []
    if stages:
        return str(stages[-1].get("reason") or fallback)
    return str(fallback or "")


def _dropoff_key(trace_status: str, paper_status: str, result: str, trace: dict) -> str:
    result_u = str(result or "").upper()
    status_u = str(paper_status or "").upper()
    trace_u = str(trace_status or "").upper()
    reason = _last_reason(trace, trace_u).lower()

    if result_u in {"MISSED_EXTENDED", "STALE_MOVE_BEFORE_ENTRY", "MISSED_ENTRY"}:
        return "missed_extended"
    if result_u.startswith("EXPIRED") or "expired" in result_u.lower():
        return "expired_entry"
    if status_u == "PENDING":
        return "waiting_entry"
    if status_u == "OPEN":
        return "open_trade"
    if status_u == "CLOSED":
        return "closed_trade"
    if trace_u == "SESSION_BLOCKED":
        return "session_blocked"
    if trace_u == "ARBITER_BLOCKED":
        return "arbiter_blocked"
    if trace_u == "GUARD_BLOCKED":
        return "guard_blocked"
    if trace_u == "RISK_REJECTED":
        return "risk_rejected"
    if trace_u == "QUALITY_BLOCKED":
        if "r:r" in reason or "rr" in reason or "offers only" in reason:
            return "rr_blocked"
        if "context" in reason or "countertrend" in reason or "narrative" in reason:
            return "context_blocked"
        if "fvg" in reason or "ote" in reason or "entry" in reason or "chase" in reason:
            return "entry_geometry_blocked"
        return "quality_blocked"
    return trace_u.lower() or result_u.lower() or status_u.lower() or "other"


def _blank_slice(label: str) -> dict:
    return {
        "label": label,
        "detected": 0,
        "qualified": 0,
        "selected": 0,
        "registered": 0,
        "filled": 0,
        "closed": 0,
        "wins": 0,
        "losses": 0,
    }


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * float(numerator) / float(denominator), 1)


def conversion_funnel81(connection, *, symbol: str = "GC", limit: int = 5000) -> dict:
    """Measure the real candidate-to-fill conversion chain for Operation 8.1.

    decision_traces_80 is the strategy-side source of truth. paper_trades is
    joined to it so a trace that originally finished as PENDING is later counted
    as filled/closed/expired according to the executor's current state.
    """
    symbol = str(symbol or "GC").upper()
    reference = _reference_time(connection)
    scope = _scope(reference)
    empty = {
        "profile": "GOLD_EXECUTION_CONVERSION_8_1",
        "symbol": symbol,
        "scope": scope,
        "funnel": _blank_slice(symbol),
        "conversion": {},
        "dropoffs": {},
        "by_timeframe": [],
        "by_strategy": [],
        "latest_dropoffs": [],
    }
    if not _table_exists(connection, "decision_traces_80"):
        return empty

    paper_join = _table_exists(connection, "paper_trades")
    if paper_join:
        rows = connection.execute(
            """
            SELECT d.setup_id,d.timeframe,d.strategy,d.final_status,d.trace_json,d.created_at,
                   p.status,p.opened_at,p.closed_at,p.result,p.result_r,p.result_dollars,p.risk_dollars
            FROM decision_traces_80 d
            LEFT JOIN paper_trades p ON p.setup_id=d.setup_id
            WHERE d.symbol=?
            ORDER BY d.created_at DESC
            LIMIT ?
            """,
            (symbol, int(limit)),
        ).fetchall()
    else:
        rows = [tuple(row) + (None,) * 7 for row in connection.execute(
            """
            SELECT setup_id,timeframe,strategy,final_status,trace_json,created_at
            FROM decision_traces_80
            WHERE symbol=?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (symbol, int(limit)),
        ).fetchall()]

    funnel = _blank_slice(symbol)
    by_tf: dict[str, dict] = {}
    by_strategy: dict[str, dict] = {}
    dropoffs: Counter = Counter()
    latest_dropoffs: list[dict] = []

    for row in rows:
        (
            setup_id, timeframe, strategy, final_status, trace_json, created_at,
            paper_status, opened_at, closed_at, result, result_r, result_dollars, risk_dollars,
        ) = row
        if not _in_scope(created_at, scope):
            continue
        try:
            trace = json.loads(trace_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            trace = {}

        tf = str(timeframe or "?")
        strategy_name = str(strategy or "UNKNOWN")
        tf_slice = by_tf.setdefault(tf, _blank_slice(tf))
        strategy_slice = by_strategy.setdefault(strategy_name, _blank_slice(strategy_name))
        slices = (funnel, tf_slice, strategy_slice)

        qualified = _stage(trace, "QUALITY", "PASSED") is not None
        selected = _stage(trace, "ARBITER", "SELECTED") is not None
        registered = bool(paper_status is not None)
        filled = bool(opened_at) or str(paper_status or "").upper() in {"OPEN", "CLOSED"}
        closed = str(paper_status or "").upper() == "CLOSED"
        win = closed and str(result or "").upper() == "WIN"
        loss = closed and str(result or "").upper() == "LOSS"

        for item in slices:
            item["detected"] += 1
            item["qualified"] += int(qualified)
            item["selected"] += int(selected)
            item["registered"] += int(registered)
            item["filled"] += int(filled)
            item["closed"] += int(closed)
            item["wins"] += int(win)
            item["losses"] += int(loss)

        key = _dropoff_key(final_status, paper_status, result, trace)
        if key not in {"closed_trade", "open_trade"}:
            dropoffs[key] += 1

        terminal_failure = key not in {"waiting_entry", "open_trade", "closed_trade"}
        if terminal_failure and len(latest_dropoffs) < 12:
            latest_dropoffs.append(
                {
                    "setup_id": str(setup_id),
                    "timeframe": tf,
                    "strategy": strategy_name,
                    "created_at": created_at,
                    "trace_status": str(final_status or ""),
                    "paper_status": str(paper_status or ""),
                    "result": str(result or ""),
                    "dropoff": key,
                    "reason": _last_reason(trace, str(result or final_status or "")),
                    "result_r": result_r,
                    "result_dollars": result_dollars,
                    "risk_dollars": risk_dollars,
                }
            )

    conversion = {
        "detected_to_qualified_pct": _percent(funnel["qualified"], funnel["detected"]),
        "qualified_to_selected_pct": _percent(funnel["selected"], funnel["qualified"]),
        "selected_to_registered_pct": _percent(funnel["registered"], funnel["selected"]),
        "registered_to_fill_pct": _percent(funnel["filled"], funnel["registered"]),
        "selected_to_fill_pct": _percent(funnel["filled"], funnel["selected"]),
        "candidate_to_fill_pct": _percent(funnel["filled"], funnel["detected"]),
    }

    return {
        "profile": "GOLD_EXECUTION_CONVERSION_8_1",
        "symbol": symbol,
        "scope": scope,
        "funnel": funnel,
        "conversion": conversion,
        "dropoffs": dict(dropoffs.most_common()),
        "by_timeframe": sorted(by_tf.values(), key=lambda item: item["label"]),
        "by_strategy": sorted(by_strategy.values(), key=lambda item: (-item["detected"], item["label"])),
        "latest_dropoffs": latest_dropoffs,
    }
