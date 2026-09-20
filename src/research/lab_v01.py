from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.execution.paper import CANNOT_SIZE_RESULT, PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1
from src.research.run_scope import ENGINE_VERSION, OPERATION_VERSION, current_run_id

MIN_EVIDENCE_SAMPLES = 20
LAB_VERSION = "0.1"

# Sentinel default for the `run_id` parameter on trade-scoped queries: "use
# whatever the active run is right now". Pass run_id=None explicitly to pool
# every run (opt-in only -- Research Lab never pools runs implicitly), or a
# specific run_id string to inspect one historical run.
CURRENT_RUN = object()

# Paper order-lifecycle terminal reasons that never reached a fill. See
# src/execution/paper.py for where each one is set.
EXPIRED_BEFORE_ENTRY = "EXPIRED_BEFORE_ENTRY"
STALE_MOVE_BEFORE_ENTRY = "STALE_MOVE_BEFORE_ENTRY"
INVALIDATED_BEFORE_ENTRY = "INVALIDATED_BEFORE_ENTRY"

DEFAULT_HYPOTHESES = (
    (
        "OTR-GC-001",
        "A+ vs A expectancy",
        "Does A+ Gold execution outperform A-tier execution after a meaningful comparable sample?",
        "expectancy_r",
    ),
    (
        "OTR-GC-002",
        "Entry family expectancy",
        "Which Gold entry family produces the best execution-certified expectancy: FVG first touch, OTE, Order Block, or other approved entries?",
        "expectancy_r",
    ),
    (
        "OTR-GC-003",
        "Execution mismatch clustering",
        "Do Nautilus execution-path differences cluster by setup family, timeframe, grade, or regime?",
        "nautilus_path_match_rate",
    ),
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _safe_json(value: Any, fallback: Any = None) -> Any:
    if fallback is None:
        fallback = {}
    if isinstance(value, (dict, list)):
        return value
    try:
        loaded = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return loaded


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    return value if isinstance(value, dict) else {}


def _quality_grade(meta: dict[str, Any]) -> str:
    context = meta.get("a_plus_context", {}) or {}
    if isinstance(context, dict) and context.get("quality_grade"):
        return str(context.get("quality_grade")).upper()
    score = int(meta.get("checklist_score", 0) or 0)
    total = int(meta.get("checklist_total", 0) or 0)
    if meta.get("candidate_source_80") == "EARLY_ARM_72H" and total >= 6:
        return "A" if score >= 5 else "PREVIEW"
    if meta.get("setup_quality") == "A_PLUS_STRUCTURE":
        return "A+"
    return "A" if score >= 5 else "UNKNOWN"


def _regime(meta: dict[str, Any]) -> str:
    value = meta.get("gold_regime_80", {}) or {}
    if isinstance(value, dict) and value.get("regime"):
        return str(value.get("regime")).upper()
    return "UNKNOWN"


def _setup_family(meta: dict[str, Any], trigger_type: str) -> str:
    entry_type = str(meta.get("entry_type") or "").strip().upper()
    if entry_type:
        if "ORDER_BLOCK" in entry_type or entry_type == "OB":
            return "ORDER_BLOCK"
        if "OTE" in entry_type:
            return "OTE"
        if "FVG" in entry_type:
            return "FVG"
        if "BREAKER" in entry_type:
            return "BREAKER"
        return entry_type
    strategy = str(meta.get("strategy") or "").strip().upper()
    if strategy and strategy != "ICT_CONFLUENCE":
        return strategy
    trigger = str(trigger_type or "UNKNOWN").strip().upper()
    return trigger or "UNKNOWN"


def ensure_research_lab81(connection) -> None:
    """Create only additive Research Lab tables. Trading tables are never altered."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS research_hypotheses_v01 (
            hypothesis_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            question TEXT NOT NULL,
            metric TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research_evidence_v01 (
            evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            scope_key TEXT NOT NULL,
            samples INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            breakeven INTEGER NOT NULL,
            win_rate REAL,
            expectancy_r REAL,
            avg_r REAL,
            net_pnl REAL,
            gross_win REAL,
            gross_loss REAL,
            profit_factor REAL,
            max_drawdown REAL,
            parity_samples INTEGER NOT NULL DEFAULT 0,
            parity_path_matches INTEGER NOT NULL DEFAULT 0,
            parity_full_matches INTEGER NOT NULL DEFAULT 0,
            evidence_status TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_research_evidence_v01_scope_time
        ON research_evidence_v01(scope_key, captured_at);

        CREATE TABLE IF NOT EXISTS research_counterfactuals_v01 (
            setup_id TEXT NOT NULL,
            variant TEXT NOT NULL,
            evaluability TEXT NOT NULL,
            reason TEXT NOT NULL,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            planned_rr REAL,
            metadata_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(setup_id, variant)
        );
        """
    )
    now = _utc_iso()
    for hypothesis_id, title, question, metric in DEFAULT_HYPOTHESES:
        connection.execute(
            """
            INSERT OR IGNORE INTO research_hypotheses_v01(
                hypothesis_id,title,question,metric,status,notes,created_at,updated_at
            ) VALUES (?,?,?,?, 'OPEN','',?,?)
            """,
            (hypothesis_id, title, question, metric, now, now),
        )
    connection.commit()


def _latest_parity(connection) -> dict[str, dict[str, Any]]:
    if not _table_exists(connection, "nautilus_shadow_parity"):
        return {}
    rows = connection.execute(
        """
        SELECT n.setup_id,n.observed_at,n.matched_trade_path,n.matched_full,
               n.difference_categories_json,n.note
        FROM nautilus_shadow_parity n
        JOIN (
            SELECT setup_id,MAX(observed_at) AS observed_at
            FROM nautilus_shadow_parity
            GROUP BY setup_id
        ) latest
          ON latest.setup_id=n.setup_id AND latest.observed_at=n.observed_at
        """
    ).fetchall()
    output: dict[str, dict[str, Any]] = {}
    for setup_id, observed_at, path_match, full_match, categories, note in rows:
        output[str(setup_id)] = {
            "observed_at": observed_at,
            "matched_trade_path": bool(path_match),
            "matched_full": bool(full_match),
            "categories": _safe_json(categories, []),
            "note": str(note or ""),
        }
    return output


def _resolve_run_id(connection, run_id):
    """CURRENT_RUN -> the active run id; anything else passes through as-is.

    Passing None explicitly means "no run filter" (pool every run) and is
    only ever the caller's deliberate choice -- callers that omit run_id get
    CURRENT_RUN and are therefore always scoped to the active run.
    """
    if run_id is CURRENT_RUN:
        return current_run_id(connection)
    return run_id


_TRADE_ROW_COLUMNS = (
    "p.setup_id,p.symbol,p.timeframe,p.direction,p.status,"
    "p.entry_price,p.stop_price,p.target_price,p.opened_at,p.closed_at,"
    "p.exit_price,p.result,p.result_r,p.risk_dollars,p.result_dollars,p.guard_reason,"
    "s.created_at,s.trigger_type,s.risk_reward,s.payload_json,"
    "p.requested_risk_dollars,p.actual_risk_dollars,p.quantity,p.per_contract_risk,"
    "p.contract_multiplier,p.execution_contract,p.accounting_version,p.mfe_r,p.mae_r,"
    "COALESCE(s.run_id,p.run_id) AS run_id,COALESCE(s.engine_version,p.engine_version) AS engine_version,"
    "COALESCE(s.operation_version,p.operation_version) AS operation_version,"
    "p.max_micros_cap,p.unused_risk_dollars"
)


def _hold_seconds(opened_at: str | None, closed_at: str | None) -> float | None:
    if not opened_at or not closed_at:
        return None
    try:
        opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        closed = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (closed - opened).total_seconds())


def _shape_trade_row(row, parity: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = _safe_json(row[19], {})
    meta = _metadata(payload)
    parity_row = parity.get(str(row[0]))
    certification = "UNTESTED"
    if parity_row is not None:
        certification = "MATCH" if parity_row["matched_trade_path"] else "DIFF"
    return {
        "setup_id": str(row[0]),
        "symbol": str(row[1]),
        "timeframe": str(row[2]),
        "direction": str(row[3]),
        "status": str(row[4]),
        "entry_price": _number(row[5]),
        "stop_price": _number(row[6]),
        "target_price": _number(row[7]),
        "opened_at": row[8],
        "closed_at": row[9],
        "exit_price": _number(row[10]),
        "result": str(row[11] or ""),
        "result_r": _number(row[12]),
        "risk_dollars": _number(row[13]),
        "result_dollars": _number(row[14]),
        "guard_reason": str(row[15] or ""),
        "created_at": row[16],
        "trigger_type": str(row[17] or ""),
        "planned_rr": _number(row[18]),
        "grade": _quality_grade(meta),
        "regime": _regime(meta),
        "strategy": str(meta.get("strategy") or "ICT_CONFLUENCE"),
        "entry_type": str(meta.get("entry_type") or ""),
        "setup_family": _setup_family(meta, str(row[17] or "")),
        "checklist_score": int(meta.get("checklist_score", 0) or 0),
        "checklist_total": int(meta.get("checklist_total", 0) or 0),
        "execution_certification": certification,
        "nautilus": parity_row,
        "payload": payload,
        "requested_risk_dollars": _number(row[20]),
        "actual_risk_dollars": _number(row[21]),
        "quantity": int(row[22]) if row[22] is not None else None,
        "per_contract_risk": _number(row[23]),
        "contract_multiplier": _number(row[24]),
        "execution_contract": row[25],
        "accounting_version": row[26],
        "mfe_r": _number(row[27]),
        "mae_r": _number(row[28]),
        "run_id": row[29],
        "engine_version": row[30],
        "operation_version": row[31],
        "max_micros_cap": int(row[32]) if row[32] is not None else None,
        "unused_risk_dollars": _number(row[33]),
        "hold_seconds": _hold_seconds(row[8], row[9]),
    }


def closed_gold_trades(connection, *, limit: int = 25, run_id=CURRENT_RUN) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    if not _table_exists(connection, "paper_trades") or not _table_exists(connection, "strategy_setups"):
        return []
    resolved_run_id = _resolve_run_id(connection, run_id)
    where = "p.symbol='GC' AND p.status='CLOSED'"
    params: list[Any] = []
    if resolved_run_id is not None:
        where += " AND COALESCE(s.run_id,p.run_id) = ?"
        params.append(resolved_run_id)
    params.append(bounded)
    rows = connection.execute(
        f"""
        SELECT {_TRADE_ROW_COLUMNS}
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id=p.setup_id
        WHERE {where}
        ORDER BY COALESCE(p.closed_at,p.updated_at) DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    parity = _latest_parity(connection)
    return [_shape_trade_row(row, parity) for row in rows]


def all_gc_paper_trades(connection, *, limit: int = 2000, run_id=CURRENT_RUN) -> list[dict[str, Any]]:
    """Every GC paper_trades row regardless of status (PENDING/OPEN/CLOSED/INVALIDATED).

    This is the source for fill-rate and invalidated-order research; unlike
    closed_gold_trades it is not limited to status='CLOSED'.
    """
    bounded = max(1, min(int(limit), 5000))
    if not _table_exists(connection, "paper_trades") or not _table_exists(connection, "strategy_setups"):
        return []
    resolved_run_id = _resolve_run_id(connection, run_id)
    where = "p.symbol='GC'"
    params: list[Any] = []
    if resolved_run_id is not None:
        where += " AND COALESCE(s.run_id,p.run_id) = ?"
        params.append(resolved_run_id)
    params.append(bounded)
    rows = connection.execute(
        f"""
        SELECT {_TRADE_ROW_COLUMNS}
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id=p.setup_id
        WHERE {where}
        ORDER BY COALESCE(p.closed_at,p.opened_at,p.updated_at) DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    parity = _latest_parity(connection)
    return [_shape_trade_row(row, parity) for row in rows]


def _trade_outcome(trade: dict[str, Any]) -> int:
    result = str(trade.get("result") or "").upper()
    pnl = _number(trade.get("result_dollars")) or 0.0
    if result == "WIN" or pnl > 0:
        return 1
    if result == "LOSS" or pnl < 0:
        return -1
    return 0


def evidence_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    samples = len(trades)
    wins = sum(1 for trade in trades if _trade_outcome(trade) > 0)
    losses = sum(1 for trade in trades if _trade_outcome(trade) < 0)
    breakeven = samples - wins - losses
    pnl_values = [(_number(trade.get("result_dollars")) or 0.0) for trade in trades]
    r_values = [value for value in (_number(trade.get("result_r")) for trade in trades) if value is not None]
    gross_win = sum(value for value in pnl_values if value > 0)
    gross_loss = abs(sum(value for value in pnl_values if value < 0))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else None

    ordered = sorted(trades, key=lambda trade: str(trade.get("closed_at") or ""))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for trade in ordered:
        equity += _number(trade.get("result_dollars")) or 0.0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    parity_rows = [trade.get("nautilus") for trade in trades if trade.get("nautilus") is not None]
    parity_samples = len(parity_rows)
    path_matches = sum(1 for item in parity_rows if item and item.get("matched_trade_path"))
    full_matches = sum(1 for item in parity_rows if item and item.get("matched_full"))

    if samples == 0:
        evidence_status = "NOT_EVALUABLE"
    elif samples < MIN_EVIDENCE_SAMPLES:
        evidence_status = "INSUFFICIENT_EVIDENCE"
    elif parity_samples < samples:
        evidence_status = "PARTIAL_EXECUTION_CERTIFICATION"
    elif path_matches < parity_samples:
        evidence_status = "EXECUTION_MISMATCH_REVIEW"
    else:
        evidence_status = "EVIDENCE_READY"

    avg_r = sum(r_values) / len(r_values) if r_values else None
    return {
        "samples": samples,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(wins / samples, 4) if samples else None,
        "avg_r": round(avg_r, 4) if avg_r is not None else None,
        "expectancy_r": round(avg_r, 4) if avg_r is not None else None,
        "net_pnl": round(sum(pnl_values), 2),
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor is not None else None,
        "max_drawdown": round(max_drawdown, 2),
        "parity_samples": parity_samples,
        "parity_path_matches": path_matches,
        "parity_full_matches": full_matches,
        "nautilus_path_match_rate": round(path_matches / parity_samples, 4) if parity_samples else None,
        "evidence_status": evidence_status,
        "minimum_samples": MIN_EVIDENCE_SAMPLES,
        "sample_progress": round(min(samples / MIN_EVIDENCE_SAMPLES, 1.0), 4),
    }


def _segment_metrics(trades: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get(field) or "UNKNOWN")].append(trade)
    output = []
    for key, rows in groups.items():
        item = evidence_metrics(rows)
        item[field] = key
        output.append(item)
    return sorted(output, key=lambda item: (-int(item["samples"]), str(item[field])))


def fill_rate_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Entry-quality funnel metrics over ALL registered GC paper orders.

    Unlike evidence_metrics (CLOSED trades only), this covers every terminal
    and in-flight state a registered order can reach: filled and closed,
    filled and still open, or never filled (expired/stale/invalidated/
    cannot-size). Non-authoritative research only; never mutates trading
    state. Always division-guarded so an empty or all-pending sample never
    raises.
    """
    registered = len(rows)
    filled = sum(1 for row in rows if row.get("opened_at"))
    expired = sum(1 for row in rows if row.get("result") == EXPIRED_BEFORE_ENTRY)
    stale = sum(1 for row in rows if row.get("result") == STALE_MOVE_BEFORE_ENTRY)
    invalidated = sum(1 for row in rows if row.get("result") == INVALIDATED_BEFORE_ENTRY)
    cannot_size = sum(1 for row in rows if row.get("result") == CANNOT_SIZE_RESULT)
    wins = sum(1 for row in rows if row.get("status") == "CLOSED" and row.get("result") == "WIN")
    losses = sum(1 for row in rows if row.get("status") == "CLOSED" and row.get("result") == "LOSS")
    closed = wins + losses
    pending = sum(1 for row in rows if row.get("status") == "PENDING")
    open_now = sum(1 for row in rows if row.get("status") == "OPEN")

    mfe_values = [row["mfe_r"] for row in rows if row.get("mfe_r") is not None and row.get("opened_at")]
    mae_values = [row["mae_r"] for row in rows if row.get("mae_r") is not None and row.get("opened_at")]
    hold_values = [row["hold_seconds"] for row in rows if row.get("hold_seconds") is not None]

    return {
        "registered": registered,
        "filled": filled,
        "fill_rate": round(filled / registered, 4) if registered else None,
        "expired_before_entry": expired,
        "stale_move_before_entry": stale,
        "invalidated_before_entry": invalidated,
        "cannot_size_mgc": cannot_size,
        "wins": wins,
        "losses": losses,
        "closed": closed,
        "pending": pending,
        "open": open_now,
        "avg_mfe_r": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None,
        "avg_mae_r": round(sum(mae_values) / len(mae_values), 4) if mae_values else None,
        "avg_hold_seconds": round(sum(hold_values) / len(hold_values), 1) if hold_values else None,
        "mfe_mae_samples": len(mfe_values),
        "hold_time_samples": len(hold_values),
    }


def fill_rate_segments(rows: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "UNKNOWN")].append(row)
    output = []
    for key, segment_rows in groups.items():
        item = fill_rate_metrics(segment_rows)
        item[field] = key
        output.append(item)
    return sorted(output, key=lambda item: (-int(item["registered"]), str(item[field])))


def run_scope_summary(connection) -> dict[str, Any]:
    """Run/version composition so Research Lab never silently pools runs.

    Legacy rows written before this patch have no run_id and are grouped
    under UNTAGGED_LEGACY; they remain queryable as history but are never
    counted as part of the current run.
    """
    active_run = current_run_id(connection)
    if not _table_exists(connection, "strategy_setups"):
        return {
            "current_run_id": active_run,
            "current_run_sample_count": 0,
            "available_runs": [],
            "engine_version": ENGINE_VERSION,
            "operation_version": OPERATION_VERSION,
        }

    rows = connection.execute(
        """
        SELECT COALESCE(run_id,'UNTAGGED_LEGACY') AS run_id,
               COALESCE(engine_version,'UNKNOWN') AS engine_version,
               COALESCE(operation_version,'UNKNOWN') AS operation_version,
               COUNT(*), MIN(created_at), MAX(created_at)
        FROM strategy_setups
        WHERE symbol='GC'
        GROUP BY run_id, engine_version, operation_version
        ORDER BY MAX(created_at) DESC
        """
    ).fetchall()
    available = [
        {
            "run_id": row[0],
            "engine_version": row[1],
            "operation_version": row[2],
            "setup_count": int(row[3] or 0),
            "first_seen": row[4],
            "last_seen": row[5],
            "is_current": row[0] == active_run,
        }
        for row in rows
    ]
    current_count = next((item["setup_count"] for item in available if item["is_current"]), 0)
    return {
        "current_run_id": active_run,
        "current_run_sample_count": current_count,
        "available_runs": available,
        "engine_version": ENGINE_VERSION,
        "operation_version": OPERATION_VERSION,
    }


def _retracement_price(payload: dict[str, Any], direction: str, fraction: float) -> float | None:
    displacement = payload.get("displacement", {}) if isinstance(payload, dict) else {}
    if not isinstance(displacement, dict):
        return None
    low = _number(displacement.get("low"))
    high = _number(displacement.get("high"))
    if low is None or high is None or high <= low:
        return None
    move = high - low
    if str(direction).lower() == "bullish":
        return high - fraction * move
    return low + fraction * move


def _planned_rr(direction: str, entry: float | None, stop: float | None, target: float | None) -> float | None:
    if entry is None or stop is None or target is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reward = (target - entry) if str(direction).lower() == "bullish" else (entry - target)
    if reward <= 0:
        return None
    return round(reward / risk, 4)


def counterfactual_candidates(trade: dict[str, Any]) -> list[dict[str, Any]]:
    """Return geometry candidates only. This function never invents alternate outcomes."""
    payload = trade.get("payload") or {}
    direction = str(trade.get("direction") or "")
    stop = _number(trade.get("stop_price"))
    target = _number(trade.get("target_price"))
    fvg = payload.get("entry_fvg", {}) if isinstance(payload, dict) else {}
    fvg_low = _number(fvg.get("lower")) if isinstance(fvg, dict) else None
    fvg_high = _number(fvg.get("upper")) if isinstance(fvg, dict) else None
    fvg_mid = (fvg_low + fvg_high) / 2 if fvg_low is not None and fvg_high is not None else None

    variants = (
        ("ACTUAL", _number(trade.get("entry_price"))),
        ("FVG_50", fvg_mid),
        ("OTE_70_5", _retracement_price(payload, direction, 0.705)),
        ("OTE_79", _retracement_price(payload, direction, 0.79)),
    )
    output = []
    for variant, entry in variants:
        if entry is None:
            output.append(
                {
                    "setup_id": trade.get("setup_id"),
                    "variant": variant,
                    "evaluability": "NOT_EVALUABLE",
                    "reason": "Required setup geometry was not persisted for this candidate.",
                    "entry_price": None,
                    "stop_price": stop,
                    "target_price": target,
                    "planned_rr": None,
                }
            )
            continue
        output.append(
            {
                "setup_id": trade.get("setup_id"),
                "variant": variant,
                "evaluability": "GEOMETRY_ONLY",
                "reason": "No alternate win/loss is inferred until the candidate is replayed against an eligible stored quote window.",
                "entry_price": round(entry, 6),
                "stop_price": stop,
                "target_price": target,
                "planned_rr": _planned_rr(direction, entry, stop, target),
            }
        )
    return output


def hypotheses(connection) -> list[dict[str, Any]]:
    ensure_research_lab81(connection)
    rows = connection.execute(
        """
        SELECT hypothesis_id,title,question,metric,status,notes,created_at,updated_at
        FROM research_hypotheses_v01
        ORDER BY hypothesis_id
        """
    ).fetchall()
    return [
        {
            "hypothesis_id": row[0],
            "title": row[1],
            "question": row[2],
            "metric": row[3],
            "status": row[4],
            "notes": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }
        for row in rows
    ]


def _latest_evidence(connection, *, limit: int = 12) -> list[dict[str, Any]]:
    ensure_research_lab81(connection)
    rows = connection.execute(
        """
        SELECT captured_at,scope_key,samples,wins,losses,win_rate,expectancy_r,net_pnl,
               profit_factor,max_drawdown,parity_samples,parity_path_matches,parity_full_matches,
               evidence_status,payload_json
        FROM research_evidence_v01
        ORDER BY evidence_id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    return [
        {
            "captured_at": row[0],
            "scope_key": row[1],
            "samples": int(row[2] or 0),
            "wins": int(row[3] or 0),
            "losses": int(row[4] or 0),
            "win_rate": row[5],
            "expectancy_r": row[6],
            "net_pnl": row[7],
            "profit_factor": row[8],
            "max_drawdown": row[9],
            "parity_samples": int(row[10] or 0),
            "parity_path_matches": int(row[11] or 0),
            "parity_full_matches": int(row[12] or 0),
            "evidence_status": row[13],
            "payload": _safe_json(row[14], {}),
        }
        for row in rows
    ]


def research_lab_snapshot81(connection, *, recent_limit: int = 25, run_id=CURRENT_RUN) -> dict[str, Any]:
    ensure_research_lab81(connection)
    trades = closed_gold_trades(connection, limit=500, run_id=run_id)
    baseline = evidence_metrics(trades)
    recent = trades[: max(1, min(int(recent_limit), 100))]

    all_rows = all_gc_paper_trades(connection, limit=2000, run_id=run_id)
    fill_rate = fill_rate_metrics(all_rows)

    return {
        "lab": "OTR Research Lab",
        "version": LAB_VERSION,
        "authoritative": False,
        "strategy_mutation_allowed": False,
        "broker_actions_allowed": False,
        "scope": "GC_ONLY_OPERATION_8_1_BASELINE",
        "run_scope": run_scope_summary(connection),
        "paper_accounting_version": PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1,
        "baseline": baseline,
        "fill_rate": fill_rate,
        "segments": {
            "setup_family": _segment_metrics(trades, "setup_family"),
            "grade": _segment_metrics(trades, "grade"),
            "timeframe": _segment_metrics(trades, "timeframe"),
            "regime": _segment_metrics(trades, "regime"),
        },
        "fill_rate_segments": {
            "setup_family": fill_rate_segments(all_rows, "setup_family"),
            "grade": fill_rate_segments(all_rows, "grade"),
            "timeframe": fill_rate_segments(all_rows, "timeframe"),
            "entry_type": fill_rate_segments(all_rows, "entry_type"),
        },
        "hypotheses": hypotheses(connection),
        "recent_trades": recent,
        "latest_evidence": _latest_evidence(connection),
    }


def _store_evidence(connection, scope_key: str, metrics: dict[str, Any], payload: dict[str, Any]) -> None:
    connection.execute(
        """
        INSERT INTO research_evidence_v01(
            captured_at,scope_key,samples,wins,losses,breakeven,win_rate,expectancy_r,avg_r,
            net_pnl,gross_win,gross_loss,profit_factor,max_drawdown,parity_samples,
            parity_path_matches,parity_full_matches,evidence_status,payload_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            _utc_iso(),
            scope_key,
            metrics["samples"],
            metrics["wins"],
            metrics["losses"],
            metrics["breakeven"],
            metrics["win_rate"],
            metrics["expectancy_r"],
            metrics["avg_r"],
            metrics["net_pnl"],
            metrics["gross_win"],
            metrics["gross_loss"],
            metrics["profit_factor"],
            metrics["max_drawdown"],
            metrics["parity_samples"],
            metrics["parity_path_matches"],
            metrics["parity_full_matches"],
            metrics["evidence_status"],
            json.dumps(payload, sort_keys=True),
        ),
    )


def refresh_research_lab81(connection, *, run_id=CURRENT_RUN) -> dict[str, Any]:
    """Capture evidence into Lab-owned tables without touching OTR trading state."""
    ensure_research_lab81(connection)
    trades = closed_gold_trades(connection, limit=500, run_id=run_id)
    baseline = evidence_metrics(trades)
    _store_evidence(connection, "GC:ALL", baseline, {"dimension": "all"})

    for field in ("setup_family", "grade", "timeframe", "regime"):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trade in trades:
            groups[str(trade.get(field) or "UNKNOWN")].append(trade)
        for key, rows in groups.items():
            _store_evidence(
                connection,
                f"GC:{field}:{key}",
                evidence_metrics(rows),
                {"dimension": field, "value": key},
            )

    now = _utc_iso()
    counterfactual_count = 0
    for trade in trades[:100]:
        for candidate in counterfactual_candidates(trade):
            connection.execute(
                """
                INSERT INTO research_counterfactuals_v01(
                    setup_id,variant,evaluability,reason,entry_price,stop_price,target_price,
                    planned_rr,metadata_json,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(setup_id,variant) DO UPDATE SET
                    evaluability=excluded.evaluability,
                    reason=excluded.reason,
                    entry_price=excluded.entry_price,
                    stop_price=excluded.stop_price,
                    target_price=excluded.target_price,
                    planned_rr=excluded.planned_rr,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    candidate["setup_id"],
                    candidate["variant"],
                    candidate["evaluability"],
                    candidate["reason"],
                    candidate["entry_price"],
                    candidate["stop_price"],
                    candidate["target_price"],
                    candidate["planned_rr"],
                    json.dumps({"actual_result": trade.get("result"), "actual_r": trade.get("result_r")}, sort_keys=True),
                    now,
                ),
            )
            counterfactual_count += 1
    connection.commit()
    return {
        "captured_at": now,
        "closed_gold_trades": len(trades),
        "baseline": baseline,
        "counterfactual_candidates": counterfactual_count,
        "hypotheses": len(DEFAULT_HYPOTHESES),
        "strategy_mutations": 0,
        "broker_actions": 0,
    }
