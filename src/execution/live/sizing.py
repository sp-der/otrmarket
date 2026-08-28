from __future__ import annotations

from datetime import timedelta
import hashlib
import math

from src.risk.geometry import normalize_trade_prices
from src.research.execution.contracts import execution_contract, micro_spec

from .config import ExecutionConfig
from .models import ExecutionIntent, utc_now


def _quality(setup) -> tuple[str, float | None]:
    context = getattr(setup, "metadata", {}).get("a_plus_context", {}) or {}
    grade = str(context.get("quality_grade") or "A").upper()
    score = context.get("quality_score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return grade, score


def _strategy(setup) -> str:
    return str(getattr(setup, "metadata", {}).get("strategy") or "ICT_CONFLUENCE")


def build_execution_intent(
    setup,
    *,
    risk_dollars: float,
    config: ExecutionConfig,
    signal_contract: str = "",
    now=None,
) -> ExecutionIntent:
    now = now or utc_now()
    entry, stop, target = normalize_trade_prices(
        setup.symbol,
        setup.direction,
        float(setup.entry_price),
        float(setup.stop_price),
        float(setup.target_price),
    )
    spec = micro_spec(setup.symbol)
    per_contract_risk = abs(entry - stop) * float(spec.point_value)
    if per_contract_risk <= 0:
        raise ValueError("Execution intent has zero per-contract risk.")

    requested = max(0.0, float(risk_dollars or 0.0))
    allowed = min(requested, float(config.max_risk_dollars))
    quantity = min(config.max_micros, math.floor(allowed / per_contract_risk))
    if quantity < 1:
        raise ValueError(
            f"Allowed execution risk ${allowed:.2f} cannot fund one {spec.instrument} "
            f"contract at ${per_contract_risk:.2f} risk."
        )

    actual_risk = quantity * per_contract_risk
    grade, score = _quality(setup)
    setup_id = str(setup.setup_id)
    command_id = hashlib.sha256(f"OTR72|{setup_id}".encode("utf-8")).hexdigest()[:24]
    signal_contract = (signal_contract or "").strip()
    return ExecutionIntent(
        command_id=command_id,
        setup_id=setup_id,
        mode=config.mode.value,
        account=config.account,
        root_symbol=str(setup.symbol).upper(),
        signal_contract=signal_contract,
        execution_contract=execution_contract(setup.symbol, signal_contract or None),
        direction=str(setup.direction).lower(),
        side="BUY" if str(setup.direction).lower() == "bullish" else "SELL",
        quantity=int(quantity),
        order_type="LIMIT_BRACKET",
        entry_price=float(entry),
        stop_price=float(stop),
        target_price=float(target),
        risk_dollars=float(actual_risk),
        per_contract_risk=float(per_contract_risk),
        requested_risk=float(requested),
        setup_grade=grade,
        quality_score=score,
        timeframe=str(setup.timeframe),
        strategy=_strategy(setup),
        created_at=now,
        expires_at=now + timedelta(seconds=config.command_ttl_seconds),
        metadata={
            "source": "OTR_OPERATION_7_2",
            "risk_cap_dollars": float(allowed),
            "unused_risk_dollars": float(max(0.0, allowed - actual_risk)),
            "original_entry": float(setup.entry_price),
            "original_stop": float(setup.stop_price),
            "original_target": float(setup.target_price),
        },
    )
