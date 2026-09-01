from __future__ import annotations

from src.strategies.execution_quality import _structure_bias, evaluate_ict_context


VERIFY_MODES = {"VERIFY", "VERIFICATION", "TEST"}


def _history_at_or_before(histories, symbol: str, timeframe: str, market_time):
    return [
        candle
        for candle in histories.get((symbol, timeframe), [])
        if candle.close_time <= market_time
    ]


def assess_gold_1m_verify(setup, histories, trading_mode: str):
    """Final quality firewall for Gold 1m during continuous verification.

    Operations 6.1/6.4/7.0 intentionally added salvage and recovery lanes. Those
    lanes can reduce risk, but they must not turn a primary 5m context rejection
    into permission to execute GC 1m. Re-run the original ICT quality contract
    after every inherited wrapper has finished. Continuation re-arms get the same
    directional 5m sanity check before execution.

    This does not make 1m scanner-only and does not block the dedicated 7.2 MSS
    reversal lane, whose separate multi-timeframe reversal guard remains intact.
    """
    mode = str(trading_mode or "").strip().upper()
    if mode not in VERIFY_MODES:
        return True, "Gold 1m VERIFY firewall is inactive outside verification mode.", {}
    if str(getattr(setup, "symbol", "") or "").upper() != "GC" or str(getattr(setup, "timeframe", "")) != "1m":
        return True, "Gold 1m VERIFY firewall is not applicable.", {}

    metadata = getattr(setup, "metadata", {}) or {}
    strategy = str(metadata.get("strategy", "ICT_CONFLUENCE") or "ICT_CONFLUENCE").upper()

    if strategy == "MSS_REVERSAL":
        return True, "Dedicated 7.2 MSS reversal guard remains authoritative.", {
            "profile": "GOLD_1M_VERIFY_72Q",
            "strategy": strategy,
            "allowed": True,
            "reason": "DEDICATED_REVERSAL_GUARD",
        }

    if strategy == "ICT_CONFLUENCE":
        core_allowed, core_reason, details = evaluate_ict_context(setup, histories)
        metadata["a_plus_context"] = details
        metadata["gold_verify_guard_72q"] = {
            "profile": "GOLD_1M_VERIFY_72Q",
            "strategy": strategy,
            "allowed": bool(core_allowed),
            "core_reason": core_reason,
            "prior_execution_tier": metadata.get("execution_tier"),
        }
        setup.metadata = metadata
        if not core_allowed:
            return False, (
                "Gold 1m quality firewall: this setup only survived through a salvage/recovery path; "
                f"the original 5m quality contract still rejects it. {core_reason}"
            ), metadata["gold_verify_guard_72q"]

        grade = str(details.get("quality_grade") or "").upper()
        if grade not in {"A+", "A", "B+"}:
            return False, f"Gold 1m quality firewall: {grade or 'ungraded'} quality is research-only.", metadata["gold_verify_guard_72q"]

        return True, (
            f"Gold 1m quality firewall passed: {grade} ICT setup is aligned with "
            f"{details.get('context_timeframe', '5m')} {details.get('higher_timeframe_bias', 'context')}."
        ), metadata["gold_verify_guard_72q"]

    if strategy == "TREND_CONTINUATION_REARM":
        context_tf = "5m"
        candles = _history_at_or_before(histories, "GC", context_tf, setup.created_at)
        bias, bias_details = _structure_bias(candles)
        details = {
            "profile": "GOLD_1M_VERIFY_72Q",
            "strategy": strategy,
            "context_timeframe": context_tf,
            "higher_timeframe_bias": bias,
            "higher_timeframe_details": bias_details,
            "direction": setup.direction,
        }
        metadata["gold_verify_guard_72q"] = details
        setup.metadata = metadata
        if bias in {"unknown", "neutral"}:
            return False, f"Gold 1m continuation firewall: 5m is {bias}; wait for directional structure before re-entry.", details
        if bias != setup.direction:
            return False, (
                f"Gold 1m continuation firewall: 5m is {bias} while the re-arm is {setup.direction}; "
                "the stale thesis cannot override current structure."
            ), details
        return True, f"Gold 1m continuation firewall passed: 5m remains {bias}." , details

    return True, "Gold 1m VERIFY firewall leaves this strategy to its inherited quality gate.", {
        "profile": "GOLD_1M_VERIFY_72Q",
        "strategy": strategy,
        "allowed": True,
        "reason": "INHERITED_GATE",
    }
