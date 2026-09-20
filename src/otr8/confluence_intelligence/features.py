"""Pre-trade feature extraction for Operation 8.2 Confluence Intelligence.

This module is a pure PROJECTION of evidence OTR 8.0/8.1 already computed
for a setup (via SetupArbiter80 -> evaluate_market_narrative, and the
strategy/quality/risk metadata the pipeline already stamps onto
setup.metadata before save_setup()/register_setup() run). It never re-derives
candle-level structure, never touches live execution state, and never
fabricates a value: anything not already present becomes None/"UNKNOWN"
rather than a guess.

Only fields knowable at or before setup registration belong here -- nothing
about the eventual outcome (result, MFE/MAE, Nautilus, Vibe) is read.
"""

from __future__ import annotations

from typing import Any

FEATURE_VERSION = "confluence_features_v1"

# Timeframes covered by src.strategies.market_intelligence.CONTEXT_TIMEFRAMES
# once Operation 7.2T's runtime patch extends it with "4h". A timeframe with
# no data at capture time is reported as UNKNOWN, never guessed.
TIMEFRAMES = ("1m", "5m", "15m", "30m", "1h", "4h")

# HTF alignment/conflict is judged from these context timeframes; the setup's
# own execution timeframe is the LOCAL TRIGGER, not part of the HTF vote.
HTF_VOTE_TIMEFRAMES = ("5m", "15m", "30m", "1h", "4h")

UNKNOWN = "UNKNOWN"


def _meta(setup) -> dict[str, Any]:
    value = getattr(setup, "metadata", None)
    return value if isinstance(value, dict) else {}


def _own_arbiter_assessment(setup) -> dict[str, Any] | None:
    """This setup's own SetupArbiter80 assessment.

    The winning (chosen) setup carries the full assessment list under
    setup_arbiter_80.assessments, including the Market Narrative used to pick
    it. A losing candidate only carries its own score/reason. Setups that
    never reached the arbiter (e.g. blocked earlier) carry neither.
    """
    arbiter = _meta(setup).get("setup_arbiter_80")
    if not isinstance(arbiter, dict):
        return None
    assessments = arbiter.get("assessments")
    if isinstance(assessments, list):
        for item in assessments:
            if isinstance(item, dict) and str(item.get("setup_id")) == str(setup.setup_id):
                return item
    if "score" in arbiter:
        return {"score": arbiter.get("score"), "details": {}}
    return None


def _market_narrative(setup) -> dict[str, Any]:
    assessment = _own_arbiter_assessment(setup)
    if not assessment:
        return {}
    narrative = (assessment.get("details") or {}).get("market_narrative")
    return narrative if isinstance(narrative, dict) else {}


def _market_map(setup) -> dict[str, Any]:
    return _market_narrative(setup).get("market_map") or {}


def _timeframe_snapshot(setup, timeframe: str) -> dict[str, Any]:
    timeframes = _market_map(setup).get("timeframes")
    if not isinstance(timeframes, dict):
        return {}
    snapshot = timeframes.get(timeframe)
    return snapshot if isinstance(snapshot, dict) else {}


def structure_features(setup) -> dict[str, Any]:
    """STRUCTURE per timeframe: direction, plus swing/BOS-MSS evidence where
    the underlying swing_structure source was used (see market_intelligence
    .structure_snapshot -- close_momentum fallback carries no swing highs/
    lows, so those stay None rather than being invented).
    """
    per_timeframe: dict[str, Any] = {}
    for timeframe in TIMEFRAMES:
        structure = _timeframe_snapshot(setup, timeframe).get("structure", {})
        per_timeframe[timeframe] = {
            "direction": structure.get("direction", UNKNOWN),
            "source": structure.get("source", UNKNOWN),
            "last_high": structure.get("last_high"),
            "prior_high": structure.get("prior_high"),
            "last_low": structure.get("last_low"),
            "prior_low": structure.get("prior_low"),
        }
    return per_timeframe


def liquidity_features(setup) -> dict[str, Any]:
    """LIQUIDITY: sweep/swept level from the setup's own trigger, equal
    highs/lows and session liquidity from the execution-timeframe market map.
    """
    execution_timeframe = str(getattr(setup, "timeframe", "") or "")
    execution_snapshot = _timeframe_snapshot(setup, execution_timeframe)
    equal = execution_snapshot.get("equal_liquidity", {}) if isinstance(execution_snapshot, dict) else {}
    session = _market_map(setup).get("session_liquidity", {})
    trigger_details = getattr(setup, "trigger_details", None)
    trigger_details = trigger_details if isinstance(trigger_details, dict) else {}
    return {
        "liquidity_sweep": str(getattr(setup, "trigger_type", "") or "") == "liquidity_sweep",
        "swept_level": trigger_details.get("swept_level"),
        "equal_highs": equal.get("equal_highs") or [],
        "equal_lows": equal.get("equal_lows") or [],
        "previous_session_high": session.get("previous_day_high"),
        "previous_session_low": session.get("previous_day_low"),
        "session": session.get("session", UNKNOWN),
    }


def _fvg_dict(fvg) -> dict[str, Any] | None:
    if fvg is None:
        return None
    return {
        "direction": getattr(fvg, "direction", None),
        "lower": getattr(fvg, "lower", None),
        "upper": getattr(fvg, "upper", None),
        "midpoint": getattr(fvg, "midpoint", None),
        "formed_at": getattr(fvg, "formed_at", None).isoformat() if getattr(fvg, "formed_at", None) else None,
    }


def pd_array_features(setup) -> dict[str, Any]:
    """PD ARRAYS: this setup's own entry FVG/PD array plus the broader active
    FVG/order-block/breaker context from its own execution timeframe.
    """
    execution_timeframe = str(getattr(setup, "timeframe", "") or "")
    execution_snapshot = _timeframe_snapshot(setup, execution_timeframe)
    fvgs = execution_snapshot.get("fvgs", {}) if isinstance(execution_snapshot, dict) else {}
    order_blocks = execution_snapshot.get("order_blocks", {}) if isinstance(execution_snapshot, dict) else {}
    dealing_range = execution_snapshot.get("dealing_range", {}) if isinstance(execution_snapshot, dict) else {}
    meta = _meta(setup)

    entry_fvg = _fvg_dict(getattr(setup, "entry_fvg", None))
    created_at = getattr(setup, "created_at", None)
    fvg_age_seconds = None
    if entry_fvg and entry_fvg.get("formed_at") and created_at is not None:
        try:
            from datetime import datetime

            formed = datetime.fromisoformat(str(entry_fvg["formed_at"]).replace("Z", "+00:00"))
            fvg_age_seconds = max(0.0, (created_at.astimezone(formed.tzinfo or created_at.tzinfo) - formed).total_seconds())
        except (ValueError, TypeError):
            fvg_age_seconds = None

    return {
        "entry_fvg": entry_fvg,
        "entry_fvg_age_seconds": fvg_age_seconds,
        "entry_fvg_size": (
            abs(entry_fvg["upper"] - entry_fvg["lower"])
            if entry_fvg and entry_fvg.get("upper") is not None and entry_fvg.get("lower") is not None
            else None
        ),
        "first_touch": meta.get("first_touch") if "first_touch" in meta else None,
        "active_fvgs": fvgs.get("active") or [],
        "inverse_fvgs": fvgs.get("inverse") or [],
        "active_order_blocks": order_blocks.get("active") or [],
        "breaker_candidates": order_blocks.get("breaker_candidates") or [],
        "ote_depth": meta.get("ote_depth"),
        "premium_discount_zone": dealing_range.get("zone", UNKNOWN) if isinstance(dealing_range, dict) else UNKNOWN,
        "premium_discount_position": dealing_range.get("position") if isinstance(dealing_range, dict) else None,
    }


def momentum_features(setup) -> dict[str, Any]:
    """MOMENTUM: the setup's own displacement plus its execution-timeframe
    rejection/wick evidence.
    """
    displacement = getattr(setup, "displacement", None)
    execution_timeframe = str(getattr(setup, "timeframe", "") or "")
    rejection = _timeframe_snapshot(setup, execution_timeframe).get("rejection", {})
    regime = _meta(setup).get("gold_regime_80", {})
    regime = regime if isinstance(regime, dict) else {}
    return {
        "displacement_body_ratio": getattr(displacement, "body_ratio", None),
        "displacement_range_ratio": getattr(displacement, "range_ratio", None),
        "rejection_signal": rejection.get("signal") if isinstance(rejection, dict) else None,
        "lower_wick_fraction": rejection.get("lower_wick_fraction") if isinstance(rejection, dict) else None,
        "upper_wick_fraction": rejection.get("upper_wick_fraction") if isinstance(rejection, dict) else None,
        "regime": str(regime.get("regime") or UNKNOWN).upper(),
        "regime_direction": regime.get("direction", UNKNOWN),
    }


def context_features(setup) -> dict[str, Any]:
    """CONTEXT: session, Market Narrative, candidate/quality metadata already
    on the setup, and the requested risk already decided by the risk guard.
    """
    meta = _meta(setup)
    narrative = _market_narrative(setup)
    a_plus = meta.get("a_plus_context", {})
    a_plus = a_plus if isinstance(a_plus, dict) else {}
    evaluation_guard = meta.get("evaluation_guard", {})
    evaluation_guard = evaluation_guard if isinstance(evaluation_guard, dict) else {}
    arbiter = meta.get("setup_arbiter_80", {})
    arbiter = arbiter if isinstance(arbiter, dict) else {}
    dynamic_rr = meta.get("dynamic_rr_81", {})
    dynamic_rr = dynamic_rr if isinstance(dynamic_rr, dict) else {}

    return {
        "session": _market_map(setup).get("session_liquidity", {}).get("session", UNKNOWN),
        "market_narrative_score": narrative.get("score"),
        "market_narrative_grade": narrative.get("grade"),
        "market_narrative_aligned_votes": narrative.get("aligned_votes"),
        "market_narrative_opposed_votes": narrative.get("opposed_votes"),
        "setup_family": meta.get("entry_type") or meta.get("strategy") or UNKNOWN,
        "entry_type": meta.get("entry_type", UNKNOWN),
        "strategy": meta.get("strategy", "ICT_CONFLUENCE"),
        "grade": str(a_plus.get("quality_grade") or narrative.get("grade") or UNKNOWN).upper(),
        "checklist_score": meta.get("checklist_score"),
        "checklist_total": meta.get("checklist_total"),
        "planned_rr": getattr(setup, "risk_reward", None),
        "final_rr": dynamic_rr.get("final_rr"),
        "final_rr_grade": dynamic_rr.get("final_grade"),
        "requested_risk_dollars": evaluation_guard.get("risk_dollars"),
        "candidate_source": meta.get("candidate_source_80", UNKNOWN),
        "arbiter_score": arbiter.get("score"),
        "arbiter_selected": arbiter.get("selected"),
    }


def extract_features(setup) -> dict[str, Any]:
    """Full pre-trade confluence feature bundle for one GC setup."""
    return {
        "feature_version": FEATURE_VERSION,
        "structure": structure_features(setup),
        "liquidity": liquidity_features(setup),
        "pd_arrays": pd_array_features(setup),
        "momentum": momentum_features(setup),
        "context": context_features(setup),
    }
