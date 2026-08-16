from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone

from src import main as runtime
from src.execution.paper import PaperExecutor
from src.risk.evaluation import EvaluationConfig
from src.risk.session_consistency import evaluate_session_consistency
from src.storage.intelligence import (
    upsert_shadow_trade,
    upsert_trade_intelligence,
)
from src.strategies.execution_quality import evaluate_ict_context
from src.strategies.manager import MultiStrategyEngine


# Replace the single setup detector with a multi-strategy coordinator while
# preserving the existing collectors, replay handling, evaluation guard,
# storage, paper executor, and dashboard runtime.
runtime.strategy = MultiStrategyEngine()

# Operation 5.3 runs the pre-5.x / Operation 4.8 candidate stream in parallel
# without sending it through the 5.x session/context gates. It never affects
# the live evaluation ledger. The older pending-order behavior is intentionally
# preserved in this shadow executor so the comparison can expose whether the
# later stale-entry protections helped or hurt.
SHADOW_PROFILE = "OPERATION_4_8_SHADOW"
shadow_paper = PaperExecutor(
    pending_expiry_enabled=False,
    stale_preentry_enabled=False,
)
shadow_base_risk = float(EvaluationConfig.from_env().risk_per_trade)


# Persist MFE/MAE and setup fingerprints whenever the normal paper ledger is
# written. src.main resolves this global at call time, so wrapping it here also
# captures updates produced inside the existing process_price loop.
_original_upsert_paper_trade = runtime.upsert_paper_trade


def _upsert_live_trade_with_intelligence(connection, position, updated_at):
    _original_upsert_paper_trade(connection, position, updated_at)
    upsert_trade_intelligence(connection, position, updated_at)


runtime.upsert_paper_trade = _upsert_live_trade_with_intelligence


# Advance shadow positions before the normal runtime processes the same tick.
# This ordering matters: a setup discovered on a candle close must wait until
# the next incoming tick in both live and shadow books rather than receiving a
# same-tick fill advantage.
_original_process_price = runtime.process_price


def _process_price_with_shadow(connection, symbol, price, bid, ask, timestamp=None):
    event_time = timestamp or runtime.utc_now()
    for position in shadow_paper.on_price(symbol, price, event_time):
        upsert_shadow_trade(
            connection,
            position,
            event_time.isoformat(),
            profile=SHADOW_PROFILE,
            source_setup_id=position.setup.metadata.get("shadow_source_setup_id"),
        )
    return _original_process_price(connection, symbol, price, bid, ask, event_time)


runtime.process_price = _process_price_with_shadow


def _setup_risk(decision, setup) -> tuple[float, float]:
    """Apply a setup's replay multiplier without ever exceeding guard risk."""
    try:
        multiplier = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        multiplier = 1.0
    multiplier = max(0.0, min(1.0, multiplier))
    return round(float(decision.risk_dollars) * multiplier, 2), multiplier


def _shadow_risk(setup) -> tuple[float, float]:
    """Apply Operation 4.8 RR sizing without touching the live guard ledger."""
    try:
        multiplier = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        multiplier = 1.0
    multiplier = max(0.0, min(1.0, multiplier))
    return round(shadow_base_risk * multiplier, 2), multiplier


def _register_48_shadow(connection, setup) -> None:
    """Register the raw Operation 4.8 candidate before 5.x filters mutate it."""
    shadow_id = f"s48_{setup.setup_id}"
    if shadow_id in shadow_paper.positions or any(
        item.setup.setup_id == shadow_id for item in shadow_paper.closed
    ):
        return

    shadow_setup = deepcopy(setup)
    shadow_setup.setup_id = shadow_id
    shadow_setup.status = "PENDING"
    shadow_setup.metadata = deepcopy(setup.metadata)
    shadow_setup.metadata["shadow_profile"] = SHADOW_PROFILE
    shadow_setup.metadata["shadow_source_setup_id"] = setup.setup_id
    shadow_setup.metadata["execution_quality_gate"] = {
        "allowed": True,
        "reason": "Operation 4.8 shadow bypasses Operation 5.x session/context filters.",
        "profile": SHADOW_PROFILE,
    }

    risk_dollars, multiplier = _shadow_risk(shadow_setup)
    try:
        position = shadow_paper.register_setup(
            shadow_setup,
            risk_dollars=risk_dollars,
            guard_reason=(
                f"Operation 4.8 strategy shadow only; theoretical risk tier "
                f"{multiplier:.0%} of ${shadow_base_risk:.2f}."
            ),
        )
    except ValueError:
        return

    upsert_shadow_trade(
        connection,
        position,
        setup.created_at.isoformat(),
        profile=SHADOW_PROFILE,
        source_setup_id=setup.setup_id,
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _same_symbol_cooldown(connection, setup) -> tuple[bool, str]:
    """Prevent rapid-fire re-entry on the same market after a completed trade."""
    row = connection.execute(
        """
        SELECT closed_at, result
        FROM paper_trades
        WHERE symbol = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
        ORDER BY closed_at DESC
        LIMIT 1
        """,
        (setup.symbol,),
    ).fetchone()
    if row is None:
        return True, "No prior closed trade on this market."

    closed_at = _parse_time(row[0])
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    if closed_at is None or created_at <= closed_at:
        return True, "No active cooldown applies."

    cooldown_minutes = 60 if str(row[1] or "").upper() == "LOSS" else 20
    elapsed = (created_at - closed_at).total_seconds() / 60.0
    if elapsed < cooldown_minutes:
        remaining = cooldown_minutes - elapsed
        return False, (
            f"Same-market reset window active: {remaining:.0f} replay minutes remain "
            f"after the prior {str(row[1] or 'trade').lower()}."
        )
    return True, "Same-market reset window cleared."


def _global_loss_cooldown(connection, setup) -> tuple[bool, str]:
    """After any futures loss, require a short market-wide reset before new risk."""
    row = connection.execute(
        """
        SELECT symbol, closed_at
        FROM paper_trades
        WHERE status = 'CLOSED' AND result = 'LOSS' AND closed_at IS NOT NULL
        ORDER BY closed_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return True, "No global loss reset applies."

    loss_symbol, closed_value = row
    closed_at = _parse_time(closed_value)
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    created_at = created_at.astimezone(timezone.utc)
    if closed_at is None or created_at <= closed_at:
        return True, "No global loss reset applies."

    elapsed = (created_at - closed_at).total_seconds() / 60.0
    if elapsed < 30:
        return False, (
            f"Global post-loss reset active after {loss_symbol}: "
            f"{30 - elapsed:.0f} replay minutes remain before new futures risk."
        )
    return True, "Global post-loss reset cleared."


def _active_risk_gate(setup) -> tuple[bool, str]:
    """Avoid duplicate and NQ/ES correlated exposure while an idea is active."""
    for position in runtime.paper.positions.values():
        if position.status not in {"PENDING", "OPEN"}:
            continue
        other = position.setup
        if other.setup_id == getattr(setup, "setup_id", None):
            continue
        if other.symbol == setup.symbol:
            return False, f"{setup.symbol} already has an active paper idea."
        if {other.symbol, setup.symbol} == {"NQ", "ES"}:
            return False, (
                f"Correlated exposure blocked: {other.symbol} already has "
                f"an active {position.status.lower()} idea."
            )
    return True, "No duplicate/correlated active risk."


def _a_plus_quality_gate(connection, setup, histories=None) -> tuple[bool, str]:
    """A/A+ context and exposure filter used by every enabled timeframe.

    Operation 5.2 lets 1m, 5m, 15m and 1h candidates compete for execution.
    Timeframe permission does not weaken setup quality: every candidate still
    has to pass its own context, sequence, RR floor, exposure and reset rules.
    """
    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    rr = float(setup.risk_reward or 0.0)

    if strategy == "REJECTION_BLOCK_10_10":
        score = int(setup.metadata.get("checklist_score", 0) or 0)
        total = int(setup.metadata.get("checklist_total", 10) or 10)
        if score < total or total < 10:
            return False, f"Rejection-block checklist is only {score}/{total}; require a full 10/10."
        if rr < 3.0:
            return False, f"Rejection-block setup offers only {rr:.2f}R; require at least 3.00R."
    else:
        if rr < 1.0:
            return False, f"ICT setup offers only {rr:.2f}R; structural minimum is 1.00R."

        # The 1m index chart is noisy enough that SMT remains mandatory. Other
        # timeframes may qualify through their normal ICT trigger plus HTF bias.
        if (
            setup.timeframe == "1m"
            and setup.symbol in {"NQ", "ES"}
            and str(setup.trigger_type or "").lower() != "smt"
        ):
            return False, "1m NQ/ES execution requires SMT confirmation in addition to structure."

        if histories is not None:
            context_ok, context_reason, context_details = evaluate_ict_context(
                setup, histories
            )
            setup.metadata["a_plus_context"] = context_details
            if not context_ok:
                return False, context_reason

    active_ok, active_reason = _active_risk_gate(setup)
    if not active_ok:
        return False, active_reason

    global_ok, global_reason = _global_loss_cooldown(connection, setup)
    if not global_ok:
        return False, global_reason

    cooldown_ok, cooldown_reason = _same_symbol_cooldown(connection, setup)
    if not cooldown_ok:
        return False, cooldown_reason

    return True, "A/A+ context, sequence, exposure, and reset gates passed."


def evaluate_strategy(connection, symbol: str, timeframe: str):
    if not runtime.session.strategy_enabled(symbol):
        return None

    histories = runtime.histories_snapshot()
    setups = runtime.strategy.on_candle_all(symbol, timeframe, histories)
    runtime.save_diagnostic(
        connection, runtime.strategy.diagnostic(symbol, timeframe)
    )

    handled = []
    for setup in setups:
        # Capture the same detector output Operation 4.8 would have received
        # before any 5.x context/session rules are allowed to reject it.
        _register_48_shadow(connection, setup)

        session_decision = evaluate_session_consistency(connection, setup)
        setup.metadata["session_consistency"] = session_decision.details
        if not session_decision.allowed:
            setup.metadata["execution_quality_gate"] = {
                "allowed": False,
                "reason": session_decision.reason,
                "profile": "TRADE_INTELLIGENCE_5_3",
                "baseline_shadow_profile": SHADOW_PROFILE,
            }
            setup.status = "SESSION_BLOCKED"
            runtime.save_setup(connection, setup)
            runtime.console.log(
                f"SESSION 5.3 blocked {setup.symbol} {setup.timeframe} "
                f"[{setup.metadata.get('strategy', 'UNKNOWN')}]: {session_decision.reason}"
            )
            handled.append(setup)
            continue

        quality_allowed, quality_reason = _a_plus_quality_gate(
            connection, setup, histories
        )
        setup.metadata["execution_quality_gate"] = {
            "allowed": quality_allowed,
            "reason": quality_reason,
            "profile": "TRADE_INTELLIGENCE_5_3",
            "baseline_shadow_profile": SHADOW_PROFILE,
        }
        if not quality_allowed:
            setup.status = "QUALITY_BLOCKED"
            runtime.save_setup(connection, setup)
            runtime.console.log(
                f"A/A+ QUALITY blocked {setup.symbol} {setup.timeframe} "
                f"[{setup.metadata.get('strategy', 'UNKNOWN')}]: {quality_reason}"
            )
            handled.append(setup)
            continue

        decision = runtime.evaluation_guard.decide(connection, setup.created_at)
        applied_risk, risk_multiplier = _setup_risk(decision, setup)
        setup.metadata["evaluation_guard"] = {
            "status": decision.status,
            "allowed": decision.allowed,
            "risk_cap_dollars": decision.risk_dollars,
            "risk_multiplier": risk_multiplier,
            "risk_dollars": applied_risk if decision.allowed else 0.0,
            "reason": decision.reason,
            "profile": decision.snapshot.get("profile"),
            "phase": decision.snapshot.get("phase"),
        }
        if not decision.allowed:
            setup.status = "GUARD_BLOCKED"
            runtime.save_setup(connection, setup)
            runtime.console.log(
                f"PROP GUARD blocked {setup.symbol} {setup.timeframe} "
                f"[{setup.metadata.get('strategy', 'UNKNOWN')}]: "
                f"{decision.status} - {decision.reason}"
            )
            handled.append(setup)
            continue

        runtime.save_setup(connection, setup)
        try:
            position = runtime.paper.register_setup(
                setup,
                risk_dollars=applied_risk,
                guard_reason=(
                    f"{decision.reason} Operation 5.3 live A/A+ gate passed. "
                    f"Replay RR tier {risk_multiplier:.0%} of ${decision.risk_dollars:.2f} cap."
                ),
            )
        except ValueError as exc:
            setup.status = "RISK_REJECTED"
            setup.metadata["geometry_rejection"] = str(exc)
            runtime.save_setup(connection, setup)
            runtime.console.log(
                f"RISK GEOMETRY rejected {setup.symbol} {setup.timeframe} "
                f"[{setup.metadata.get('strategy', 'UNKNOWN')}]: {exc}"
            )
            handled.append(setup)
            continue

        runtime.upsert_paper_trade(
            connection, position, setup.created_at.isoformat()
        )
        context = setup.metadata.get("a_plus_context", {})
        htf = context.get("context_timeframe")
        bias = context.get("higher_timeframe_bias")
        context_text = f" HTF {htf}:{bias}" if htf and bias else ""
        session = setup.metadata.get("session_consistency", {})
        session_text = (
            f" {session.get('local_day')} {session.get('local_time')} "
            f"{session.get('timezone')}"
            if session
            else ""
        )
        runtime.console.log(
            f"SETUP REGISTERED {setup.symbol} {setup.timeframe} "
            f"[{setup.metadata.get('strategy', 'UNKNOWN')}] "
            f"{setup.direction.upper()} {setup.risk_reward:.2f}R "
            f"risk ${applied_risk:.2f} ({risk_multiplier:.0%} tier)"
            f"{context_text}{session_text}"
        )
        handled.append(setup)

    return handled[-1] if handled else None


# process_price() resolves evaluate_strategy from src.main's global namespace at
# call time, so this assignment upgrades every existing replay/live code path.
runtime.evaluate_strategy = evaluate_strategy


if __name__ == "__main__":
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
