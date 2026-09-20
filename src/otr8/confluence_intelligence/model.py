"""LightGBM meta-labeling research scaffold for Operation 8.2 -- SHADOW ONLY.

This module NEVER lets a model control a trade. Training only ever happens
on demand (research capture / an operator-triggered API call), never as a
side effect of setup registration, and its output is a research prediction
persisted for comparison, not an input to any execution decision.

Anti-leakage discipline is load-bearing here: only features knowable at or
before setup registration may be model inputs. Training uses a strict
chronological split (earliest captured_at rows train, latest validate) --
never a random shuffle, which would let future information leak into
training via random assignment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.execution.paper import PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1

from . import store

MODEL_VERSION = "confluence_model_v1"

# Do not publish predictions below this many clean, resolved, compatible
# trades (task-specified floor). 100+ is preferred before "promotion"
# (treating predictions as worth surfacing prominently) but MIN_TRAINING_SAMPLES
# is the hard gate below which training does not run at all.
MIN_TRAINING_SAMPLES = 50
PROMOTION_SAMPLES = 100

VALIDATION_FRACTION = 0.2

CLASSIFICATION_TARGETS = ("PLUS_1R_BEFORE_STOP", "PLUS_2R_BEFORE_STOP", "IMMEDIATE_STOP")
REGRESSION_TARGETS = ("MFE_R", "MAE_R")

# Explicit input allowlist: every one of these is knowable strictly BEFORE
# the trade resolves. Nothing outcome-related is ever read from this list.
FEATURE_KEYS = (
    "local_quality_score",
    "htf_alignment_score",
    "context_score",
    "liquidity_score",
    "entry_quality_score",
    "combined_shadow_score",
    "htf_bullish_count",
    "htf_bearish_count",
    "htf_mixed_count",
    "requested_risk_dollars",
)

# Defensive denylist: even if one of these ever ended up in a stored record,
# build_feature_row() refuses to emit it. This is the anti-leakage guardrail
# the task requires, made explicit and testable rather than implicit.
FORBIDDEN_KEYS = frozenset(
    {
        "result",
        "result_r",
        "result_dollars",
        "mfe_r",
        "mae_r",
        "hold_seconds",
        "outcome_updated_at",
        "otr_result",
        "otr_result_r",
        "otr_result_dollars",
        "otr_trade_status",
        "nautilus_matched_trade_path",
        "nautilus_matched_full",
        "closed_at",
        "exit_price",
        "similar_setup_status",
    }
)


def build_feature_row(record: dict[str, Any]) -> dict[str, float]:
    """Project one stored snapshot into a flat numeric feature row.

    Only FEATURE_KEYS are read; FORBIDDEN_KEYS are asserted absent as a
    belt-and-braces anti-leakage check even though they were never read.
    """
    leaked = FORBIDDEN_KEYS.intersection(FEATURE_KEYS)
    assert not leaked, f"Anti-leakage violation: forbidden keys in FEATURE_KEYS: {leaked}"

    row: dict[str, float] = {}
    for key in FEATURE_KEYS:
        value = record.get(key)
        try:
            row[key] = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            row[key] = 0.0
    features = record.get("features") or {}
    momentum = features.get("momentum", {})
    context = features.get("context", {})
    for key, value in (
        ("displacement_body_ratio", momentum.get("displacement_body_ratio")),
        ("displacement_range_ratio", momentum.get("displacement_range_ratio")),
        ("planned_rr", context.get("planned_rr")),
        ("checklist_score", context.get("checklist_score")),
        ("checklist_total", context.get("checklist_total")),
    ):
        try:
            row[key] = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            row[key] = 0.0
    return row


def _label_for(record: dict[str, Any], target: str) -> float | None:
    if target == "MFE_R":
        return record.get("mfe_r")
    if target == "MAE_R":
        return record.get("mae_r")
    mfe = record.get("mfe_r")
    if mfe is None:
        return None
    if target == "PLUS_1R_BEFORE_STOP":
        return 1.0 if mfe >= 1.0 else 0.0
    if target == "PLUS_2R_BEFORE_STOP":
        return 1.0 if mfe >= 2.0 else 0.0
    if target == "IMMEDIATE_STOP":
        return 1.0 if (record.get("result") == "LOSS" and mfe < 0.1) else 0.0
    raise ValueError(f"Unknown target: {target}")


def build_dataset(connection, *, target: str) -> dict[str, Any]:
    """Chronologically ordered (captured_at ASC), MGC_WHOLE_CONTRACT_V1-only,
    resolved confluence snapshots projected into (feature_row, label) pairs.
    """
    records = store.compatible_snapshots_for_training(
        connection, accounting_version=PAPER_ACCOUNTING_VERSION_MGC_WHOLE_CONTRACT_V1
    )
    rows: list[dict[str, float]] = []
    labels: list[float] = []
    for record in records:
        label = _label_for(record, target)
        if label is None:
            continue
        rows.append(build_feature_row(record))
        labels.append(label)
    return {"rows": rows, "labels": labels, "target": target, "sample_count": len(rows)}


def time_based_split(dataset: dict[str, Any], *, validation_fraction: float = VALIDATION_FRACTION) -> dict[str, Any]:
    """Chronological split: the LAST validation_fraction of rows (by
    captured_at, already ordered ascending by build_dataset) become
    validation. No shuffling -- shuffling would let a later trade's
    information leak into an earlier validation fold via random assignment.
    """
    rows = dataset["rows"]
    labels = dataset["labels"]
    n = len(rows)
    split_at = max(1, int(round(n * (1 - validation_fraction)))) if n else 0
    return {
        "train_rows": rows[:split_at],
        "train_labels": labels[:split_at],
        "validation_rows": rows[split_at:],
        "validation_labels": labels[split_at:],
    }


def train_model(connection, *, target: str = "PLUS_1R_BEFORE_STOP") -> dict[str, Any]:
    """Train (or refuse to train) a research meta-model for one target.

    Never called as a side effect of trade registration -- this is only ever
    invoked explicitly (a research/dashboard action), and its output is
    persisted as a research artifact, never consulted by execution.
    """
    dataset = build_dataset(connection, target=target)
    sample_count = dataset["sample_count"]
    if sample_count < MIN_TRAINING_SAMPLES:
        return {
            "model_status": "INSUFFICIENT_DATA",
            "model_version": MODEL_VERSION,
            "target": target,
            "sample_count": sample_count,
            "minimum_required": MIN_TRAINING_SAMPLES,
        }

    try:
        import lightgbm as lgb
    except ImportError:
        return {
            "model_status": "DEPENDENCY_UNAVAILABLE",
            "model_version": MODEL_VERSION,
            "target": target,
            "sample_count": sample_count,
            "minimum_required": MIN_TRAINING_SAMPLES,
        }

    split = time_based_split(dataset)
    feature_names = list(FEATURE_KEYS) + [
        "displacement_body_ratio",
        "displacement_range_ratio",
        "planned_rr",
        "checklist_score",
        "checklist_total",
    ]
    train_matrix = [[row.get(name, 0.0) for name in feature_names] for row in split["train_rows"]]
    validation_matrix = [[row.get(name, 0.0) for name in feature_names] for row in split["validation_rows"]]

    is_classification = target != "MFE_R" and target != "MAE_R"
    params = {
        "objective": "binary" if is_classification else "regression",
        "verbosity": -1,
        "num_leaves": 7,
        "min_data_in_leaf": 5,
    }
    train_set = lgb.Dataset(train_matrix, label=split["train_labels"], feature_name=feature_names)
    valid_sets = []
    if validation_matrix:
        valid_sets = [lgb.Dataset(validation_matrix, label=split["validation_labels"], reference=train_set)]
    booster = lgb.train(params, train_set, num_boost_round=25, valid_sets=valid_sets or None)

    return {
        "model_status": "TRAINED" if sample_count >= PROMOTION_SAMPLES else "TRAINED_BELOW_PROMOTION_THRESHOLD",
        "model_version": MODEL_VERSION,
        "target": target,
        "sample_count": sample_count,
        "train_samples": len(split["train_rows"]),
        "validation_samples": len(split["validation_rows"]),
        "feature_names": feature_names,
        "minimum_required": MIN_TRAINING_SAMPLES,
        "promotion_threshold": PROMOTION_SAMPLES,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "best_iteration": getattr(booster, "best_iteration", None),
    }
