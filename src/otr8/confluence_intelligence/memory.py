"""Similar-setup memory: a plain normalized feature-distance nearest-neighbor
search over resolved, accounting-compatible confluence snapshots.

No external ML dependency (scikit-learn) is used -- the task explicitly
allows either, and a hand-rolled normalized distance keeps this shadow
research layer dependency-light and fully unit-testable. SHADOW RESEARCH
ONLY: this never mutates a trade and is never consulted by execution.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from src.execution.paper import PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1
from src.research.lab_v01 import MIN_EVIDENCE_SAMPLES

from . import store

# Reuses the project's existing "statistically meaningful sample" convention
# (src.research.lab_v01.MIN_EVIDENCE_SAMPLES) rather than inventing a
# different threshold for this layer.
MIN_COMPARABLE_SAMPLES = MIN_EVIDENCE_SAMPLES

# Categorical mismatch penalty and numeric-field normalization ranges are
# fixed, documented constants -- not tuned per request.
CATEGORICAL_MISMATCH_PENALTY = 1.0
NUMERIC_RANGES = {
    "htf_alignment_score": 100.0,
    "market_narrative_score": 100.0,
    "displacement_body_ratio": 3.0,
    "displacement_range_ratio": 3.0,
    "planned_rr": 5.0,
}
TOP_K = 20


def _vector(features: dict[str, Any]) -> dict[str, Any]:
    # htf_alignment_score lives alongside the score fields in the stored
    # record (set by the caller below), not inside features_json.
    context = features.get("context", {})
    momentum = features.get("momentum", {})
    pd_arrays = features.get("pd_arrays", {})
    return {
        "categorical": {
            "timeframe": None,  # filled by caller from the setup itself
            "regime": momentum.get("regime"),
            "setup_family": context.get("setup_family"),
            "entry_type": context.get("entry_type"),
            "session": context.get("session"),
            "premium_discount_zone": pd_arrays.get("premium_discount_zone"),
        },
        "numeric": {
            "market_narrative_score": context.get("market_narrative_score"),
            "displacement_body_ratio": momentum.get("displacement_body_ratio"),
            "displacement_range_ratio": momentum.get("displacement_range_ratio"),
            "planned_rr": context.get("planned_rr"),
        },
    }


def _distance(a: dict[str, Any], b_row: dict[str, Any]) -> float:
    b_features = b_row.get("features") or {}
    b_vector = _vector(b_features)
    b_vector["categorical"]["timeframe"] = b_row.get("timeframe")
    b_vector["numeric"]["htf_alignment_score"] = b_row.get("htf_alignment_score")

    total = 0.0
    for key, a_value in a["categorical"].items():
        b_value = b_vector["categorical"].get(key)
        if a_value is None or b_value is None:
            continue
        if str(a_value) != str(b_value):
            total += CATEGORICAL_MISMATCH_PENALTY
    for key, a_value in a["numeric"].items():
        b_value = b_vector["numeric"].get(key)
        if a_value is None or b_value is None:
            continue
        span = NUMERIC_RANGES.get(key, 1.0)
        total += min(1.0, abs(float(a_value) - float(b_value)) / span)
    return total


def find_similar_setups(connection: sqlite3.Connection, setup, features: dict[str, Any]) -> dict[str, Any]:
    """Nearest-neighbor research statistics over comparable resolved trades.

    Only trades whose paper accounting used MGC_WHOLE_CONTRACT_V1 are
    eligible -- legacy theoretical-budget rows are never silently pooled
    into dollar-outcome statistics. Direction must match exactly (a bullish
    setup is never compared against bearish outcomes).

    The pool is also cut off at `setup.created_at`: only trades that had
    already closed before this setup's own event time are comparable
    evidence. Without this, a resolved outcome's wall-clock capture order
    (which depends only on how fast each setup happened to be processed
    during a replay) could stand in for causal/simulated chronology and let
    a later trade's result leak into an earlier decision's shadow context.
    """
    direction = str(getattr(setup, "direction", "") or "")
    pool = store.compatible_snapshots_for_training(
        connection,
        accounting_version=PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1,
        resolved_before=getattr(setup, "created_at", None),
    )
    pool = [row for row in pool if row.get("direction") == direction and row.get("setup_id") != str(setup.setup_id)]

    if len(pool) < MIN_COMPARABLE_SAMPLES:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "comparable_samples": len(pool),
            "minimum_samples": MIN_COMPARABLE_SAMPLES,
        }

    target_vector = _vector(features)
    target_vector["categorical"]["timeframe"] = str(getattr(setup, "timeframe", ""))
    target_vector["numeric"]["htf_alignment_score"] = features.get("htf_alignment_score")

    ranked = sorted(pool, key=lambda row: _distance(target_vector, row))[:TOP_K]
    return summarize_neighbors(ranked)


def summarize_neighbors(neighbors: list[dict[str, Any]]) -> dict[str, Any]:
    samples = len(neighbors)
    if samples < MIN_COMPARABLE_SAMPLES:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "comparable_samples": samples,
            "minimum_samples": MIN_COMPARABLE_SAMPLES,
        }

    wins = sum(1 for row in neighbors if row.get("result") == "WIN")
    losses = sum(1 for row in neighbors if row.get("result") == "LOSS")
    r_values = [row["result_r"] for row in neighbors if row.get("result_r") is not None]
    mfe_values = [row["mfe_r"] for row in neighbors if row.get("mfe_r") is not None]
    mae_values = [row["mae_r"] for row in neighbors if row.get("mae_r") is not None]

    immediate_stop = sum(1 for row in neighbors if row.get("result") == "LOSS" and (row.get("mfe_r") or 0) < 0.1)

    result = {
        "status": "OK",
        "comparable_samples": samples,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / samples, 4) if samples else None,
        "avg_result_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
        "avg_mfe_r": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None,
        "avg_mae_r": round(sum(mae_values) / len(mae_values), 4) if mae_values else None,
        "immediate_stop_rate": round(immediate_stop / samples, 4) if samples else None,
    }
    if len(mfe_values) >= MIN_COMPARABLE_SAMPLES:
        result["+1R_before_stop_rate"] = round(sum(1 for v in mfe_values if v >= 1.0) / len(mfe_values), 4)
        result["+2R_before_stop_rate"] = round(sum(1 for v in mfe_values if v >= 2.0) / len(mfe_values), 4)
    else:
        result["+1R_before_stop_rate"] = None
        result["+2R_before_stop_rate"] = None
    return result
