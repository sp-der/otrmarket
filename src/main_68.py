from __future__ import annotations

import asyncio

from src import main_67 as op67


runtime = op67.runtime
op66 = op67.op66
op65 = op66.op65
op58 = op65.op64.op58


# ---------------------------------------------------------------------------
# Operation 6.8A: 1-minute charts remain high-speed signal scouts, but they no
# longer originate autonomous paper risk. The Aug-19 forensic sample showed the
# visible five-loss cluster entirely on 1m candidates across ICT, MSS reversal,
# and continuation, while the visible winner was a GC 5m candidate. Rather than
# teach each strategy its own version of the same lesson, put one firewall in
# the shared quality path.
#
# The scanner still evaluates and displays 1m structure. A setup that matures on
# 1m is persisted as QUALITY_BLOCKED with an explicit scout-only reason, so the
# information is retained without risking another full stop. 5m / 15m / 1h
# remain eligible for autonomous execution through their existing quality,
# session, cooldown, and evaluation guards.
# ---------------------------------------------------------------------------
_previous_quality_gate_68 = op58._adaptive_quality_gate


def _adaptive_quality_gate_68(connection, setup, histories=None):
    allowed, reason = _previous_quality_gate_68(connection, setup, histories)
    if not allowed:
        return False, reason

    if str(getattr(setup, "timeframe", "")) != "1m":
        return True, reason

    strategy = str(setup.metadata.get("strategy", "ICT_CONFLUENCE"))
    setup.metadata["one_minute_firewall_68"] = {
        "operation": 6.8,
        "mode": "SCOUT_ONLY",
        "autonomous_execution": False,
        "strategy": strategy,
        "reason": (
            "1m is retained for signal discovery and entry timing, but new "
            "autonomous risk requires confirmation on 5m or higher."
        ),
    }
    return False, (
        f"Operation 6.8 1m quality firewall: {strategy} candidate retained as "
        "signal/scout intelligence only; autonomous execution requires 5m+ confirmation."
    )


op58._adaptive_quality_gate = _adaptive_quality_gate_68


# ---------------------------------------------------------------------------
# Operation 6.8B: intrabar acceleration is now reserved for 5m. Operation 6.6
# expanded the 6.5 forming-candle probe to every execution timeframe while the
# original 0.25s / 3-confirmation / 0.75s stability cadence stayed unchanged.
# That cadence is useful for a 5m entry trigger but too permissive for 15m/1h
# bars, and 1m is now scout-only. 15m and 1h still execute normally at candle
# close, preserving multi-timeframe autonomy without letting a few ticks stand
# in for a higher-timeframe candle.
# ---------------------------------------------------------------------------
op65.INTRABAR_TIMEFRAMES = {"5m"}


if __name__ == "__main__":
    runtime.console.log(
        "Operation 6.8 active: 1m is signal/scout-only, autonomous risk requires "
        "5m+ confirmation, 5m keeps stable-state intrabar acceleration, and "
        "15m/1h remain autonomous on completed candles."
    )
    op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
