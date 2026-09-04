from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any

from src.storage.database import get_connection
from src.storage.intelligence import ensure_intelligence_schema
from src.storage.learning import ensure_learning_schema


TRAINING_BUILD_72T = "7.2T"
ACTIVE_RUN_TABLE_72T = "verify_active_run_72s"


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _ensure_counterfactual_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS counterfactual_setups (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            target_price REAL NOT NULL,
            created_at TEXT NOT NULL,
            blocked_status TEXT NOT NULL,
            blocked_reason TEXT,
            outcome TEXT NOT NULL DEFAULT 'OPEN',
            resolved_at TEXT,
            max_favorable_r REAL NOT NULL DEFAULT 0,
            max_adverse_r REAL NOT NULL DEFAULT 0,
            last_checked TEXT
        )
        """
    )


def _ensure_training_schema(connection: sqlite3.Connection) -> None:
    ensure_learning_schema(connection)
    ensure_intelligence_schema(connection)
    _ensure_counterfactual_schema(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS training_decisions_72t (
            run_id TEXT NOT NULL,
            setup_id TEXT NOT NULL,
            build TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            created_at TEXT NOT NULL,
            trigger_type TEXT,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            risk_reward REAL,
            status TEXT NOT NULL,
            payload_json TEXT,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (run_id, setup_id)
        );
        CREATE INDEX IF NOT EXISTS idx_training_decisions_72t_symbol_time
        ON training_decisions_72t(symbol, created_at);

        CREATE TABLE IF NOT EXISTS training_trades_72t (
            run_id TEXT NOT NULL,
            setup_id TEXT NOT NULL,
            build TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL,
            opened_at TEXT,
            closed_at TEXT,
            result TEXT,
            result_r REAL,
            risk_dollars REAL,
            result_dollars REAL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, setup_id)
        );
        CREATE INDEX IF NOT EXISTS idx_training_trades_72t_closed
        ON training_trades_72t(symbol, closed_at);

        CREATE TABLE IF NOT EXISTS training_trade_metrics_72t (
            run_id TEXT NOT NULL,
            setup_id TEXT NOT NULL,
            build TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            strategy TEXT,
            trigger_type TEXT,
            entry_type TEXT,
            result TEXT,
            result_r REAL,
            risk_reward REAL,
            displacement_body_ratio REAL,
            displacement_range_ratio REAL,
            fvg_age_bars REAL,
            htf_timeframe TEXT,
            htf_bias TEXT,
            mfe_r REAL,
            mae_r REAL,
            duration_seconds REAL,
            outcome_class TEXT,
            fingerprint_json TEXT,
            closed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, setup_id)
        );

        CREATE TABLE IF NOT EXISTS training_counterfactuals_72t (
            run_id TEXT NOT NULL,
            setup_id TEXT NOT NULL,
            build TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            created_at TEXT NOT NULL,
            blocked_status TEXT NOT NULL,
            blocked_reason TEXT,
            outcome TEXT NOT NULL,
            resolved_at TEXT,
            max_favorable_r REAL NOT NULL DEFAULT 0,
            max_adverse_r REAL NOT NULL DEFAULT 0,
            last_checked TEXT,
            PRIMARY KEY (run_id, setup_id)
        );
        CREATE INDEX IF NOT EXISTS idx_training_counterfactuals_72t_outcome
        ON training_counterfactuals_72t(symbol, outcome);

        CREATE TABLE IF NOT EXISTS training_shadow_72t (
            run_id TEXT NOT NULL,
            setup_id TEXT NOT NULL,
            build TEXT NOT NULL,
            source_setup_id TEXT,
            profile TEXT,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            strategy TEXT,
            status TEXT NOT NULL,
            result TEXT,
            result_r REAL,
            mfe_r REAL,
            mae_r REAL,
            closed_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, setup_id)
        );
        """
    )
    connection.commit()


def _active_run(connection: sqlite3.Connection) -> tuple[str, str]:
    run_id = os.getenv("OTR_VERIFY_RUN_ID", "").strip()
    build = TRAINING_BUILD_72T
    if _table_exists(connection, ACTIVE_RUN_TABLE_72T):
        row = connection.execute(
            f"SELECT run_id, build FROM {ACTIVE_RUN_TABLE_72T} WHERE slot=1"
        ).fetchone()
        if row:
            run_id = str(row[0] or run_id)
            build = str(row[1] or build)
    return run_id, build


def install_training_capture_72t() -> dict[str, int | str]:
    """Persist replay/live research samples across dashboard wipes.

    The normal VERIFY ledger is intentionally disposable between experiments.
    These tables are not. Every setup decision, actual trade outcome, trade
    intelligence record, counterfactual result and shadow result is copied with
    the stable VERIFY run ID so repeated replays become a research corpus rather
    than overwriting the previous experiment.
    """
    connection = get_connection()
    try:
        _ensure_training_schema(connection)
        run_id, build = _active_run(connection)
        if not run_id:
            return {"run_id": "", "decisions": 0, "trades": 0}

        # SQLite triggers are deliberately database-side so inherited execution
        # paths cannot bypass training capture the way old VERIFY tagging did.
        connection.executescript(
            """
            DROP TRIGGER IF EXISTS training_decision_insert_72t;
            DROP TRIGGER IF EXISTS training_decision_update_72t;
            DROP TRIGGER IF EXISTS training_trade_insert_72t;
            DROP TRIGGER IF EXISTS training_trade_update_72t;
            DROP TRIGGER IF EXISTS training_intelligence_insert_72t;
            DROP TRIGGER IF EXISTS training_intelligence_update_72t;
            DROP TRIGGER IF EXISTS training_counterfactual_insert_72t;
            DROP TRIGGER IF EXISTS training_counterfactual_update_72t;
            DROP TRIGGER IF EXISTS training_shadow_insert_72t;
            DROP TRIGGER IF EXISTS training_shadow_update_72t;

            CREATE TRIGGER training_decision_insert_72t
            AFTER INSERT ON strategy_setups
            BEGIN
              INSERT OR REPLACE INTO training_decisions_72t(
                run_id,setup_id,build,symbol,timeframe,direction,created_at,
                trigger_type,entry_price,stop_price,target_price,risk_reward,status,
                payload_json,last_seen_at
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.created_at,NEW.trigger_type,NEW.entry_price,NEW.stop_price,
                NEW.target_price,NEW.risk_reward,NEW.status,NEW.payload_json,
                datetime('now')
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_decision_update_72t
            AFTER UPDATE ON strategy_setups
            BEGIN
              INSERT OR REPLACE INTO training_decisions_72t(
                run_id,setup_id,build,symbol,timeframe,direction,created_at,
                trigger_type,entry_price,stop_price,target_price,risk_reward,status,
                payload_json,last_seen_at
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.created_at,NEW.trigger_type,NEW.entry_price,NEW.stop_price,
                NEW.target_price,NEW.risk_reward,NEW.status,NEW.payload_json,
                datetime('now')
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_trade_insert_72t
            AFTER INSERT ON paper_trades
            BEGIN
              INSERT OR REPLACE INTO training_trades_72t(
                run_id,setup_id,build,symbol,timeframe,direction,status,opened_at,
                closed_at,result,result_r,risk_dollars,result_dollars,updated_at
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.status,NEW.opened_at,NEW.closed_at,NEW.result,NEW.result_r,
                NEW.risk_dollars,NEW.result_dollars,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_trade_update_72t
            AFTER UPDATE ON paper_trades
            BEGIN
              INSERT OR REPLACE INTO training_trades_72t(
                run_id,setup_id,build,symbol,timeframe,direction,status,opened_at,
                closed_at,result,result_r,risk_dollars,result_dollars,updated_at
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.status,NEW.opened_at,NEW.closed_at,NEW.result,NEW.result_r,
                NEW.risk_dollars,NEW.result_dollars,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_intelligence_insert_72t
            AFTER INSERT ON trade_intelligence
            BEGIN
              INSERT OR REPLACE INTO training_trade_metrics_72t(
                run_id,setup_id,build,symbol,timeframe,strategy,trigger_type,entry_type,
                result,result_r,risk_reward,displacement_body_ratio,
                displacement_range_ratio,fvg_age_bars,htf_timeframe,htf_bias,mfe_r,
                mae_r,duration_seconds,outcome_class,fingerprint_json,closed_at,updated_at
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.strategy,
                NEW.trigger_type,NEW.entry_type,NEW.result,NEW.result_r,NEW.risk_reward,
                NEW.displacement_body_ratio,NEW.displacement_range_ratio,
                NEW.fvg_age_bars,NEW.htf_timeframe,NEW.htf_bias,NEW.mfe_r,NEW.mae_r,
                NEW.duration_seconds,NEW.outcome_class,NEW.fingerprint_json,
                NEW.closed_at,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_intelligence_update_72t
            AFTER UPDATE ON trade_intelligence
            BEGIN
              INSERT OR REPLACE INTO training_trade_metrics_72t(
                run_id,setup_id,build,symbol,timeframe,strategy,trigger_type,entry_type,
                result,result_r,risk_reward,displacement_body_ratio,
                displacement_range_ratio,fvg_age_bars,htf_timeframe,htf_bias,mfe_r,
                mae_r,duration_seconds,outcome_class,fingerprint_json,closed_at,updated_at
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.strategy,
                NEW.trigger_type,NEW.entry_type,NEW.result,NEW.result_r,NEW.risk_reward,
                NEW.displacement_body_ratio,NEW.displacement_range_ratio,
                NEW.fvg_age_bars,NEW.htf_timeframe,NEW.htf_bias,NEW.mfe_r,NEW.mae_r,
                NEW.duration_seconds,NEW.outcome_class,NEW.fingerprint_json,
                NEW.closed_at,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_counterfactual_insert_72t
            AFTER INSERT ON counterfactual_setups
            BEGIN
              INSERT OR REPLACE INTO training_counterfactuals_72t(
                run_id,setup_id,build,symbol,timeframe,direction,created_at,
                blocked_status,blocked_reason,outcome,resolved_at,max_favorable_r,
                max_adverse_r,last_checked
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.created_at,NEW.blocked_status,NEW.blocked_reason,NEW.outcome,
                NEW.resolved_at,NEW.max_favorable_r,NEW.max_adverse_r,NEW.last_checked
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_counterfactual_update_72t
            AFTER UPDATE ON counterfactual_setups
            BEGIN
              INSERT OR REPLACE INTO training_counterfactuals_72t(
                run_id,setup_id,build,symbol,timeframe,direction,created_at,
                blocked_status,blocked_reason,outcome,resolved_at,max_favorable_r,
                max_adverse_r,last_checked
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.created_at,NEW.blocked_status,NEW.blocked_reason,NEW.outcome,
                NEW.resolved_at,NEW.max_favorable_r,NEW.max_adverse_r,NEW.last_checked
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_shadow_insert_72t
            AFTER INSERT ON shadow_trades
            BEGIN
              INSERT OR REPLACE INTO training_shadow_72t(
                run_id,setup_id,build,source_setup_id,profile,symbol,timeframe,
                direction,strategy,status,result,result_r,mfe_r,mae_r,closed_at,updated_at
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.source_setup_id,NEW.profile,
                NEW.symbol,NEW.timeframe,NEW.direction,NEW.strategy,NEW.status,
                NEW.result,NEW.result_r,NEW.mfe_r,NEW.mae_r,NEW.closed_at,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1;
            END;

            CREATE TRIGGER training_shadow_update_72t
            AFTER UPDATE ON shadow_trades
            BEGIN
              INSERT OR REPLACE INTO training_shadow_72t(
                run_id,setup_id,build,source_setup_id,profile,symbol,timeframe,
                direction,strategy,status,result,result_r,mfe_r,mae_r,closed_at,updated_at
              )
              SELECT run_id,NEW.setup_id,'7.2T',NEW.source_setup_id,NEW.profile,
                NEW.symbol,NEW.timeframe,NEW.direction,NEW.strategy,NEW.status,
                NEW.result,NEW.result_r,NEW.mfe_r,NEW.mae_r,NEW.closed_at,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1;
            END;
            """
        )

        # Backfill the still-live experiment so installing 7.2T mid-replay does
        # not throw away evidence already generated before this deployment.
        connection.execute(
            """
            INSERT OR REPLACE INTO training_decisions_72t(
              run_id,setup_id,build,symbol,timeframe,direction,created_at,trigger_type,
              entry_price,stop_price,target_price,risk_reward,status,payload_json,last_seen_at
            )
            SELECT ?,setup_id,'7.2T',symbol,timeframe,direction,created_at,trigger_type,
              entry_price,stop_price,target_price,risk_reward,status,payload_json,datetime('now')
            FROM strategy_setups
            """,
            (run_id,),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO training_trades_72t(
              run_id,setup_id,build,symbol,timeframe,direction,status,opened_at,closed_at,
              result,result_r,risk_dollars,result_dollars,updated_at
            )
            SELECT ?,setup_id,'7.2T',symbol,timeframe,direction,status,opened_at,closed_at,
              result,result_r,risk_dollars,result_dollars,updated_at FROM paper_trades
            """,
            (run_id,),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO training_counterfactuals_72t(
              run_id,setup_id,build,symbol,timeframe,direction,created_at,blocked_status,
              blocked_reason,outcome,resolved_at,max_favorable_r,max_adverse_r,last_checked
            )
            SELECT ?,setup_id,'7.2T',symbol,timeframe,direction,created_at,blocked_status,
              blocked_reason,outcome,resolved_at,max_favorable_r,max_adverse_r,last_checked
            FROM counterfactual_setups
            """,
            (run_id,),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO training_trade_metrics_72t(
              run_id,setup_id,build,symbol,timeframe,strategy,trigger_type,entry_type,
              result,result_r,risk_reward,displacement_body_ratio,displacement_range_ratio,
              fvg_age_bars,htf_timeframe,htf_bias,mfe_r,mae_r,duration_seconds,
              outcome_class,fingerprint_json,closed_at,updated_at
            )
            SELECT ?,setup_id,'7.2T',symbol,timeframe,strategy,trigger_type,entry_type,
              result,result_r,risk_reward,displacement_body_ratio,displacement_range_ratio,
              fvg_age_bars,htf_timeframe,htf_bias,mfe_r,mae_r,duration_seconds,
              outcome_class,fingerprint_json,closed_at,updated_at FROM trade_intelligence
            """,
            (run_id,),
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO training_shadow_72t(
              run_id,setup_id,build,source_setup_id,profile,symbol,timeframe,direction,
              strategy,status,result,result_r,mfe_r,mae_r,closed_at,updated_at
            )
            SELECT ?,setup_id,'7.2T',source_setup_id,profile,symbol,timeframe,direction,
              strategy,status,result,result_r,mfe_r,mae_r,closed_at,updated_at
            FROM shadow_trades
            """,
            (run_id,),
        )
        connection.commit()

        decisions = int(connection.execute(
            "SELECT COUNT(*) FROM training_decisions_72t WHERE run_id=?", (run_id,)
        ).fetchone()[0])
        trades = int(connection.execute(
            "SELECT COUNT(*) FROM training_trades_72t WHERE run_id=?", (run_id,)
        ).fetchone()[0])
        return {"run_id": run_id, "build": build, "decisions": decisions, "trades": trades}
    finally:
        connection.close()


def backfill_4h_candles_72t() -> int:
    """Derive completed 4H context candles from persisted completed 1H bars.

    This is context-only data. A 4H row is inserted only after the source 1H
    history has reached that 4H bucket's close, so replay evaluation can never
    see a partially completed future macro bar.
    """
    connection = get_connection()
    try:
        if not _table_exists(connection, "candles"):
            return 0
        rows = connection.execute(
            """
            SELECT symbol,open_time,close_time,open,high,low,close,ticks
            FROM candles
            WHERE timeframe='1h' AND symbol IN ('NQ','ES','GC')
            ORDER BY symbol,open_time ASC
            """
        ).fetchall()
        grouped: dict[tuple[str, datetime], list[tuple]] = defaultdict(list)
        for row in rows:
            try:
                open_time = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
                close_time = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00"))
            except ValueError:
                continue
            if open_time.tzinfo is None:
                open_time = open_time.replace(tzinfo=timezone.utc)
            else:
                open_time = open_time.astimezone(timezone.utc)
            if close_time.tzinfo is None:
                close_time = close_time.replace(tzinfo=timezone.utc)
            else:
                close_time = close_time.astimezone(timezone.utc)
            epoch = int(open_time.timestamp())
            bucket = datetime.fromtimestamp(epoch - (epoch % 14400), tz=timezone.utc)
            grouped[(str(row[0]), bucket)].append((row, close_time))

        inserted = 0
        for (symbol, bucket), items in grouped.items():
            bucket_end = bucket + timedelta(hours=4)
            if max(item[1] for item in items) < bucket_end:
                continue
            ordered = sorted(items, key=lambda item: item[1])
            source = [item[0] for item in ordered]
            cursor = connection.execute(
                """
                INSERT OR REPLACE INTO candles(
                  symbol,timeframe,open_time,close_time,open,high,low,close,ticks
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    symbol,
                    "4h",
                    bucket.isoformat(),
                    bucket_end.isoformat(),
                    float(source[0][3]),
                    max(float(row[4]) for row in source),
                    min(float(row[5]) for row in source),
                    float(source[-1][6]),
                    sum(int(row[7] or 0) for row in source),
                ),
            )
            inserted += max(0, int(cursor.rowcount or 0))
        connection.commit()
        return inserted
    finally:
        connection.close()


def _parse_payload_strategy(raw: str | None) -> tuple[str, str | None]:
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    strategy = str(metadata.get("strategy") or "ICT_CONFLUENCE").upper()
    grade = (
        (metadata.get("a_plus_context") or {}).get("quality_grade")
        if isinstance(metadata.get("a_plus_context"), dict)
        else None
    )
    return strategy, str(grade) if grade else None


def _macro_direction_4h(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT open_time,close_time,open,high,low,close
        FROM candles WHERE symbol='GC' AND timeframe='4h'
        ORDER BY open_time DESC LIMIT 18
        """
    ).fetchall()
    rows = list(reversed(rows))
    if len(rows) < 6:
        return {
            "timeframe": "4h",
            "direction": "unknown",
            "bars": len(rows),
            "source": "warmup",
            "note": "4H macro context is still warming up.",
        }
    recent = rows[-6:]
    first = sum(float(row[5]) for row in recent[:3]) / 3.0
    second = sum(float(row[5]) for row in recent[3:]) / 3.0
    avg_range = sum(max(float(row[3]) - float(row[4]), 1e-9) for row in recent) / 6.0
    delta = second - first
    threshold = avg_range * 0.15
    if delta > threshold:
        direction = "bullish"
    elif delta < -threshold:
        direction = "bearish"
    else:
        direction = "neutral"
    return {
        "timeframe": "4h",
        "direction": direction,
        "bars": len(rows),
        "source": "4h_close_momentum",
        "last_close": float(rows[-1][5]),
        "last_close_time": rows[-1][1],
        "delta": round(delta, 4),
        "threshold": round(threshold, 4),
        "note": f"4H macro structure is {direction}. Context only; 4H does not execute trades.",
    }


def _count(connection: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = connection.execute(sql, params).fetchone()
    return int(row[0] if row else 0)


def _actual_training_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT t.run_id,t.setup_id,t.timeframe,t.direction,t.result,t.result_r,
               t.result_dollars,t.closed_at,d.payload_json,
               m.strategy,m.mfe_r,m.mae_r,m.duration_seconds,m.outcome_class
        FROM training_trades_72t t
        LEFT JOIN training_decisions_72t d
          ON d.run_id=t.run_id AND d.setup_id=t.setup_id
        LEFT JOIN training_trade_metrics_72t m
          ON m.run_id=t.run_id AND m.setup_id=t.setup_id
        WHERE t.symbol='GC' AND t.status='CLOSED'
          AND t.result IN ('WIN','LOSS') AND t.result_r IS NOT NULL
        ORDER BY COALESCE(t.closed_at,t.updated_at) ASC
        """
    ).fetchall()
    output = []
    for row in rows:
        strategy_from_payload, grade = _parse_payload_strategy(row[8])
        output.append(
            {
                "run_id": row[0],
                "setup_id": row[1],
                "timeframe": row[2],
                "direction": row[3],
                "result": row[4],
                "result_r": float(row[5] or 0.0),
                "result_dollars": float(row[6] or 0.0),
                "closed_at": row[7],
                "strategy": str(row[9] or strategy_from_payload).upper(),
                "grade": grade,
                "mfe_r": None if row[10] is None else float(row[10]),
                "mae_r": None if row[11] is None else float(row[11]),
                "duration_seconds": None if row[12] is None else float(row[12]),
                "outcome_class": row[13],
            }
        )
    return output


def _shadow_ranker(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["timeframe"]), str(row["strategy"]))].append(row)
    ranked = []
    for (timeframe, strategy), items in groups.items():
        n = len(items)
        wins = sum(1 for item in items if item["result"] == "WIN")
        win_rate = wins / n * 100.0 if n else 0.0
        total_r = sum(float(item["result_r"]) for item in items)
        avg_r = total_r / n if n else 0.0
        mfes = [float(item["mfe_r"]) for item in items if item.get("mfe_r") is not None]
        maes = [float(item["mae_r"]) for item in items if item.get("mae_r") is not None]
        evidence = min(1.0, n / 30.0)
        raw_score = 50.0 + avg_r * 18.0 + (win_rate - 50.0) * 0.25
        score = max(0.0, min(100.0, 50.0 + (raw_score - 50.0) * evidence))
        if n < 10:
            status = "COLLECTING"
        elif score >= 62:
            status = "PROMISING"
        elif score >= 52:
            status = "WATCH"
        else:
            status = "WEAK"
        ranked.append(
            {
                "timeframe": timeframe,
                "strategy": strategy,
                "sample": n,
                "wins": wins,
                "losses": n - wins,
                "win_rate": round(win_rate, 2),
                "total_r": round(total_r, 3),
                "avg_r": round(avg_r, 3),
                "avg_mfe_r": round(sum(mfes) / len(mfes), 3) if mfes else None,
                "avg_mae_r": round(sum(maes) / len(maes), 3) if maes else None,
                "confidence": round(evidence * 100.0, 1),
                "evidence_score": round(score, 1),
                "status": status,
            }
        )
    return sorted(ranked, key=lambda item: (item["evidence_score"], item["sample"]), reverse=True)


def _walk_forward(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 20:
        return {
            "status": "COLLECTING",
            "sample": len(rows),
            "minimum_sample": 20,
            "note": "Need at least 20 closed Gold outcomes before the first chronological shadow holdout check.",
        }
    split = max(10, min(len(rows) - 5, int(len(rows) * 0.70)))
    train = rows[:split]
    test = rows[split:]
    lanes: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in train:
        lanes[(row["timeframe"], row["strategy"])].append(float(row["result_r"]))
    preferred = {
        lane
        for lane, values in lanes.items()
        if len(values) >= 3 and (sum(values) / len(values)) > 0.10
    }
    selected = [row for row in test if (row["timeframe"], row["strategy"]) in preferred]
    all_test_r = sum(float(row["result_r"]) for row in test)
    selected_r = sum(float(row["result_r"]) for row in selected)
    return {
        "status": "SHADOW_ONLY",
        "train_sample": len(train),
        "test_sample": len(test),
        "preferred_lanes": [f"{tf} · {strategy}" for tf, strategy in sorted(preferred)],
        "selected_test_sample": len(selected),
        "selected_test_total_r": round(selected_r, 3),
        "selected_test_avg_r": round(selected_r / len(selected), 3) if selected else None,
        "all_test_total_r": round(all_test_r, 3),
        "all_test_avg_r": round(all_test_r / len(test), 3) if test else None,
        "note": "Chronological 70/30 holdout. Research-only; it cannot approve or place a trade.",
    }


def _extended_target_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [row for row in rows if row.get("mfe_r") is not None]
    if not measured:
        return {"sample": 0, "reached_1r": 0, "reached_2r": 0, "reached_3r": 0, "reached_4r": 0}
    return {
        "sample": len(measured),
        "reached_1r": sum(1 for row in measured if float(row["mfe_r"]) >= 1.0),
        "reached_2r": sum(1 for row in measured if float(row["mfe_r"]) >= 2.0),
        "reached_3r": sum(1 for row in measured if float(row["mfe_r"]) >= 3.0),
        "reached_4r": sum(1 for row in measured if float(row["mfe_r"]) >= 4.0),
        "avg_mfe_r": round(sum(float(row["mfe_r"]) for row in measured) / len(measured), 3),
        "note": "Measures how far completed trades traveled in our favor before exit/stop. Used to research T2/T3 runners without changing live exits.",
    }


def _backtest_run_count(research_db_path: Path) -> int:
    if not research_db_path.exists():
        return 0
    try:
        connection = sqlite3.connect(research_db_path, timeout=2)
        try:
            for table in ("backtest_runs", "research_runs", "runs"):
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if exists:
                    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        finally:
            connection.close()
    except sqlite3.Error:
        return 0
    return 0


def training_snapshot_72t(live_db_path: Path, research_db_path: Path) -> dict[str, Any]:
    if not live_db_path.exists():
        return {"profile": "OTR_TRAINING_LAB_72T", "ok": False, "reason": "Live database not found"}
    connection = sqlite3.connect(live_db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        _ensure_training_schema(connection)
        run_id, build = _active_run(connection)
        actual_rows = _actual_training_rows(connection)
        ranker = _shadow_ranker(actual_rows)

        decisions_total = _count(connection, "SELECT COUNT(*) FROM training_decisions_72t WHERE symbol='GC'")
        decisions_current = _count(
            connection,
            "SELECT COUNT(*) FROM training_decisions_72t WHERE symbol='GC' AND run_id=?",
            (run_id,),
        ) if run_id else 0
        accepted_total = _count(
            connection,
            "SELECT COUNT(*) FROM training_decisions_72t WHERE symbol='GC' AND status IN ('REGISTERED','PENDING','OPEN','CLOSED')",
        )
        blocked_total = decisions_total - accepted_total
        closed_total = len(actual_rows)
        wins_total = sum(1 for row in actual_rows if row["result"] == "WIN")
        losses_total = closed_total - wins_total

        counter_total = _count(connection, "SELECT COUNT(*) FROM training_counterfactuals_72t WHERE symbol='GC'")
        counter_resolved = _count(
            connection,
            "SELECT COUNT(*) FROM training_counterfactuals_72t WHERE symbol='GC' AND outcome<>'OPEN'",
        )
        counter_wins = _count(
            connection,
            "SELECT COUNT(*) FROM training_counterfactuals_72t WHERE symbol='GC' AND outcome='WOULD_WIN'",
        )
        counter_losses = _count(
            connection,
            "SELECT COUNT(*) FROM training_counterfactuals_72t WHERE symbol='GC' AND outcome='WOULD_LOSE'",
        )
        missed_total = _count(
            connection,
            "SELECT COUNT(*) FROM market_lessons WHERE symbol='GC' AND setup_found=0",
        ) if _table_exists(connection, "market_lessons") else 0
        lesson_total = _count(
            connection,
            "SELECT COUNT(*) FROM market_lessons WHERE symbol='GC'",
        ) if _table_exists(connection, "market_lessons") else 0
        shadow_closed = _count(
            connection,
            "SELECT COUNT(*) FROM training_shadow_72t WHERE symbol='GC' AND status='CLOSED' AND result_r IS NOT NULL",
        )

        recent_missed = []
        if _table_exists(connection, "market_lessons"):
            recent_missed = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT lesson_id,timeframe,direction,move_points,threshold_points,
                           setup_status,block_reason,summary,started_at,ended_at
                    FROM market_lessons
                    WHERE symbol='GC' AND setup_found=0
                    ORDER BY ended_at DESC LIMIT 12
                    """
                ).fetchall()
            ]

        recent_counterfactuals = [
            dict(row)
            for row in connection.execute(
                """
                SELECT run_id,setup_id,timeframe,direction,blocked_status,blocked_reason,
                       outcome,max_favorable_r,max_adverse_r,created_at,resolved_at
                FROM training_counterfactuals_72t
                WHERE symbol='GC'
                ORDER BY COALESCE(resolved_at,last_checked,created_at) DESC LIMIT 12
                """
            ).fetchall()
        ]

        top_features = []
        if _table_exists(connection, "learning_feature_stats"):
            top_features = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT feature,lesson_hits,total_move_points,bullish_hits,bearish_hits
                    FROM learning_feature_stats
                    ORDER BY lesson_hits DESC,total_move_points DESC LIMIT 12
                    """
                ).fetchall()
            ]

        recent_decisions = []
        for row in connection.execute(
            """
            SELECT run_id,setup_id,timeframe,direction,trigger_type,risk_reward,status,
                   payload_json,created_at
            FROM training_decisions_72t
            WHERE symbol='GC'
            ORDER BY created_at DESC LIMIT 12
            """
        ).fetchall():
            item = dict(row)
            strategy, grade = _parse_payload_strategy(item.pop("payload_json", None))
            item["strategy"] = strategy
            item["grade"] = grade
            recent_decisions.append(item)

        decision_progress = min(100.0, decisions_total / 500.0 * 100.0)
        outcome_progress = min(100.0, (closed_total + counter_resolved) / 150.0 * 100.0)
        missed_progress = min(100.0, missed_total / 50.0 * 100.0)
        ranker_progress = min(100.0, closed_total / 100.0 * 100.0)
        readiness = round((decision_progress + outcome_progress + missed_progress + ranker_progress) / 4.0, 1)
        if readiness >= 80 and closed_total >= 60:
            readiness_stage = "WALK_FORWARD_READY"
        elif readiness >= 50:
            readiness_stage = "SHADOW_READY"
        else:
            readiness_stage = "COLLECTING"

        return {
            "profile": "OTR_TRAINING_LAB_72T",
            "ok": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "build": build,
            "research_only": True,
            "control_authority": "NONE",
            "macro_4h": _macro_direction_4h(connection),
            "corpus": {
                "decisions": decisions_total,
                "current_run_decisions": decisions_current,
                "accepted_decisions": accepted_total,
                "blocked_decisions": max(0, blocked_total),
                "closed_actual_trades": closed_total,
                "wins": wins_total,
                "losses": losses_total,
                "counterfactuals": counter_total,
                "resolved_counterfactuals": counter_resolved,
                "counterfactual_would_win": counter_wins,
                "counterfactual_would_lose": counter_losses,
                "market_lessons": lesson_total,
                "missed_opportunities": missed_total,
                "shadow_closed": shadow_closed,
                "backtest_runs": _backtest_run_count(research_db_path),
            },
            "readiness": {
                "overall": readiness,
                "stage": readiness_stage,
                "decision_recorder": round(decision_progress, 1),
                "outcome_labels": round(outcome_progress, 1),
                "missed_move_library": round(missed_progress, 1),
                "shadow_ranker": round(ranker_progress, 1),
                "note": "Readiness measures research sample depth only. It is not a profitability or deployment guarantee.",
            },
            "shadow_ranker": ranker[:12],
            "walk_forward": _walk_forward(actual_rows),
            "extended_targets": _extended_target_audit(actual_rows),
            "recent_missed": recent_missed,
            "recent_counterfactuals": recent_counterfactuals,
            "recent_decisions": recent_decisions,
            "top_learning_features": top_features,
        }
    finally:
        connection.close()
