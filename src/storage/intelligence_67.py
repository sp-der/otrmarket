from __future__ import annotations

import json


FIB_PROFILE_67 = {
    "levels": [0.0, 0.50, 0.618, 0.705, 0.79, 0.88, 1.0],
    "standard_ote_zone": [0.705, 0.79],
    "preferred_deep_ote": 0.79,
}


def _duration_seconds(position) -> float | None:
    opened_at = getattr(position, "opened_at", None)
    closed_at = getattr(position, "closed_at", None)
    if opened_at is None or closed_at is None:
        return None
    try:
        return max(0.0, (closed_at - opened_at).total_seconds())
    except Exception:
        return None


def operation67_failure_tags(position) -> list[str]:
    if str(getattr(position, "result", "") or "").upper() != "LOSS":
        return []

    seconds = _duration_seconds(position)
    if seconds is None or seconds > 60:
        return []

    setup = position.setup
    metadata = dict(getattr(setup, "metadata", {}) or {})
    retracement = metadata.get("retracement_fraction")
    try:
        retracement = float(retracement) if retracement is not None else None
    except (TypeError, ValueError):
        retracement = None

    tags = ["INSTANT_STOP", "ENTRY_TIMING_FAILURE"]
    if retracement is not None and retracement < 0.705:
        tags.extend(
            [
                "ENTRY_TOO_EARLY",
                "PREMATURE_RETRACEMENT_FILL",
                "NOISE_SWEEP_BEFORE_CONFIRMATION",
            ]
        )
    if (
        str(getattr(setup, "symbol", "")) == "GC"
        and str(getattr(setup, "timeframe", "")) == "1m"
        and str(getattr(setup, "direction", "")) == "bullish"
    ):
        tags.append("GC_1M_BULLISH_INSTANT_STOP")
    return tags


def enrich_trade_intelligence_67(connection, position, updated_at: str) -> None:
    """Attach Operation 6.7 entry-depth and failure tags to stored intelligence.

    The base 5.x intelligence writer owns the schema and outcome_class. This
    enrichment keeps compatibility by extending fingerprint_json only.
    """
    setup = getattr(position, "setup", None)
    if setup is None:
        return

    row = connection.execute(
        "SELECT fingerprint_json FROM trade_intelligence WHERE setup_id = ?",
        (setup.setup_id,),
    ).fetchone()
    if row is None:
        return

    try:
        fingerprint = json.loads(row[0] or "{}")
    except (TypeError, ValueError):
        fingerprint = {}

    metadata = dict(getattr(setup, "metadata", {}) or {})
    fingerprint.update(
        {
            "operation": 6.7,
            "direction": getattr(setup, "direction", None),
            "fib_profile": metadata.get("fib_profile", FIB_PROFILE_67),
            "retracement_fraction": metadata.get("retracement_fraction"),
            "entry_zone": metadata.get("entry_zone"),
            "aggressive_entry": bool(metadata.get("aggressive_entry")),
            "aggressive_confirmation": metadata.get("aggressive_confirmation"),
            "entry_risk_cap": metadata.get("entry_risk_cap"),
            "instant_stop_penalty_active": bool(metadata.get("instant_stop_penalty_active")),
            "failure_tags": operation67_failure_tags(position),
        }
    )

    connection.execute(
        """
        UPDATE trade_intelligence
        SET fingerprint_json = ?, updated_at = ?
        WHERE setup_id = ?
        """,
        (json.dumps(fingerprint, sort_keys=True), updated_at, setup.setup_id),
    )
    connection.commit()
