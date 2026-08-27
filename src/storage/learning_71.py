from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

from src.storage import learning as base_learning
from src.strategies.market_intelligence import build_market_map


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


def _update_feature_stat(
    connection: sqlite3.Connection,
    feature: str,
    move_points: float,
    direction: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO learning_feature_stats(
            feature,lesson_hits,total_move_points,bullish_hits,bearish_hits,updated_at
        ) VALUES (?,1,?,?,?,?)
        ON CONFLICT(feature) DO UPDATE SET
          lesson_hits=lesson_hits+1,
          total_move_points=total_move_points+excluded.total_move_points,
          bullish_hits=bullish_hits+excluded.bullish_hits,
          bearish_hits=bearish_hits+excluded.bearish_hits,
          updated_at=excluded.updated_at
        """,
        (
            feature,
            move_points,
            int(direction == "bullish"),
            int(direction == "bearish"),
            now,
        ),
    )


def _market_features(symbol: str, timeframe: str, histories, cutoff: datetime, direction: str) -> tuple[dict, list[str]]:
    market_map = build_market_map(symbol, timeframe, histories, cutoff)
    execution = market_map.get("timeframes", {}).get(timeframe, {})

    structural_votes = []
    for tf in ("5m", "15m", "30m", "1h"):
        vote = (
            market_map.get("timeframes", {})
            .get(tf, {})
            .get("structure", {})
            .get("direction")
        )
        if vote in {"bullish", "bearish"}:
            structural_votes.append(vote)
    aligned_votes = sum(vote == direction for vote in structural_votes)
    opposed_votes = sum(vote != direction for vote in structural_votes)

    dealing = execution.get("dealing_range", {})
    equal = execution.get("equal_liquidity", {})
    fvg = execution.get("fvgs", {})
    blocks = execution.get("order_blocks", {})
    rejection = execution.get("rejection", {})
    pair_smt = market_map.get("pair_smt")
    session = market_map.get("session_liquidity", {}).get("session")

    same_active_fvg = any(item.get("direction") == direction for item in fvg.get("active", []))
    same_inverse_fvg = any(
        item.get("direction") == direction and item.get("retested")
        for item in fvg.get("inverse", [])
    )
    same_order_block = any(item.get("direction") == direction for item in blocks.get("active", []))
    same_breaker = any(item.get("direction") == direction for item in blocks.get("breaker_candidates", []))
    target_equal_key = "equal_highs" if direction == "bullish" else "equal_lows"
    target_equal_liquidity = bool(equal.get(target_equal_key))
    favorable_dealing_range = (
        (direction == "bullish" and dealing.get("zone") == "discount")
        or (direction == "bearish" and dealing.get("zone") == "premium")
    )
    matching_rejection = rejection.get("signal") == direction
    matching_smt = bool(pair_smt and pair_smt.get("direction") == direction)

    intelligence = {
        "profile": "MARKET_LEARNING_INTELLIGENCE_7_1",
        "structural_votes": structural_votes,
        "aligned_votes": aligned_votes,
        "opposed_votes": opposed_votes,
        "dealing_range_zone": dealing.get("zone"),
        "favorable_dealing_range": favorable_dealing_range,
        "target_equal_liquidity": target_equal_liquidity,
        "same_direction_active_fvg": same_active_fvg,
        "same_direction_inverse_fvg_retest": same_inverse_fvg,
        "same_direction_order_block": same_order_block,
        "same_direction_breaker_candidate": same_breaker,
        "matching_rejection": matching_rejection,
        "matching_pair_smt": matching_smt,
        "session": session,
        "market_map": market_map,
    }

    flags = []
    if len(structural_votes) >= 2 and aligned_votes >= max(2, opposed_votes):
        flags.append("MTF_STRUCTURE_ALIGNED")
    if favorable_dealing_range:
        flags.append("FAVORABLE_DEALING_RANGE")
    if target_equal_liquidity:
        flags.append("TARGET_EQUAL_LIQUIDITY")
    if same_active_fvg:
        flags.append("ACTIVE_FVG_ALIGNED")
    if same_inverse_fvg:
        flags.append("INVERSE_FVG_RETEST_ALIGNED")
    if same_order_block:
        flags.append("ORDER_BLOCK_ALIGNED")
    if same_breaker:
        flags.append("BREAKER_CANDIDATE_ALIGNED")
    if matching_rejection:
        flags.append("REJECTION_ALIGNED")
    if matching_smt:
        flags.append("SMT_MARKET_MAP_ALIGNED")
    if session:
        flags.append(f"SESSION_{session}")

    return intelligence, flags


def observe_market_opportunity_71(connection, symbol, timeframe, histories):
    """Preserve the 5.8 learner, then enrich new lessons with the 7.1 market map.

    The base learner still decides whether a completed move is large enough to
    become a lesson. This wrapper only adds context that was available at the
    lesson cutoff, so it cannot leak future candles into live decisions.
    """
    lesson = base_learning.observe_market_opportunity(
        connection,
        symbol,
        timeframe,
        histories,
    )
    if not lesson:
        return None

    row = connection.execute(
        """
        SELECT direction, ended_at, move_points, features_json, summary
        FROM market_lessons
        WHERE lesson_id = ?
        """,
        (lesson["lesson_id"],),
    ).fetchone()
    if row is None:
        return lesson

    direction, ended_at, move_points, features_json, summary = row
    cutoff = _parse(ended_at)
    if cutoff is None:
        return lesson

    try:
        features = json.loads(features_json or "{}")
    except (TypeError, ValueError):
        features = {}

    intelligence, feature_flags = _market_features(
        symbol,
        timeframe,
        histories,
        cutoff,
        str(direction),
    )
    features["market_intelligence"] = intelligence

    clues = []
    if intelligence["aligned_votes"]:
        clues.append(
            f"{intelligence['aligned_votes']} aligned HTF structure vote(s)"
        )
    if intelligence["favorable_dealing_range"]:
        clues.append(f"{intelligence['dealing_range_zone']} dealing range")
    if intelligence["target_equal_liquidity"]:
        clues.append("equal-liquidity draw")
    if intelligence["same_direction_order_block"]:
        clues.append("aligned order block")
    if intelligence["same_direction_breaker_candidate"]:
        clues.append("aligned breaker candidate")
    if intelligence["same_direction_inverse_fvg_retest"]:
        clues.append("inverse-FVG retest")
    if intelligence["matching_rejection"]:
        clues.append("matching rejection")
    map_text = ", ".join(clues) if clues else "no additional 7.1 market-map cluster"
    enriched_summary = f"{summary} Market map: {map_text}."

    connection.execute(
        "UPDATE market_lessons SET features_json=?, summary=? WHERE lesson_id=?",
        (
            json.dumps(features, sort_keys=True),
            enriched_summary,
            lesson["lesson_id"],
        ),
    )

    now = datetime.now(timezone.utc).isoformat()
    for feature in feature_flags:
        _update_feature_stat(
            connection,
            feature,
            float(move_points or 0.0),
            str(direction),
            now,
        )
    connection.commit()

    lesson["features"] = features
    lesson["summary"] = enriched_summary
    lesson["market_intelligence_features"] = feature_flags
    return lesson
