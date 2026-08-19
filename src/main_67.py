from __future__ import annotations

import asyncio

from src import main_66 as op66
from src.storage.database import get_connection
from src.storage.intelligence_67 import enrich_trade_intelligence_67
from src.strategies.ote_entry_policy_67 import OTEEntryPolicy67


runtime = op66.runtime
base = op66.base


# ---------------------------------------------------------------------------
# Operation 6.7A: replace only the ICT confluence entry engine with the user's
# Fib/OTE-aware policy. Rejection-block and MSS reversal strategies remain
# untouched. A Railway restart starts with clean in-memory scanner state, so no
# active setup context is discarded by this replacement.
# ---------------------------------------------------------------------------
_policy_67 = OTEEntryPolicy67()
runtime.strategy.ict = _policy_67
if hasattr(runtime.strategy, "reversal"):
    runtime.strategy.engines = (
        _policy_67,
        runtime.strategy.rejection_block,
        runtime.strategy.reversal,
    )
else:
    runtime.strategy.engines = (
        _policy_67,
        runtime.strategy.rejection_block,
    )


# ---------------------------------------------------------------------------
# Operation 6.7B: preserve instant-stop memory across restarts and enrich the
# existing trade-intelligence row after the normal ledger/intelligence writer.
# This does not create or delete paper trades.
# ---------------------------------------------------------------------------
try:
    _seed_connection = get_connection()
    try:
        _seeded_penalties = _policy_67.seed_instant_stops(_seed_connection)
    finally:
        _seed_connection.close()
except Exception:
    _seeded_penalties = 0

_previous_upsert_67 = runtime.upsert_paper_trade


def _upsert_trade_67(connection, position, updated_at):
    _previous_upsert_67(connection, position, updated_at)
    try:
        enrich_trade_intelligence_67(connection, position, updated_at)
    except Exception as exc:
        runtime.console.log(f"INTELLIGENCE 6.7 enrichment error: {exc}")
    _policy_67.record_instant_stop(position)


runtime.upsert_paper_trade = _upsert_trade_67


if __name__ == "__main__":
    runtime.console.log(
        "Operation 6.7 active: user Fib 0/.50/.618/.705/.79/.88/1 profile, "
        ".705-.79 standard OTE with .79 preferred, shallow entries require the "
        "full aggressive checklist at reduced risk, and <=60s losses activate "
        f"same-day shallow-entry penalties. Restored {_seeded_penalties} instant-stop penalty dates."
    )
    op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
