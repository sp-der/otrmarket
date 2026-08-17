from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.strategies.ict import detect_displacement, detect_fvg, detect_liquidity_sweep, detect_smt
from src.strategies.regime import classify_regime
from src.strategies.structure import detect_swings

WINDOW_BARS = {"5m": 6, "15m": 3, "30m": 2}
POINT_FLOORS = {"NQ": 30.0, "ES": 8.0, "GC": 6.0}


def ensure_learning_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_lessons (
            lesson_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT NOT NULL,
            move_points REAL NOT NULL,
            threshold_points REAL NOT NULL,
            setup_found INTEGER NOT NULL DEFAULT 0,
            setup_status TEXT,
            block_reason TEXT,
            features_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_market_lessons_symbol_time
        ON market_lessons(symbol, timeframe, ended_at);
        CREATE TABLE IF NOT EXISTS learning_feature_stats (
            feature TEXT PRIMARY KEY,
            lesson_hits INTEGER NOT NULL DEFAULT 0,
            total_move_points REAL NOT NULL DEFAULT 0,
            bullish_hits INTEGER NOT NULL DEFAULT 0,
            bearish_hits INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        """
    )
    connection.commit()


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _threshold(symbol, start_price, prior):
    ranges = [max(c.range, 0.0) for c in prior[-12:]]
    avg_range = sum(ranges) / len(ranges) if ranges else 0.0
    fixed = abs(start_price) * 0.0035 if symbol == "BTC-USD" else POINT_FLOORS.get(symbol, 5.0)
    return max(fixed, avg_range * 1.8)


def _mss(prior, early, direction):
    swings = detect_swings(prior)
    kind = "low" if direction == "bearish" else "high"
    swing = next((s for s in reversed(swings) if s.kind == kind), None)
    if not swing:
        return False, None
    if direction == "bearish":
        return any(c.close < swing.price for c in early), swing.price
    return any(c.close > swing.price for c in early), swing.price


def _pair_smt(symbol, timeframe, histories, cutoff, direction):
    if symbol not in {"NQ", "ES"}:
        return False
    pair = "ES" if symbol == "NQ" else "NQ"
    own = [c for c in histories.get((symbol, timeframe), []) if c.close_time <= cutoff]
    other = [c for c in histories.get((pair, timeframe), []) if c.close_time <= cutoff]
    for first, second in ((own, other), (other, own)):
        if first and second:
            smt = detect_smt(first, second)
            if smt and smt.direction == direction:
                return True
    return False


def _setup_audit(connection, symbol, timeframe, start, end):
    rows = connection.execute(
        """
        SELECT status, payload_json FROM strategy_setups
        WHERE symbol=? AND timeframe=? AND created_at>=? AND created_at<=?
        ORDER BY created_at ASC
        """,
        (symbol, timeframe, start.isoformat(), end.isoformat()),
    ).fetchall()
    if not rows:
        return False, None, None
    status, payload_json = rows[-1]
    reason = None
    try:
        payload = json.loads(payload_json or "{}")
        gate = payload.get("metadata", {}).get("execution_quality_gate", {}) or {}
        if not gate.get("allowed", True):
            reason = gate.get("reason")
    except (TypeError, ValueError):
        pass
    return True, status, reason


def observe_market_opportunity(connection, symbol, timeframe, histories):
    """Learn from completed large moves without using future data to place a trade."""
    bars = WINDOW_BARS.get(timeframe)
    candles = histories.get((symbol, timeframe), [])
    if bars is None or len(candles) < max(22, bars + 12):
        return None

    window = candles[-bars:]
    prior = candles[:-bars]
    started_at = window[0].open_time
    ended_at = window[-1].close_time
    start_price = float(window[0].open)
    signed_move = float(window[-1].close) - start_price
    threshold = _threshold(symbol, start_price, prior)
    if abs(signed_move) < threshold:
        return None

    direction = "bullish" if signed_move > 0 else "bearish"
    move_points = abs(signed_move)
    ensure_learning_schema(connection)

    previous = connection.execute(
        "SELECT ended_at FROM market_lessons WHERE symbol=? AND timeframe=? AND direction=? ORDER BY ended_at DESC LIMIT 1",
        (symbol, timeframe, direction),
    ).fetchone()
    if previous:
        previous_end = _parse(previous[0])
        if previous_end and started_at <= previous_end:
            return None

    early = window[: min(3, len(window))]
    regime_before = classify_regime(prior[-30:])
    mss, break_level = _mss(prior, early, direction)
    sweep = False
    displacement = None
    fvg = False
    for candle in early:
        available = [c for c in candles if c.close_time <= candle.close_time]
        found_sweep = detect_liquidity_sweep(available)
        if found_sweep and found_sweep.direction == direction:
            sweep = True
        found_displacement = detect_displacement(available)
        if found_displacement and found_displacement.direction == direction:
            if displacement is None or found_displacement.body_ratio > displacement.body_ratio:
                displacement = found_displacement
        found_fvg = detect_fvg(available)
        if found_fvg and found_fvg.direction == direction:
            fvg = True

    smt = _pair_smt(symbol, timeframe, histories, early[-1].close_time, direction)
    setup_found, setup_status, block_reason = _setup_audit(connection, symbol, timeframe, started_at, ended_at)
    body_ratio = float(getattr(displacement, "body_ratio", 0.0) or 0.0)
    range_ratio = float(getattr(displacement, "range_ratio", 0.0) or 0.0)
    features = {
        "regime_before": regime_before,
        "mss": mss,
        "break_level": break_level,
        "liquidity_sweep": sweep,
        "smt": smt,
        "displacement": displacement is not None,
        "displacement_body_ratio": body_ratio,
        "displacement_range_ratio": range_ratio,
        "fvg": fvg,
        "setup_found": setup_found,
        "setup_status": setup_status,
        "block_reason": block_reason,
    }

    clues = []
    if sweep:
        clues.append("liquidity sweep")
    if smt:
        clues.append("SMT")
    if mss:
        clues.append("MSS/CHOCH")
    if displacement:
        clues.append(f"{body_ratio:.2f}x/{range_ratio:.2f}x displacement")
    if fvg:
        clues.append("fresh FVG")
    clue_text = ", ".join(clues) if clues else "no strong early trigger cluster"
    action = f"candidate status {setup_status}" if setup_found else "no strategy candidate was produced"
    if block_reason:
        action += f"; blocker: {block_reason}"
    summary = (
        f"{direction.title()} {symbol} {timeframe} move of {move_points:.2f} points. "
        f"Early clues: {clue_text}. The engine had {action}."
    )

    raw = f"{symbol}|{timeframe}|{direction}|{started_at.isoformat()}|{ended_at.isoformat()}"
    lesson_id = hashlib.sha1(raw.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO market_lessons (
            lesson_id,symbol,timeframe,direction,started_at,ended_at,move_points,
            threshold_points,setup_found,setup_status,block_reason,features_json,summary,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (lesson_id, symbol, timeframe, direction, started_at.isoformat(), ended_at.isoformat(),
         move_points, threshold, int(setup_found), setup_status, block_reason,
         json.dumps(features, sort_keys=True), summary, now),
    )
    if cursor.rowcount == 0:
        return None

    feature_flags = {
        "MSS_CHOCH": mss,
        "LIQUIDITY_SWEEP": sweep,
        "SMT": smt,
        "DISPLACEMENT": displacement is not None,
        "FVG": fvg,
        f"REGIME_{regime_before.get('regime', 'UNKNOWN')}": True,
    }
    for feature, present in feature_flags.items():
        if not present:
            continue
        connection.execute(
            """
            INSERT INTO learning_feature_stats(feature,lesson_hits,total_move_points,bullish_hits,bearish_hits,updated_at)
            VALUES (?,1,?,?,?,?)
            ON CONFLICT(feature) DO UPDATE SET
              lesson_hits=lesson_hits+1,
              total_move_points=total_move_points+excluded.total_move_points,
              bullish_hits=bullish_hits+excluded.bullish_hits,
              bearish_hits=bearish_hits+excluded.bearish_hits,
              updated_at=excluded.updated_at
            """,
            (feature, move_points, int(direction == "bullish"), int(direction == "bearish"), now),
        )
    connection.commit()
    return {"lesson_id": lesson_id, "summary": summary, "features": features}


def learning_snapshot(db_path: Path) -> dict:
    if not db_path.exists():
        return {"profile": "MARKET_LEARNING_5_8", "lessons": 0, "recent": [], "top_features": []}
    connection = sqlite3.connect(db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        ensure_learning_schema(connection)
        count = int(connection.execute("SELECT COUNT(*) FROM market_lessons").fetchone()[0])
        recent = [dict(row) for row in connection.execute(
            "SELECT lesson_id,symbol,timeframe,direction,move_points,setup_found,setup_status,block_reason,summary,ended_at FROM market_lessons ORDER BY ended_at DESC LIMIT 12"
        ).fetchall()]
        top = [dict(row) for row in connection.execute(
            "SELECT feature,lesson_hits,total_move_points,bullish_hits,bearish_hits FROM learning_feature_stats ORDER BY lesson_hits DESC,total_move_points DESC LIMIT 10"
        ).fetchall()]
        return {
            "profile": "MARKET_LEARNING_5_8",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lessons": count,
            "recent": recent,
            "top_features": top,
        }
    finally:
        connection.close()
