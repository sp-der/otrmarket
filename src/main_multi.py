from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src import main as runtime
from src.strategies.manager import MultiStrategyEngine


# Replace the single setup detector with a multi-strategy coordinator while
# preserving the existing collectors, replay handling, evaluation guard,
# storage, paper executor, and dashboard runtime.
runtime.strategy = MultiStrategyEngine()


def _setup_risk(decision, setup) -> tuple[float, float]:
    """Apply a setup's replay multiplier without ever exceeding guard risk."""
    try:
        multiplier = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        multiplier = 1.0
    multiplier = max(0.0, min(1.0, multiplier))
    return round(float(decision.risk_dollars) * multiplier, 2), multiplier


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
    """Prevent rapid-fire re-entry on the same market after a completed trade.

    A loss receives a longer reset window than a win. The clock uses replay
    market time, not wall-clock time, so 10x replay does not make the bot behave
    more aggressively than normal market speed.
    """
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


def _a_plus_quality_gate(connection, setup) -> tuple[bool, str]:
    """Execution-only A+ filter layered on top of the existing detectors.

    The strategy engines may still identify looser research candidates so the
    scanner can show what they saw, but only candidates meeting these stricter
    execution standards become paper trades.
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
        # 1-minute entries need more room because they are the easiest place for
        # replay noise to generate several technically-valid setups in a burst.
        min_rr = 2.0 if setup.timeframe == "1m" else 1.5
        entry_type = str(setup.metadata.get("entry_type", "FVG_MIDPOINT"))

        if rr < min_rr:
            return False, (
                f"ICT {setup.timeframe} setup offers {rr:.2f}R; tightened A+ minimum is "
                f"{min_rr:.2f}R for this timeframe."
            )

        if entry_type == "ORDER_BLOCK":
            order_block_min = 2.5 if setup.timeframe == "1m" else 2.0
            if rr < order_block_min:
                return False, (
                    f"Order-block fallback offers {rr:.2f}R; require {order_block_min:.2f}R "
                    "before using the fallback entry."
                )

        # For NQ/ES on the 1-minute chart, require SMT confirmation rather than
        # accepting a sweep-only trigger. Higher timeframes and GC can still use
        # the existing liquidity-sweep path.
        if (
            setup.timeframe == "1m"
            and setup.symbol in {"NQ", "ES"}
            and str(setup.trigger_type or "").lower() != "smt"
        ):
            return False, "1m NQ/ES execution now requires SMT confirmation in addition to structure."

    cooldown_ok, cooldown_reason = _same_symbol_cooldown(connection, setup)
    if not cooldown_ok:
        return False, cooldown_reason

    return True, "Tightened A+ execution quality gate passed."


def evaluate_strategy(connection, symbol: str, timeframe: str):
    if not runtime.session.strategy_enabled(symbol):
        return None

    setups = runtime.strategy.on_candle_all(
        symbol, timeframe, runtime.histories_snapshot()
    )
    runtime.save_diagnostic(
        connection, runtime.strategy.diagnostic(symbol, timeframe)
    )

    handled = []
    for setup in setups:
        quality_allowed, quality_reason = _a_plus_quality_gate(connection, setup)
        setup.metadata["execution_quality_gate"] = {
            "allowed": quality_allowed,
            "reason": quality_reason,
            "profile": "A_PLUS_TIGHT_4_9",
        }
        if not quality_allowed:
            setup.status = "QUALITY_BLOCKED"
            runtime.save_setup(connection, setup)
            runtime.console.log(
                f"A+ QUALITY blocked {setup.symbol} {setup.timeframe} "
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
                    f"{decision.reason} A+ gate passed. Replay RR tier "
                    f"{risk_multiplier:.0%} of ${decision.risk_dollars:.2f} cap."
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
        runtime.console.log(
            f"SETUP REGISTERED {setup.symbol} {setup.timeframe} "
            f"[{setup.metadata.get('strategy', 'UNKNOWN')}] "
            f"{setup.direction.upper()} {setup.risk_reward:.2f}R "
            f"risk ${applied_risk:.2f} ({risk_multiplier:.0%} tier)"
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
