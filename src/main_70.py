from __future__ import annotations

import asyncio
import json
from datetime import timezone
from pathlib import Path

from src import main_69 as op69
from src import main_59 as op59
from src import main_61 as op61


runtime = op69.runtime
ACTIVE_FUTURES = {"NQ", "ES", "GC"}


def _cap_risk(setup, cap: float, tier: str, reason: str) -> None:
    try:
        current = float(setup.metadata.get("risk_multiplier", 1.0))
    except (TypeError, ValueError):
        current = 1.0
    setup.metadata["risk_multiplier"] = min(current, cap)
    setup.metadata["execution_tier"] = tier
    setup.metadata["tier_reason"] = reason


def _created_utc(setup):
    created_at = setup.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at.astimezone(timezone.utc)


def _candidate_day(setup) -> str | None:
    return op59.op58.base._trading_day(setup.created_at)


def _symbol_day_losses(connection, setup) -> int:
    candidate_day = _candidate_day(setup)
    if not candidate_day:
        return 0
    rows = connection.execute(
        """
        SELECT closed_at
        FROM paper_trades
        WHERE symbol = ? AND status = 'CLOSED' AND result = 'LOSS' AND closed_at IS NOT NULL
        ORDER BY closed_at ASC
        """,
        (setup.symbol,),
    ).fetchall()
    return sum(op59.op58.base._trading_day(row[0]) == candidate_day for row in rows)


def _futures_consecutive_losses(connection, setup) -> int:
    candidate_day = _candidate_day(setup)
    if not candidate_day:
        return 0
    rows = connection.execute(
        """
        SELECT result, closed_at
        FROM paper_trades
        WHERE symbol IN ('NQ', 'ES', 'GC')
          AND status = 'CLOSED' AND result IN ('WIN', 'LOSS') AND closed_at IS NOT NULL
        ORDER BY closed_at DESC
        """
    ).fetchall()
    streak = 0
    for result, closed_at in rows:
        if op59.op58.base._trading_day(closed_at) != candidate_day:
            continue
        if str(result or "").upper() != "LOSS":
            break
        streak += 1
    return streak


def _no_cross_market_loss_cooldown(connection, setup) -> tuple[bool, str]:  # noqa: ARG001
    return True, "Operation 7.0: losses no longer freeze unrelated futures markets."


def _same_symbol_cooldown_70(connection, setup) -> tuple[bool, str]:
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

    closed_at = op59.op58.base._parse_time(row[0])
    created_at = _created_utc(setup)
    if closed_at is None or created_at <= closed_at:
        return True, "No active same-market cooldown applies."

    result = str(row[1] or "").upper()
    cooldown_minutes = 30 if result == "LOSS" else 20
    elapsed = (created_at - closed_at).total_seconds() / 60.0
    if elapsed < cooldown_minutes:
        return False, (
            f"{setup.symbol} reset window active: {cooldown_minutes - elapsed:.0f} market minutes remain "
            f"after the prior {result.lower() or 'trade'}. Other futures markets remain eligible."
        )
    return True, f"{setup.symbol} same-market reset window cleared."


def _b_plus_execution_gate_70(connection, setup) -> tuple[bool, str]:
    candidate_day = _candidate_day(setup)
    rows = connection.execute(
        """
        SELECT p.symbol, p.result, p.closed_at, s.created_at, s.payload_json
        FROM paper_trades p
        LEFT JOIN strategy_setups s ON s.setup_id = p.setup_id
        WHERE p.symbol IN ('NQ', 'ES', 'GC')
        """
    ).fetchall()

    b_plus_count = 0
    for symbol, result, closed_at, created_at, payload_json in rows:
        if (
            symbol == setup.symbol
            and str(result or "").upper() == "LOSS"
            and op59.op58.base._trading_day(closed_at) == candidate_day
        ):
            return False, f"B+ {setup.symbol} tier disabled after a realized {setup.symbol} loss today."

        if op59.op58.base._trading_day(created_at) != candidate_day or not payload_json:
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        grade = payload.get("metadata", {}).get("a_plus_context", {}).get("quality_grade")
        if grade == "B+":
            b_plus_count += 1

    if b_plus_count >= 2:
        return False, "Daily B+ futures limit reached (2/2 reduced-risk trades)."
    return True, f"B+ reduced-risk slot available ({b_plus_count}/2 used); unrelated-market losses do not disable it."


def _post_loss_risk_70(connection, setup) -> tuple[bool, str]:
    symbol_losses = _symbol_day_losses(connection, setup)
    futures_loss_streak = _futures_consecutive_losses(connection, setup)
    grade = str(setup.metadata.get("a_plus_context", {}).get("quality_grade") or "A").upper()

    setup.metadata["recovery_control_70"] = {
        "symbol_losses_today": symbol_losses,
        "futures_consecutive_losses": futures_loss_streak,
        "quality_grade": grade,
        "cross_market_penalty": False,
    }

    # Two back-to-back futures losses are an account-level warning, regardless
    # of which symbols produced them. Keep only A/A+ ideas and cut exposure hard.
    if futures_loss_streak >= 2:
        if grade == "B+":
            return False, "Operation 7.0 recovery mode: B+ is disabled after two consecutive futures losses."
        cap = 0.35 if grade == "A+" else 0.30
        reason = (
            f"Operation 7.0 account recovery mode after {futures_loss_streak} consecutive futures losses; "
            f"qualified {grade} setup remains eligible at {cap:.0%} max risk."
        )
        _cap_risk(setup, cap, "ACCOUNT_RECOVERY_70", reason)
        setup.metadata["recovery_control_70"].update(mode="ACCOUNT_RECOVERY", risk_cap=cap)
        return True, reason

    # A loss now disciplines only the market that made the mistake. A/A+ ideas
    # on that symbol may resume after the cooldown at reduced risk. B+ stays off.
    if symbol_losses >= 1:
        if grade == "B+":
            return False, f"Operation 7.0: B+ {setup.symbol} is disabled after a realized {setup.symbol} loss today."
        cap = 0.60 if grade == "A+" else 0.50
        reason = (
            f"Operation 7.0 {setup.symbol} recovery: prior {setup.symbol} loss does not shut down the book; "
            f"qualified {grade} setup may trade at {cap:.0%} max risk after the symbol cooldown."
        )
        _cap_risk(setup, cap, "SYMBOL_RECOVERY_70", reason)
        setup.metadata["recovery_control_70"].update(mode="SYMBOL_RECOVERY", risk_cap=cap)
        return True, reason

    # A loss in NQ should not automatically cripple ES/GC and vice versa.
    reason = "Operation 7.0 normal risk: no realized loss on this symbol today."
    setup.metadata["recovery_control_70"].update(mode="NORMAL", risk_cap=None)
    return True, reason


# Patch the shared guards used by the inherited 6.1/6.9 quality chain.
op59.op58.base._global_loss_cooldown = _no_cross_market_loss_cooldown
op59.op58.base._same_symbol_cooldown = _same_symbol_cooldown_70
op59.op58.base._b_plus_execution_gate = _b_plus_execution_gate_70
op61._post_loss_risk = _post_loss_risk_70


def _patch_runtime_manifest_70() -> None:
    path = Path(__file__).resolve().parent / "dashboard" / "static" / "runtime-build.json"
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.setdefault("build", {})["operation"] = "Operation 7.0"
        rules = manifest.setdefault("rules", [])
        by_name = {str(item.get("name")): item for item in rules if isinstance(item, dict)}

        if "Autonomous timeframes" in by_name:
            by_name["Autonomous timeframes"]["value"] = (
                "1m / 5m / 15m / 1h autonomous when inherited quality and evaluation guards pass; "
                "1m remains precision-entry capable"
            )
            by_name["Autonomous timeframes"]["source"] = "src/main_70.py + src/main_69.py"

        if "Post-loss behavior" in by_name:
            by_name["Post-loss behavior"]["value"] = (
                "Losses cool only the losing symbol for 30m; A/A+ may resume at 50-60% risk; "
                "two consecutive futures losses trigger 30-35% account recovery mode"
            )
            by_name["Post-loss behavior"]["source"] = "src/main_70.py"

        if "B+ tier" in by_name:
            by_name["B+ tier"]["value"] = (
                "Reduced risk only · max 2 B+ futures trades/day · disabled only on the symbol that realized a loss; "
                "disabled portfolio-wide after two consecutive futures losses"
            )
            by_name["B+ tier"]["source"] = "src/main_70.py"

        if "Decision telemetry" not in by_name:
            rules.append(
                {
                    "name": "Decision telemetry",
                    "value": "Dashboard counts accepted candidates and rejection reasons per NQ / ES / GC trading day",
                    "source": "src/dashboard/queries_59.py + decision-telemetry.js",
                }
            )

        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception as exc:
        runtime.console.log(f"Operation 7.0 manifest audit warning: {exc}")


if __name__ == "__main__":
    _patch_runtime_manifest_70()
    runtime.console.log(
        "Operation 7.0 active: 1m precision execution stays enabled; losses are symbol-aware, "
        "A/A+ recovery trades use reduced risk, unrelated markets stay available, and two "
        "consecutive futures losses trigger account recovery mode."
    )
    op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
