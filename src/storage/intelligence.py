from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _duration_seconds(opened_at, closed_at) -> float | None:
    if opened_at is None or closed_at is None:
        return None
    try:
        return max(0.0, (closed_at - opened_at).total_seconds())
    except Exception:
        return None


def _loss_classification(position) -> str | None:
    if str(position.result or "").upper() != "LOSS":
        return "WIN" if str(position.result or "").upper() == "WIN" else None
    seconds = _duration_seconds(position.opened_at, position.closed_at)
    mfe_r = float(getattr(position, "mfe_r", 0.0) or 0.0)
    if seconds is not None and seconds <= 60:
        return "INSTANT_STOP"
    if seconds is not None and seconds <= 300:
        return "EARLY_STOP"
    if mfe_r >= 0.50:
        return "GAVE_BACK_EDGE"
    return "NORMAL_LOSS"


def ensure_intelligence_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS trade_intelligence (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            strategy TEXT,
            trigger_type TEXT,
            entry_type TEXT,
            created_at TEXT,
            opened_at TEXT,
            closed_at TEXT,
            status TEXT NOT NULL,
            result TEXT,
            result_r REAL,
            risk_reward REAL,
            risk_dollars REAL,
            result_dollars REAL,
            displacement_body_ratio REAL,
            displacement_range_ratio REAL,
            fvg_age_bars REAL,
            htf_timeframe TEXT,
            htf_bias TEXT,
            mfe_r REAL NOT NULL DEFAULT 0,
            mae_r REAL NOT NULL DEFAULT 0,
            duration_seconds REAL,
            outcome_class TEXT,
            fingerprint_json TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS shadow_trades (
            setup_id TEXT PRIMARY KEY,
            source_setup_id TEXT,
            profile TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            strategy TEXT,
            trigger_type TEXT,
            entry_type TEXT,
            status TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            target_price REAL NOT NULL,
            risk_reward REAL NOT NULL,
            opened_at TEXT,
            closed_at TEXT,
            exit_price REAL,
            result TEXT,
            result_r REAL,
            risk_dollars REAL,
            result_dollars REAL,
            mfe_r REAL NOT NULL DEFAULT 0,
            mae_r REAL NOT NULL DEFAULT 0,
            duration_seconds REAL,
            outcome_class TEXT,
            fingerprint_json TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trade_intelligence_closed
        ON trade_intelligence(closed_at);

        CREATE INDEX IF NOT EXISTS idx_shadow_trades_profile_closed
        ON shadow_trades(profile, closed_at);
        """
    )
    connection.commit()


def _fingerprint(setup) -> dict[str, Any]:
    metadata = dict(getattr(setup, "metadata", {}) or {})
    displacement = getattr(setup, "displacement", None)
    context = metadata.get("a_plus_context", {}) or {}
    session = metadata.get("session_consistency", {}) or {}
    return {
        "symbol": setup.symbol,
        "timeframe": setup.timeframe,
        "direction": setup.direction,
        "strategy": metadata.get("strategy", "ICT_CONFLUENCE"),
        "trigger_type": getattr(setup, "trigger_type", None),
        "entry_type": metadata.get("entry_type", "FVG_MIDPOINT"),
        "risk_reward": float(getattr(setup, "risk_reward", 0.0) or 0.0),
        "displacement_body_ratio": float(getattr(displacement, "body_ratio", 0.0) or 0.0),
        "displacement_range_ratio": float(getattr(displacement, "range_ratio", 0.0) or 0.0),
        "fvg_age_bars": context.get("entry_fvg_age_bars"),
        "htf_timeframe": context.get("context_timeframe"),
        "htf_bias": context.get("higher_timeframe_bias"),
        "session_day": session.get("local_day"),
        "session_time": session.get("local_time"),
        "session_timezone": session.get("timezone"),
        "quality_profile": (metadata.get("execution_quality_gate", {}) or {}).get("profile"),
    }


def upsert_trade_intelligence(connection: sqlite3.Connection, position, updated_at: str) -> None:
    ensure_intelligence_schema(connection)
    setup = position.setup
    fp = _fingerprint(setup)
    connection.execute(
        """
        INSERT INTO trade_intelligence (
            setup_id, symbol, timeframe, strategy, trigger_type, entry_type,
            created_at, opened_at, closed_at, status, result, result_r,
            risk_reward, risk_dollars, result_dollars,
            displacement_body_ratio, displacement_range_ratio, fvg_age_bars,
            htf_timeframe, htf_bias, mfe_r, mae_r, duration_seconds,
            outcome_class, fingerprint_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(setup_id) DO UPDATE SET
            opened_at=excluded.opened_at,
            closed_at=excluded.closed_at,
            status=excluded.status,
            result=excluded.result,
            result_r=excluded.result_r,
            risk_dollars=COALESCE(excluded.risk_dollars, trade_intelligence.risk_dollars),
            result_dollars=excluded.result_dollars,
            fvg_age_bars=COALESCE(excluded.fvg_age_bars, trade_intelligence.fvg_age_bars),
            htf_timeframe=COALESCE(excluded.htf_timeframe, trade_intelligence.htf_timeframe),
            htf_bias=COALESCE(excluded.htf_bias, trade_intelligence.htf_bias),
            mfe_r=excluded.mfe_r,
            mae_r=excluded.mae_r,
            duration_seconds=excluded.duration_seconds,
            outcome_class=excluded.outcome_class,
            fingerprint_json=excluded.fingerprint_json,
            updated_at=excluded.updated_at
        """,
        (
            setup.setup_id,
            setup.symbol,
            setup.timeframe,
            fp["strategy"],
            fp["trigger_type"],
            fp["entry_type"],
            _iso(setup.created_at),
            _iso(position.opened_at),
            _iso(position.closed_at),
            position.status,
            position.result,
            position.result_r,
            float(setup.risk_reward or 0.0),
            position.risk_dollars,
            position.result_dollars,
            fp["displacement_body_ratio"],
            fp["displacement_range_ratio"],
            fp["fvg_age_bars"],
            fp["htf_timeframe"],
            fp["htf_bias"],
            float(getattr(position, "mfe_r", 0.0) or 0.0),
            float(getattr(position, "mae_r", 0.0) or 0.0),
            _duration_seconds(position.opened_at, position.closed_at),
            _loss_classification(position),
            json.dumps(fp, sort_keys=True),
            updated_at,
        ),
    )
    connection.commit()


def upsert_shadow_trade(
    connection: sqlite3.Connection,
    position,
    updated_at: str,
    *,
    profile: str = "OPERATION_4_8_SHADOW",
    source_setup_id: str | None = None,
) -> None:
    ensure_intelligence_schema(connection)
    setup = position.setup
    fp = _fingerprint(setup)
    connection.execute(
        """
        INSERT INTO shadow_trades (
            setup_id, source_setup_id, profile, symbol, timeframe, direction,
            strategy, trigger_type, entry_type, status,
            entry_price, stop_price, target_price, risk_reward,
            opened_at, closed_at, exit_price, result, result_r,
            risk_dollars, result_dollars, mfe_r, mae_r, duration_seconds,
            outcome_class, fingerprint_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(setup_id) DO UPDATE SET
            status=excluded.status,
            opened_at=excluded.opened_at,
            closed_at=excluded.closed_at,
            exit_price=excluded.exit_price,
            result=excluded.result,
            result_r=excluded.result_r,
            result_dollars=excluded.result_dollars,
            mfe_r=excluded.mfe_r,
            mae_r=excluded.mae_r,
            duration_seconds=excluded.duration_seconds,
            outcome_class=excluded.outcome_class,
            fingerprint_json=excluded.fingerprint_json,
            updated_at=excluded.updated_at
        """,
        (
            setup.setup_id,
            source_setup_id,
            profile,
            setup.symbol,
            setup.timeframe,
            setup.direction,
            fp["strategy"],
            fp["trigger_type"],
            fp["entry_type"],
            position.status,
            float(setup.entry_price),
            float(setup.stop_price),
            float(setup.target_price),
            float(setup.risk_reward or 0.0),
            _iso(position.opened_at),
            _iso(position.closed_at),
            position.exit_price,
            position.result,
            position.result_r,
            position.risk_dollars,
            position.result_dollars,
            float(getattr(position, "mfe_r", 0.0) or 0.0),
            float(getattr(position, "mae_r", 0.0) or 0.0),
            _duration_seconds(position.opened_at, position.closed_at),
            _loss_classification(position),
            json.dumps(fp, sort_keys=True),
            updated_at,
        ),
    )
    connection.commit()


def _stats(connection: sqlite3.Connection, table: str, where: str = "1=1", params: tuple = ()) -> dict[str, Any]:
    rows = connection.execute(
        f"""
        SELECT symbol, timeframe, result, result_r, result_dollars,
               mfe_r, mae_r, duration_seconds, outcome_class
        FROM {table}
        WHERE {where} AND status = 'CLOSED' AND result_r IS NOT NULL
        ORDER BY closed_at ASC
        """,
        params,
    ).fetchall()
    closed = len(rows)
    wins = sum(1 for row in rows if str(row[2] or "").upper() == "WIN")
    losses = sum(1 for row in rows if str(row[2] or "").upper() == "LOSS")
    total_r = sum(float(row[3] or 0.0) for row in rows)
    total_dollars = sum(float(row[4] or 0.0) for row in rows)
    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / closed * 100.0) if closed else None,
        "total_r": total_r,
        "avg_r": (total_r / closed) if closed else None,
        "total_dollars": total_dollars,
        "avg_mfe_r": (sum(float(row[5] or 0.0) for row in rows) / closed) if closed else None,
        "avg_mae_r": (sum(float(row[6] or 0.0) for row in rows) / closed) if closed else None,
        "instant_stops": sum(1 for row in rows if row[8] == "INSTANT_STOP"),
        "early_stops": sum(1 for row in rows if row[8] == "EARLY_STOP"),
        "gave_back_edge": sum(1 for row in rows if row[8] == "GAVE_BACK_EDGE"),
    }


def intelligence_snapshot(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {
            "profile": "TRADE_INTELLIGENCE_5_3",
            "live": {},
            "shadow_48": {},
            "recent_live": [],
            "recent_shadow": [],
        }
    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        ensure_intelligence_schema(connection)
        live = _stats(connection, "trade_intelligence")
        shadow = _stats(
            connection,
            "shadow_trades",
            "profile = ?",
            ("OPERATION_4_8_SHADOW",),
        )
        recent_live = [
            dict(row)
            for row in connection.execute(
                """
                SELECT setup_id, symbol, timeframe, strategy, trigger_type, entry_type,
                       result, result_r, result_dollars, mfe_r, mae_r,
                       duration_seconds, outcome_class, opened_at, closed_at
                FROM trade_intelligence
                WHERE status = 'CLOSED'
                ORDER BY closed_at DESC
                LIMIT 12
                """
            ).fetchall()
        ]
        recent_shadow = [
            dict(row)
            for row in connection.execute(
                """
                SELECT setup_id, source_setup_id, symbol, timeframe, strategy,
                       trigger_type, entry_type, result, result_r, result_dollars,
                       mfe_r, mae_r, duration_seconds, outcome_class,
                       opened_at, closed_at
                FROM shadow_trades
                WHERE profile = 'OPERATION_4_8_SHADOW' AND status = 'CLOSED'
                ORDER BY closed_at DESC
                LIMIT 12
                """
            ).fetchall()
        ]
        return {
            "profile": "TRADE_INTELLIGENCE_5_3",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "live": live,
            "shadow_48": shadow,
            "recent_live": recent_live,
            "recent_shadow": recent_shadow,
        }
    finally:
        connection.close()
