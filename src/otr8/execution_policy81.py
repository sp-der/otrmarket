from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.risk.geometry import normalize_trade_prices, validate_trade_geometry


FULL_RISK_DOLLARS = 750.0
REDUCED_RISK_DOLLARS = 500.0
MIN_EXECUTION_RISK_DOLLARS = 500.0
HARD_NO_CHASE_PROGRESS = 0.75

# These are pending-order lifetimes after registration. Strategy/thesis age is
# still owned by the strategy engines and is intentionally a separate clock.
PENDING_BARS_81 = {
    "1m": 12,
    "5m": 8,
    "15m": 5,
    "1h": 3,
}


@dataclass(frozen=True)
class RRDecision81:
    allowed: bool
    grade: str
    floor: float
    reason: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class ZoneDecision81:
    allowed: bool
    low: float
    high: float
    preferred_entry: float
    activation_entry: float
    risk_reward: float
    source: str
    reason: str


def _number(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _metadata(setup) -> dict:
    value = getattr(setup, "metadata", None)
    if not isinstance(value, dict):
        value = {}
        setup.metadata = value
    return value


def quality_grade81(setup) -> str:
    metadata = _metadata(setup)
    score = int(metadata.get("checklist_score", 0) or 0)
    total = int(metadata.get("checklist_total", 0) or 0)

    # The user-approved contract is explicit: fresh 5/6 Gold arms are A-tier,
    # never silently promoted to full-risk A+ because the surrounding market map
    # happens to score well.
    if metadata.get("candidate_source_80") == "EARLY_ARM_72H" and total >= 6:
        return "A" if score >= 5 else "PREVIEW"

    context = metadata.get("a_plus_context", {}) or {}
    grade = str(context.get("quality_grade") or "").upper()
    if grade:
        return grade

    strategy = str(metadata.get("strategy", "ICT_CONFLUENCE") or "ICT_CONFLUENCE").upper()
    if strategy == "REJECTION_BLOCK_10_10":
        return "A+" if score >= total >= 10 else "RESEARCH"
    if metadata.get("setup_quality") == "A_PLUS_STRUCTURE":
        return "A+"
    if strategy == "GOLD_MOMENTUM_PULLBACK_72R":
        return "A"
    if strategy == "TREND_CONTINUATION_REARM":
        narrative = metadata.get("multi_timeframe_narrative_62", {}) or {}
        return "A+" if narrative.get("strong_support") else "A"
    return "A"


def counterfactual_expectancy81(connection, setup, *, limit: int = 160) -> dict[str, Any]:
    """Summarize comparable resolved blocked setups without letting tiny samples steer policy."""
    strategy = str(_metadata(setup).get("strategy", "ICT_CONFLUENCE") or "ICT_CONFLUENCE")
    regime = str((_metadata(setup).get("gold_regime_80", {}) or {}).get("regime") or "")
    rows = []
    try:
        rows = connection.execute(
            """
            SELECT c.outcome,c.entry_price,c.stop_price,c.target_price,s.payload_json
            FROM counterfactual_setups c
            LEFT JOIN strategy_setups s ON s.setup_id=c.setup_id
            WHERE c.symbol=? AND c.timeframe=?
              AND c.outcome IN ('WOULD_WIN','WOULD_LOSE')
            ORDER BY COALESCE(c.resolved_at,c.created_at) DESC
            LIMIT ?
            """,
            (str(setup.symbol), str(setup.timeframe), int(limit)),
        ).fetchall()
    except Exception:
        return {"samples": 0, "wins": 0, "losses": 0, "win_rate": None, "expectancy_r": None, "usable": False}

    outcomes: list[float] = []
    wins = 0
    losses = 0
    for outcome, entry, stop, target, payload_json in rows:
        payload = {}
        if payload_json:
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
        other_meta = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        if str(other_meta.get("strategy", "ICT_CONFLUENCE") or "ICT_CONFLUENCE") != strategy:
            continue
        other_regime = str((other_meta.get("gold_regime_80", {}) or {}).get("regime") or "")
        if regime and other_regime and other_regime != regime:
            continue

        risk = abs(_number(entry) - _number(stop))
        if risk <= 0:
            continue
        rr = abs(_number(target) - _number(entry)) / risk
        if str(outcome) == "WOULD_WIN":
            outcomes.append(rr)
            wins += 1
        elif str(outcome) == "WOULD_LOSE":
            outcomes.append(-1.0)
            losses += 1

    samples = len(outcomes)
    expectancy = sum(outcomes) / samples if samples else None
    win_rate = wins / samples if samples else None
    return {
        "samples": samples,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "expectancy_r": round(expectancy, 4) if expectancy is not None else None,
        "usable": samples >= 20,
        "strategy": strategy,
        "regime": regime or None,
    }


def rr_decision81(setup, evidence: dict[str, Any] | None = None) -> RRDecision81:
    metadata = _metadata(setup)
    strategy = str(metadata.get("strategy", "ICT_CONFLUENCE") or "ICT_CONFLUENCE").upper()
    grade = quality_grade81(setup)
    rr = _number(getattr(setup, "risk_reward", 0.0))
    evidence = dict(evidence or {})

    # Keep the specialized rejection-block contract untouched until its own
    # counterfactual sample justifies changing a historically very strict lane.
    if strategy == "REJECTION_BLOCK_10_10":
        floor = 3.0
    elif grade == "A+":
        floor = 1.20
    elif grade == "A":
        floor = 1.30
    else:
        floor = 1.50

    regime = metadata.get("gold_regime_80", {}) or {}
    regime_name = str(regime.get("regime") or "")
    regime_direction = str(regime.get("direction") or "neutral")
    if regime_name in {"CHOP", "WARMUP"}:
        floor = max(floor, 1.50)
    if regime_direction not in {"", "neutral", str(setup.direction)}:
        floor = max(floor, 1.50)

    # Evidence can tighten a weak lane immediately, but it only relaxes A-tier
    # from 1.30R to 1.20R after a meaningful same-strategy/regime sample.
    if evidence.get("usable"):
        exp_r = _number(evidence.get("expectancy_r"), -99.0)
        win_rate = _number(evidence.get("win_rate"), 0.0)
        if exp_r <= 0:
            floor = max(floor, 1.50)
        elif grade == "A" and exp_r >= 0.20 and win_rate >= 0.50:
            floor = min(floor, 1.20)

    # The very bottom of the dynamic band is reserved for a real catalyst.
    trigger = str(getattr(setup, "trigger_type", "") or "").lower()
    if floor <= 1.20 and strategy == "MSS_REVERSAL" and trigger not in {"smt", "liquidity_sweep"}:
        floor = 1.30

    allowed_grade = grade in {"A+", "A"}
    allowed = allowed_grade and rr + 1e-9 >= floor
    if not allowed_grade:
        reason = f"Operation 8.1 is A/A+ only; {grade or 'ungraded'} remains research-only."
    elif allowed:
        reason = f"Operation 8.1 dynamic R:R passed: {grade} {rr:.2f}R >= {floor:.2f}R floor."
    else:
        reason = f"Operation 8.1 dynamic R:R blocked: {grade} {rr:.2f}R < {floor:.2f}R floor."
    return RRDecision81(allowed=allowed, grade=grade, floor=round(floor, 2), reason=reason, evidence=evidence)


def _retracement_price(setup, fraction: float) -> float:
    displacement = setup.displacement
    move = _number(displacement.high) - _number(displacement.low)
    if str(setup.direction) == "bullish":
        return _number(displacement.high) - fraction * move
    return _number(displacement.low) + fraction * move


def _candidate_details(setup, entry_type: str) -> dict:
    candidates = _metadata(setup).get("entry_candidates", []) or []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("entry_type") or "") == entry_type:
            details = candidate.get("details", {}) or {}
            return details if isinstance(details, dict) else {}
    return {}


def _raw_zone81(setup) -> tuple[float, float, str]:
    preferred = _number(setup.entry_price)
    entry_type = str(_metadata(setup).get("entry_type") or "")
    details = _candidate_details(setup, entry_type)

    if entry_type == "ORDER_BLOCK":
        low = details.get("zone_overlap_low")
        high = details.get("zone_overlap_high")
        if low is not None and high is not None:
            return min(_number(low), _number(high)), max(_number(low), _number(high)), "ORDER_BLOCK_OVERLAP"

    if "62" in entry_type and bool(
        _metadata(setup).get("aggressive_entry")
        or (_metadata(setup).get("early_entry_arm_72h", {}) or {}).get("shallow_guard_passed")
    ):
        first = _retracement_price(setup, 0.62)
        second = _retracement_price(setup, 0.705)
        return min(first, second), max(first, second), "SHALLOW_62_TO_70_5"

    if "OTE" in entry_type or "EARLY_OTE" in entry_type:
        first = _retracement_price(setup, 0.705)
        second = _retracement_price(setup, 0.79)
        return min(first, second), max(first, second), "OTE_70_5_TO_79"

    fvg = getattr(setup, "entry_fvg", None)
    if fvg is not None:
        fvg_low = min(_number(fvg.lower), _number(fvg.upper))
        fvg_high = max(_number(fvg.lower), _number(fvg.upper))
        zone_50 = _retracement_price(setup, 0.50)
        zone_79 = _retracement_price(setup, 0.79)
        retrace_low, retrace_high = min(zone_50, zone_79), max(zone_50, zone_79)
        overlap_low = max(fvg_low, retrace_low)
        overlap_high = min(fvg_high, retrace_high)
        if overlap_low <= overlap_high:
            return overlap_low, overlap_high, "FVG_X_50_TO_79"
        return fvg_low, fvg_high, "FVG"

    return preferred, preferred, "EXACT_FALLBACK"


def prepare_execution_zone81(setup, required_rr: float) -> ZoneDecision81:
    """Convert existing OTE/FVG/OB geometry into a conservative first-touch execution zone.

    The activation price is the least favorable edge of the valid zone that still
    satisfies the approved R:R floor. That means research never gives itself a
    fantasy midpoint fill merely because price touched the edge of the zone.
    """
    preferred = _number(setup.entry_price)
    stop = _number(setup.stop_price)
    target = _number(setup.target_price)
    low, high, source = _raw_zone81(setup)
    if low > high:
        low, high = high, low

    r = max(0.0, float(required_rr))
    boundary = (target + r * stop) / (1.0 + r) if r > 0 else preferred
    if str(setup.direction) == "bullish":
        activation = min(high, boundary)
        if activation < low:
            return ZoneDecision81(False, low, high, preferred, preferred, _number(setup.risk_reward), source,
                                  "No bullish point inside the entry zone preserves the required R:R floor.")
    else:
        activation = max(low, boundary)
        if activation > high:
            return ZoneDecision81(False, low, high, preferred, preferred, _number(setup.risk_reward), source,
                                  "No bearish point inside the entry zone preserves the required R:R floor.")

    entry, normalized_stop, normalized_target = normalize_trade_prices(
        str(setup.symbol), str(setup.direction), activation, stop, target
    )
    geometry = validate_trade_geometry(
        str(setup.symbol), str(setup.direction), entry, normalized_stop, normalized_target
    )
    rr = _number(getattr(geometry, "risk_reward", 0.0)) if geometry.valid else 0.0
    if not geometry.valid or rr + 1e-9 < required_rr:
        return ZoneDecision81(False, low, high, preferred, preferred, rr, source,
                              f"Zone activation geometry failed at {rr:.2f}R; require {required_rr:.2f}R.")

    setup.entry_price = float(entry)
    setup.stop_price = float(normalized_stop)
    setup.target_price = float(normalized_target)
    setup.risk_reward = float(rr)
    metadata = _metadata(setup)
    metadata["execution_zone_81"] = {
        "profile": "GOLD_FIRST_TOUCH_ZONE_8_1",
        "source": source,
        "entry_type": metadata.get("entry_type"),
        "zone_low": round(low, 6),
        "zone_high": round(high, 6),
        "preferred_entry": round(preferred, 6),
        "activation_entry": round(float(entry), 6),
        "activation_rr": round(rr, 4),
        "required_rr": round(float(required_rr), 4),
        "hard_no_chase_progress": HARD_NO_CHASE_PROGRESS,
        "fill_rule": "First touch of the valid zone at the least-favorable R:R-safe edge",
    }
    return ZoneDecision81(True, low, high, preferred, float(entry), rr, source,
                          f"{source} first-touch zone armed at {float(entry):.2f}, {rr:.2f}R.")


def registration_time81(setup, runtime) -> datetime:
    value = None
    try:
        value = runtime.clock.event_time(setup.symbol)
    except Exception:
        value = None
    if value is None:
        value = getattr(setup, "created_at", None) or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def stamp_registration81(setup, runtime) -> datetime:
    registered = registration_time81(setup, runtime)
    metadata = _metadata(setup)
    metadata["pending_registered_at_81"] = registered.isoformat()
    metadata["entry_lifecycle_81"] = {
        "profile": "REGISTRATION_CLOCK_8_1",
        "registered_at": registered.isoformat(),
        "signal_created_at": getattr(setup.created_at, "isoformat", lambda: str(setup.created_at))(),
        "pending_bars": int(PENDING_BARS_81.get(str(setup.timeframe), 4)),
        "clock_rule": "Pending lifetime begins when the approved plan enters the order book, not when the thesis was first detected.",
    }
    return registered


def pending_expiry81(setup) -> datetime:
    metadata = _metadata(setup)
    raw = metadata.get("pending_registered_at_81")
    registered = None
    if raw:
        try:
            registered = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            registered = None
    if registered is None:
        registered = setup.created_at
    if registered.tzinfo is None:
        registered = registered.replace(tzinfo=timezone.utc)
    registered = registered.astimezone(timezone.utc)
    bars = int(PENDING_BARS_81.get(str(setup.timeframe), 4))
    seconds = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}.get(str(setup.timeframe), 60)
    return registered + timedelta(seconds=seconds * bars)


def eval_risk81(decision, setup) -> tuple[float, float]:
    """Explicit 8.1 risk contract: A+ $750, A/5-of-6 $500, preview/lower grade $0."""
    metadata = _metadata(setup)
    grade = quality_grade81(setup)
    score = int(metadata.get("checklist_score", 0) or 0)
    total = int(metadata.get("checklist_total", 0) or 0)
    preview = bool(metadata.get("preview_only_80")) or grade == "PREVIEW"

    if preview or grade not in {"A+", "A"}:
        target = 0.0
    elif metadata.get("candidate_source_80") == "EARLY_ARM_72H" and total >= 6 and score == 5:
        target = REDUCED_RISK_DOLLARS
    elif grade == "A+":
        target = FULL_RISK_DOLLARS
    else:
        target = REDUCED_RISK_DOLLARS

    strategy = str(metadata.get("strategy", "") or "").upper()
    explicit_caps = []
    if str(setup.timeframe) == "1m" and strategy == "MSS_REVERSAL":
        explicit_caps.append(REDUCED_RISK_DOLLARS)
    zone = metadata.get("execution_zone_81", {}) or {}
    if str(zone.get("source") or "").startswith("SHALLOW_62"):
        explicit_caps.append(REDUCED_RISK_DOLLARS)
    if explicit_caps:
        target = min([target] + explicit_caps)

    available = max(0.0, _number(getattr(decision, "risk_dollars", 0.0)))
    applied = min(target, available)
    multiplier = applied / available if available > 0 else 0.0
    projected = applied * max(0.0, _number(getattr(setup, "risk_reward", 0.0)))
    metadata["risk_policy_81"] = {
        "profile": "GOLD_EVAL_500_750_8_1",
        "grade": grade,
        "target_risk_dollars": round(target, 2),
        "available_risk_dollars": round(available, 2),
        "applied_risk_dollars": round(applied, 2),
        "explicit_safety_caps": [round(item, 2) for item in explicit_caps],
        "projected_profit_dollars": round(projected, 2),
        "session_objective_dollars": 1500.0,
        "daily_drawdown_stop_dollars": 1000.0,
        "max_trades": None,
        "profit_cap": None,
    }
    return round(applied, 2), round(multiplier, 6)
