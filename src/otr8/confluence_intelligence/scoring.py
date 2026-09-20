"""Deterministic, explainable shadow scoring for Operation 8.2.

SHADOW RESEARCH ONLY -- nothing here can allow, block, resize, or otherwise
affect a real/paper OTR trade. It only produces a research opinion to be
compared, after the fact, against what OTR 8.1 actually did.

Every weight below is a plain, documented constant (no hidden tuning, no
per-request "magic" adjustment). Missing evidence is never guessed: a score
component that cannot be computed from already-known evidence is None, and
the combined score/shadow_action degrade to INSUFFICIENT_DATA rather than
faking confidence.
"""

from __future__ import annotations

from typing import Any

from .features import HTF_VOTE_TIMEFRAMES, UNKNOWN

# Local setup quality by OTR's own grade vocabulary. A+ is OTR's top ICT
# quality tier; PREVIEW/RESEARCH setups never reach registration so they only
# appear here for candidates that were blocked before the arbiter.
GRADE_POINTS = {
    "A+": 100.0,
    "A": 80.0,
    "A_COUNTERTREND": 70.0,
    "B+": 60.0,
    "B": 40.0,
    "PREVIEW": 20.0,
    "RESEARCH": 20.0,
}

# Combined-score weights. local_quality and htf_alignment are the two signals
# this layer exists to compare (OTR's own grade vs. the wider chart context);
# together they carry most of the weight. context/liquidity are supporting
# evidence and degrade gracefully (renormalized) rather than gating the score
# if unavailable -- but local_quality and htf_alignment are load-bearing: if
# either is unknown, the combined score is INSUFFICIENT_DATA, never a guess.
SCORE_WEIGHTS = {
    "local_quality": 0.35,
    "htf_alignment": 0.30,
    "context": 0.20,
    "liquidity": 0.15,
}

# shadow_action thresholds on the 0-100 combined score. DOWNGRADE only fires
# in the 60-79 band when HTF context actively conflicts with the local
# trigger -- a mid-scoring setup with clean HTF agreement is left as ALLOW.
ALLOW_THRESHOLD = 80.0
DOWNGRADE_OR_ALLOW_THRESHOLD = 60.0
WAIT_THRESHOLD = 40.0


def local_quality_score(features: dict[str, Any]) -> float | None:
    context = features.get("context", {})
    grade = str(context.get("grade") or UNKNOWN).upper()
    if grade in GRADE_POINTS:
        return GRADE_POINTS[grade]
    score = context.get("checklist_score")
    total = context.get("checklist_total")
    if score is None or not total:
        return None
    return max(0.0, min(100.0, round(100.0 * float(score) / float(total), 2)))


def entry_quality_score(features: dict[str, Any]) -> float | None:
    pd_arrays = features.get("pd_arrays", {})
    momentum = features.get("momentum", {})
    points = 0.0
    signals = 0
    if pd_arrays.get("entry_fvg"):
        signals += 1
        points += 25.0
    if pd_arrays.get("active_order_blocks"):
        signals += 1
        points += 20.0
    if pd_arrays.get("premium_discount_zone") in {"premium", "discount"}:
        signals += 1
        points += 20.0
    body = momentum.get("displacement_body_ratio")
    rng = momentum.get("displacement_range_ratio")
    if body is not None and rng is not None:
        signals += 1
        points += min(35.0, (float(body) + float(rng)) * 8.0)
    if signals == 0:
        return None
    return max(0.0, min(100.0, round(points, 2)))


def liquidity_score(features: dict[str, Any]) -> float | None:
    liquidity = features.get("liquidity", {})
    points = 0.0
    signals = 0
    if liquidity.get("liquidity_sweep"):
        signals += 1
        points += 40.0
    if liquidity.get("equal_highs") or liquidity.get("equal_lows"):
        signals += 1
        points += 30.0
    if liquidity.get("previous_session_high") is not None or liquidity.get("previous_session_low") is not None:
        signals += 1
        points += 30.0
    if signals == 0:
        return None
    return max(0.0, min(100.0, round(points, 2)))


def context_score(features: dict[str, Any]) -> float | None:
    context = features.get("context", {})
    narrative_score = context.get("market_narrative_score")
    session = context.get("session")
    samples = []
    if narrative_score is not None:
        samples.append(float(narrative_score))
    if session == "MAINTENANCE":
        samples.append(20.0)
    elif session not in (None, UNKNOWN):
        samples.append(70.0)
    if not samples:
        return None
    return max(0.0, min(100.0, round(sum(samples) / len(samples), 2)))


def htf_conflict(features: dict[str, Any], direction: str) -> dict[str, Any]:
    """The Higher-Timeframe Conflict Engine (Operation 8.2 Step 3).

    Separates the LOCAL TRIGGER (the setup's own execution timeframe) from
    the TRADE THESIS (5m/15m/30m/1h/4h context). A timeframe with no known
    structure (UNKNOWN, e.g. insufficient candle history) is excluded from
    every count rather than being treated as neutral or aligned.
    """
    structure = features.get("structure", {})
    bullish = bearish = mixed = 0
    for timeframe in HTF_VOTE_TIMEFRAMES:
        vote = structure.get(timeframe, {}).get("direction", UNKNOWN)
        if vote == "bullish":
            bullish += 1
        elif vote == "bearish":
            bearish += 1
        elif vote in {"mixed", "neutral"}:
            mixed += 1

    total_votes = bullish + bearish + mixed
    if total_votes == 0:
        return {
            "htf_bullish_count": 0,
            "htf_bearish_count": 0,
            "htf_mixed_count": 0,
            "htf_alignment_direction": UNKNOWN,
            "htf_alignment_score": None,
            "htf_conflict_level": UNKNOWN,
            "execution_vs_htf_conflict": None,
        }

    if bullish > bearish:
        alignment_direction = "bullish"
    elif bearish > bullish:
        alignment_direction = "bearish"
    else:
        alignment_direction = "mixed"

    agree = bullish if direction == "bullish" else bearish if direction == "bearish" else 0
    oppose = bearish if direction == "bullish" else bullish if direction == "bearish" else 0
    alignment_score = round(100.0 * agree / total_votes, 2)
    oppose_fraction = oppose / total_votes

    if oppose_fraction >= 0.5:
        conflict_level = "HIGH"
    elif oppose_fraction > 0 or mixed > 0:
        conflict_level = "MODERATE"
    else:
        conflict_level = "LOW"

    return {
        "htf_bullish_count": bullish,
        "htf_bearish_count": bearish,
        "htf_mixed_count": mixed,
        "htf_alignment_direction": alignment_direction,
        "htf_alignment_score": alignment_score,
        "htf_conflict_level": conflict_level,
        "execution_vs_htf_conflict": bool(direction != alignment_direction and alignment_direction != "mixed"),
    }


def combined_score(
    local_quality: float | None,
    htf_alignment: float | None,
    context: float | None,
    liquidity: float | None,
) -> float | None:
    """Weighted blend of the four component scores.

    local_quality and htf_alignment are load-bearing: either missing makes
    the combined score INSUFFICIENT_DATA (None here). context/liquidity are
    supporting evidence -- if unavailable, their weight is simply dropped and
    the remaining weights are renormalized, rather than blocking a verdict.
    """
    if local_quality is None or htf_alignment is None:
        return None
    total = local_quality * SCORE_WEIGHTS["local_quality"] + htf_alignment * SCORE_WEIGHTS["htf_alignment"]
    weight_sum = SCORE_WEIGHTS["local_quality"] + SCORE_WEIGHTS["htf_alignment"]
    if context is not None:
        total += context * SCORE_WEIGHTS["context"]
        weight_sum += SCORE_WEIGHTS["context"]
    if liquidity is not None:
        total += liquidity * SCORE_WEIGHTS["liquidity"]
        weight_sum += SCORE_WEIGHTS["liquidity"]
    if weight_sum <= 0:
        return None
    return round(max(0.0, min(100.0, total / weight_sum)), 2)


def shadow_action_for(combined: float | None, conflict_level: str) -> str:
    if combined is None:
        return "INSUFFICIENT_DATA"
    if combined >= ALLOW_THRESHOLD:
        return "ALLOW"
    if combined >= DOWNGRADE_OR_ALLOW_THRESHOLD:
        return "DOWNGRADE" if conflict_level in {"HIGH", "MODERATE"} else "ALLOW"
    if combined >= WAIT_THRESHOLD:
        return "WAIT"
    return "REJECT"


def build_reasons(features: dict[str, Any], htf: dict[str, Any], direction: str) -> list[str]:
    reasons: list[str] = []
    structure = features.get("structure", {})
    for timeframe in HTF_VOTE_TIMEFRAMES:
        vote = structure.get(timeframe, {}).get("direction", UNKNOWN)
        if vote != UNKNOWN:
            reasons.append(f"{timeframe} structure {vote}")

    local_timeframe = None
    for timeframe, snapshot in structure.items():
        if timeframe not in HTF_VOTE_TIMEFRAMES and snapshot.get("direction", UNKNOWN) != UNKNOWN:
            local_timeframe = timeframe
            break
    if local_timeframe:
        reasons.append(f"{local_timeframe} local setup {structure[local_timeframe]['direction']}")

    pd_arrays = features.get("pd_arrays", {})
    if pd_arrays.get("entry_fvg"):
        entry_fvg_direction = pd_arrays["entry_fvg"].get("direction")
        reasons.append(f"fresh {entry_fvg_direction or ''} FVG present".replace("  ", " ").strip())
    if pd_arrays.get("active_order_blocks"):
        reasons.append("order block present")

    liquidity = features.get("liquidity", {})
    if liquidity.get("liquidity_sweep"):
        reasons.append("liquidity sweep present")

    if htf.get("htf_conflict_level") in {"HIGH", "MODERATE"}:
        reasons.append(
            f"HTF alignment {htf.get('htf_alignment_direction')} conflicts with {direction} thesis "
            f"({htf.get('htf_bullish_count')} bullish / {htf.get('htf_bearish_count')} bearish / "
            f"{htf.get('htf_mixed_count')} mixed)"
        )
    return reasons


def score_setup(setup, features: dict[str, Any]) -> dict[str, Any]:
    """Full deterministic shadow score for one setup's already-extracted
    feature bundle. Pure function: same features always produce the same
    score, so results are reproducible and testable without a live setup.
    """
    direction = str(getattr(setup, "direction", "") or "")
    local_quality = local_quality_score(features)
    entry_quality = entry_quality_score(features)
    liquidity = liquidity_score(features)
    context = context_score(features)
    htf = htf_conflict(features, direction)
    combined = combined_score(local_quality, htf.get("htf_alignment_score"), context, liquidity)
    action = shadow_action_for(combined, htf.get("htf_conflict_level", UNKNOWN))
    reasons = build_reasons(features, htf, direction)

    return {
        "local_quality_score": local_quality,
        "htf_alignment_score": htf.get("htf_alignment_score"),
        "context_score": context,
        "liquidity_score": liquidity,
        "entry_quality_score": entry_quality,
        "combined_shadow_score": combined,
        "shadow_action": action,
        "shadow_reasons": reasons,
        "htf_bullish_count": htf.get("htf_bullish_count"),
        "htf_bearish_count": htf.get("htf_bearish_count"),
        "htf_mixed_count": htf.get("htf_mixed_count"),
        "htf_alignment_direction": htf.get("htf_alignment_direction"),
        "htf_conflict_level": htf.get("htf_conflict_level"),
        "execution_vs_htf_conflict": htf.get("execution_vs_htf_conflict"),
    }
