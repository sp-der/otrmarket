from __future__ import annotations

import asyncio

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
                    f"{decision.reason} Replay RR tier {risk_multiplier:.0%} "
                    f"of ${decision.risk_dollars:.2f} cap."
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
