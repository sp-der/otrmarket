from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

MIN_EVIDENCE_SAMPLES = 20
LAB_VERSION = "0.1"

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


def closed_gold_trades(connection, *, limit: int = 25) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 500))
    if not _table_exists(connection, "paper_trades") or not _table_exists(connection, "strategy_setups"):
        return []
    rows = connection.execute(
        """
        SELECT
            p.setup_id,p.symbol,p.timeframe,p.direction,p.status,
            p.entry_price,p.stop_price,p.target_price,p.opened_at,p.closed_at,
            p.exit_price,p.result,p.result_r,p.risk_dollars,p.result_dollars,p.guard_reason,
            s.created_at,s.trigger_type,s.risk_reward,s.payload_json
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id=p.setup_id
        WHERE p.symbol='GC' AND p.status='CLOSED'
        ORDER BY COALESCE(p.closed_at,p.updated_at) DESC
        LIMIT ?
        """,
        (bounded,),
    ).fetchall()
    parity = _latest_parity(connection)
    output: list[dict[str, Any]] = []
    for row in rows:
        payload = _safe_json(row[19], {})
        meta = _metadata(payload)
        parity_row = parity.get(str(row[0]))
        certification = "UNTESTED"
        if parity_row is not None:
            certification = "MATCH" if parity_row["matched_trade_path"] else "DIFF"
        output.append(
            {
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
            }
        )
    return output


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


def research_lab_snapshot81(connection, *, recent_limit: int = 25) -> dict[str, Any]:
    ensure_research_lab81(connection)
    trades = closed_gold_trades(connection, limit=500)
    baseline = evidence_metrics(trades)
    recent = trades[: max(1, min(int(recent_limit), 100))]
    return {
        "lab": "OTR Research Lab",
        "version": LAB_VERSION,
        "authoritative": False,
        "strategy_mutation_allowed": False,
        "broker_actions_allowed": False,
        "scope": "GC_ONLY_OPERATION_8_1_BASELINE",
        "baseline": baseline,
        "segments": {
            "setup_family": _segment_metrics(trades, "setup_family"),
            "grade": _segment_metrics(trades, "grade"),
            "timeframe": _segment_metrics(trades, "timeframe"),
            "regime": _segment_metrics(trades, "regime"),
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


def refresh_research_lab81(connection) -> dict[str, Any]:
    """Capture evidence into Lab-owned tables without touching OTR trading state."""
    ensure_research_lab81(connection)
    trades = closed_gold_trades(connection, limit=500)
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
