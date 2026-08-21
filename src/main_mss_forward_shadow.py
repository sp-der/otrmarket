from __future__ import annotations

import asyncio

from src import main_70 as op70
from src.research.mss_forward_shadow import MSSForwardShadowHarness


runtime = op70.runtime
shadow = MSSForwardShadowHarness()


_original_upsert_paper_trade = runtime.upsert_paper_trade


def _upsert_with_mss_forward_shadow(connection, position, updated_at):
    _original_upsert_paper_trade(connection, position, updated_at)
    try:
        if shadow.register_live_position(connection, position, updated_at):
            setup = position.setup
            runtime.console.log(
                f"MSS FORWARD SHADOW armed {setup.symbol} {setup.timeframe} "
                f"{setup.direction.upper()} source={setup.setup_id}"
            )
    except Exception as exc:
        # The observer is additive. It must never interrupt the production path.
        runtime.console.log(f"MSS FORWARD SHADOW registration error: {exc}")


runtime.upsert_paper_trade = _upsert_with_mss_forward_shadow


_original_process_price = runtime.process_price


def _process_price_with_mss_forward_shadow(connection, symbol, price, bid, ask, timestamp=None):
    event_time = timestamp or runtime.utc_now()
    try:
        # Advance shadow books before the live runtime sees this tick. A setup
        # discovered by the live runtime therefore cannot receive a same-tick
        # hindsight fill in either shadow book.
        shadow.on_price(connection, symbol, price, bid, ask, event_time)
    except Exception as exc:
        runtime.console.log(f"MSS FORWARD SHADOW tick error {symbol}: {exc}")
    return _original_process_price(connection, symbol, price, bid, ask, event_time)


runtime.process_price = _process_price_with_mss_forward_shadow


def _prepare_forward_shadow() -> None:
    connection = runtime.get_connection()
    try:
        interrupted = shadow.prepare_session(connection)
        runtime.console.log(
            "MSS Forward Shadow V1 ready: baseline=current Operation 7.0 MSS entry; "
            "candidate=FVG shallow-25; no live behavior or risk gates changed."
        )
        if interrupted:
            runtime.console.log(
                f"MSS Forward Shadow marked {interrupted} in-flight shadow row(s) "
                "INTERRUPTED_RESTART rather than inventing continuity."
            )
    finally:
        connection.close()


if __name__ == "__main__":
    _prepare_forward_shadow()
    # Reuse Operation 7.0's existing restart recovery for the actual paper book.
    op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")
