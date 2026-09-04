from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from src import main_72s as op72s
from src.execution import paper as paper_module
from src.execution.live.config import ExecutionConfig
from src.research.live_training72t import backfill_4h_candles_72t, install_training_capture_72t
from src.strategies import candles as candle_module
from src.strategies import execution_quality
from src.strategies import market_intelligence


runtime = op72s.runtime
_original_evaluate_strategy_72t = runtime.evaluate_strategy
_original_load_recent_candles_72t = runtime.load_recent_candles


def _parse_time_72t(value):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _table_exists_72t(connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _install_4h_context_72t() -> None:
    """Add 4H Gold macro context without creating a 4H execution lane."""
    candle_module.TIMEFRAME_SECONDS["4h"] = 14_400
    if "4h" not in runtime.candles.timeframes:
        runtime.candles.timeframes = tuple(runtime.candles.timeframes) + ("4h",)

    # Market-map research can see the 4H structure. Existing execution grades
    # are intentionally not hard-blocked by it yet; 4H begins as context/evidence.
    if "4h" not in market_intelligence.CONTEXT_TIMEFRAMES:
        market_intelligence.CONTEXT_TIMEFRAMES = tuple(market_intelligence.CONTEXT_TIMEFRAMES) + ("4h",)
    execution_quality.BAR_SECONDS["4h"] = 14_400

    def load_with_4h(connection, symbols, timeframes, limit_per_series=500):
        requested = tuple(dict.fromkeys(tuple(timeframes) + ("4h",)))
        return _original_load_recent_candles_72t(
            connection,
            symbols=symbols,
            timeframes=requested,
            limit_per_series=limit_per_series,
        )

    def evaluate_without_4h_execution(connection, symbol: str, timeframe: str):
        if str(timeframe).lower() == "4h":
            return None
        return _original_evaluate_strategy_72t(connection, symbol, timeframe)

    runtime.load_recent_candles = load_with_4h
    runtime.evaluate_strategy = evaluate_without_4h_execution


def _install_single_symbol_execution_guard_72t() -> None:
    """Make one active/pending idea per symbol an executor invariant.

    Upstream quality gates already reject duplicate GC exposure. This second
    barrier lives at the final paper-order registration point so candle-close,
    intrabar, continuation, recovery, or future strategy paths cannot create a
    second executable Gold idea even if an upstream wrapper regresses.
    """
    marker = "_otr_single_symbol_guard_72t"
    if getattr(runtime.paper, marker, False):
        return

    original_register = runtime.paper.register_setup

    def guarded_register(setup, *, risk_dollars=None, guard_reason=None):
        new_id = str(getattr(setup, "setup_id", "") or "")
        new_symbol = str(getattr(setup, "symbol", "") or "").upper()
        existing_same_id = runtime.paper.positions.get(new_id)
        if existing_same_id is not None and existing_same_id.status in {"PENDING", "OPEN"}:
            return existing_same_id

        for position in runtime.paper.positions.values():
            if str(getattr(position, "status", "") or "").upper() not in {"PENDING", "OPEN"}:
                continue
            other = position.setup
            other_id = str(getattr(other, "setup_id", "") or "")
            other_symbol = str(getattr(other, "symbol", "") or "").upper()
            if other_id == new_id:
                return position
            if other_symbol and other_symbol == new_symbol:
                raise ValueError(
                    "ACTIVE_SYMBOL_CONFLICT_72T: "
                    f"{new_symbol} already has {position.status.lower()} idea "
                    f"{other_id} ({getattr(other, 'timeframe', '?')} "
                    f"{getattr(other, 'direction', '?')}); second order blocked."
                )

        return original_register(
            setup,
            risk_dollars=risk_dollars,
            guard_reason=guard_reason,
        )

    runtime.paper.register_setup = guarded_register
    setattr(runtime.paper, marker, True)


def _latest_event_72t(connection, symbol: str = "GC"):
    if not _table_exists_72t(connection, "market_quotes"):
        return None, None
    columns = {row[1] for row in connection.execute("PRAGMA table_info(market_quotes)").fetchall()}
    price_expr = "COALESCE(price, mid)" if "mid" in columns else "price"
    order_column = "id" if "id" in columns else "received_at"
    row = connection.execute(
        f"SELECT received_at, {price_expr} FROM market_quotes WHERE symbol=? ORDER BY {order_column} DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if not row:
        return None, None
    parsed = _parse_time_72t(row[0])
    try:
        price = float(row[1]) if row[1] is not None else None
    except (TypeError, ValueError):
        price = None
    return parsed, price


def _invalidate_persisted_pending_72t(connection, setup_id: str, event_time: datetime, price, reason: str) -> None:
    timestamp = event_time.isoformat()
    connection.execute(
        """
        UPDATE paper_trades
        SET status='INVALIDATED', closed_at=?, exit_price=?, result=?, result_r=NULL,
            result_dollars=CASE WHEN risk_dollars IS NULL THEN NULL ELSE 0.0 END,
            updated_at=?
        WHERE setup_id=? AND status='PENDING'
        """,
        (timestamp, price, reason, timestamp, setup_id),
    )
    if _table_exists_72t(connection, "strategy_setups"):
        connection.execute(
            "UPDATE strategy_setups SET status='INVALIDATED' WHERE setup_id=?",
            (setup_id,),
        )


def _reconcile_active_connection_72t(connection, event_time=None, current_price=None) -> dict[str, int]:
    """Expire stale persisted limits and collapse duplicate pending exposure."""
    summary = {"expired": 0, "conflicts": 0, "multiple_open": 0, "surviving": 0}
    if not _table_exists_72t(connection, "paper_trades") or not _table_exists_72t(connection, "strategy_setups"):
        return summary

    # Training is reinstalled/backfilled immediately after reconciliation. Drop
    # only its paper-trade triggers here so repairing a stale ledger row cannot
    # be blocked by an old trigger definition from a prior container.
    connection.executescript(
        """
        DROP TRIGGER IF EXISTS training_trade_insert_72t;
        DROP TRIGGER IF EXISTS training_trade_update_72t;
        """
    )

    if event_time is None:
        event_time, current_price = _latest_event_72t(connection, "GC")
    event_time = _parse_time_72t(event_time)
    if event_time is None:
        connection.commit()
        return summary

    rows = connection.execute(
        """
        SELECT p.setup_id,p.symbol,p.timeframe,p.direction,p.status,s.created_at,p.updated_at
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id=p.setup_id
        WHERE p.status IN ('PENDING','OPEN')
        ORDER BY COALESCE(s.created_at,p.updated_at) ASC
        """
    ).fetchall()

    # First remove pending orders that could not still be alive at the current
    # replay event time. This is the common stale-row case after a restart.
    for row in rows:
        setup_id, symbol, timeframe, _direction, status, created_at, _updated_at = row
        if str(status).upper() != "PENDING":
            continue
        created = _parse_time_72t(created_at)
        if created is None:
            continue
        bars = int(paper_module._PENDING_BARS.get(str(timeframe), 4))
        seconds = int(paper_module._BAR_SECONDS.get(str(timeframe), 60)) * bars
        age = (event_time - created).total_seconds()
        if age >= 0 and age > seconds:
            _invalidate_persisted_pending_72t(
                connection,
                str(setup_id),
                event_time,
                current_price,
                "EXPIRED_ON_RESTART_72T",
            )
            summary["expired"] += 1

    survivors = connection.execute(
        """
        SELECT p.setup_id,p.symbol,p.timeframe,p.direction,p.status,s.created_at,p.updated_at
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id=p.setup_id
        WHERE p.status IN ('PENDING','OPEN')
        ORDER BY COALESCE(s.created_at,p.updated_at) ASC
        """
    ).fetchall()

    by_symbol: dict[str, list] = {}
    for row in survivors:
        by_symbol.setdefault(str(row[1]).upper(), []).append(row)

    for symbol, items in by_symbol.items():
        if len(items) <= 1:
            continue
        opens = [row for row in items if str(row[4]).upper() == "OPEN"]
        pendings = [row for row in items if str(row[4]).upper() == "PENDING"]
        if len(opens) > 1:
            # Never silently flatten a genuinely open exposure during recovery.
            # Block all new registrations and surface the anomaly in Railway.
            summary["multiple_open"] += len(opens)
            continue

        if opens:
            keep_id = str(opens[0][0])
        else:
            # If two valid pending ideas somehow survived, preserve the first
            # risk slot and invalidate later conflicting orders.
            keep_id = str(items[0][0])

        for row in pendings:
            setup_id = str(row[0])
            if setup_id == keep_id:
                continue
            _invalidate_persisted_pending_72t(
                connection,
                setup_id,
                event_time,
                current_price,
                "ACTIVE_SYMBOL_CONFLICT_RECONCILED_72T",
            )
            summary["conflicts"] += 1

    connection.commit()
    row = connection.execute(
        "SELECT COUNT(*) FROM paper_trades WHERE status IN ('PENDING','OPEN')"
    ).fetchone()
    summary["surviving"] = int(row[0] if row else 0)
    return summary


def _reconcile_persisted_active_72t() -> dict[str, int]:
    connection = runtime.get_connection()
    try:
        summary = _reconcile_active_connection_72t(connection)
        runtime.console.log(
            "Operation 7.2T active-order reconciliation: "
            f"expired={summary['expired']} conflicts={summary['conflicts']} "
            f"multiple_open={summary['multiple_open']} surviving={summary['surviving']}."
        )
        return summary
    finally:
        connection.close()


def _install_idempotent_training_trade_triggers_on_connection_72t(connection) -> None:
    """Make training trade capture update-safe across repeated paper writes."""
    if not _table_exists_72t(connection, "training_trades_72t") or not _table_exists_72t(connection, "verify_active_run_72s"):
        return
    connection.executescript(
        """
        DROP TRIGGER IF EXISTS training_trade_insert_72t;
        DROP TRIGGER IF EXISTS training_trade_update_72t;

        CREATE TRIGGER training_trade_insert_72t
        AFTER INSERT ON paper_trades
        BEGIN
          INSERT OR IGNORE INTO training_trades_72t(
            run_id,setup_id,build,symbol,timeframe,direction,status,opened_at,
            closed_at,result,result_r,risk_dollars,result_dollars,updated_at
          )
          SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.direction,
            NEW.status,NEW.opened_at,NEW.closed_at,NEW.result,NEW.result_r,
            NEW.risk_dollars,NEW.result_dollars,NEW.updated_at
          FROM verify_active_run_72s WHERE slot=1;
          UPDATE training_trades_72t SET
            build='7.2T',symbol=NEW.symbol,timeframe=NEW.timeframe,direction=NEW.direction,
            status=NEW.status,opened_at=NEW.opened_at,closed_at=NEW.closed_at,
            result=NEW.result,result_r=NEW.result_risk_dollars,
            risk_dollars=NEW.risk_dollars,result_dollars=NEW.result_dollars,
            updated_at=NEW.updated_at
          WHERE setup_id=NEW.setup_id
            AND run_id=(SELECT run_id FROM verify_active_run_72s WHERE slot=1);
        END;

        CREATE TRIGGER training_trade_update_72t
        AFTER UPDATE ON paper_trades
        BEGIN
          INSERT OR IGNORE INTO training_trades_72t(
            run_id,setup_id,build,symbol,timeframe,direction,status,opened_at,
            closed_at,result,result_r,risk_dollars,result_dollars,updated_at
          )
          SELECT run_id,NEW.setup_id,'7.2T',NEW.symbol,NEW.timeframe,NEW.direction,
            NEW.status,NEW.opened_at,NEW.closed_at,NEW.result,NEW.result_r,
            NEW.risk_dollars,NEW.result_dollars,NEW.updated_at
          FROM verify_active_run_72s WHERE slot=1;
          UPDATE training_trades_72t SET
            build='7.2T',symbol=NEW.symbol,timeframe=NEW.timeframe,direction=NEW.direction,
            status=NEW.status,opened_at=NEW.opened_at,closed_at=NEW.closed_at,
            result=NEW.result,result_r=NEW.result_r,
            risk_dollars=NEW.risk_dollars,result_dollars=NEW.result_dollars,
            updated_at=NEW.updated_at
          WHERE setup_id=NEW.setup_id
            AND run_id=(SELECT run_id FROM verify_active_run_72s WHERE slot=1);
        END;
        """
    )
    connection.commit()


def _install_idempotent_training_trade_triggers_72t() -> None:
    connection = runtime.get_connection()
    try:
        _install_idempotent_training_trade_triggers_on_connection_72t(connection)
    finally:
        connection.close()


def main() -> None:
    op72s.op72r.op72q.op72.op71._patch_runtime_manifest_71()
    op72s.op72r.op72q.op72._patch_runtime_manifest_72()

    # Keep 7.2S's database-authoritative VERIFY ledger first. Training capture
    # uses the same stable run marker but writes to durable research tables that
    # are deliberately not part of a replay scoreboard wipe.
    op72s._install_verify_trade_tag_trigger_72s()
    derived = backfill_4h_candles_72t()
    _install_4h_context_72t()
    _install_single_symbol_execution_guard_72t()
    reconciliation = _reconcile_persisted_active_72t()
    capture = install_training_capture_72t()
    _install_idempotent_training_trade_triggers_72t()

    config = ExecutionConfig.from_env()
    runtime.console.log(
        "Operation 7.2T active: 7.2S ledger + 7.2R Gold momentum + 7.2Q 1m firewall retained; "
        "single-symbol execution invariant active; stale pending rows reconciled before recovery; "
        "4H candles are macro direction/research context only and cannot execute trades; "
        "Strategy Lab training corpus captures decisions, actual outcomes, counterfactuals, missed moves and shadow evidence; "
        f"4h_backfill_rows={derived}; active_survivors={reconciliation.get('surviving', 0)}; "
        f"training_run={capture.get('run_id') or os.getenv('OTR_VERIFY_RUN_ID', 'none')}; "
        f"broker gateway mode={config.mode.value}."
    )
    op72s.op72r.op72q.op72.op71.op70.op69.op68.op66.op65.op64.op63.op62._restore_progress_62()
    try:
        asyncio.run(runtime.main())
    except KeyboardInterrupt:
        runtime.console.print("\n[yellow]OTR Market stopped.[/yellow]")


if __name__ == "__main__":
    main()
