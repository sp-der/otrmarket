from __future__ import annotations

import sqlite3


CANONICAL_TRIGGER_NAMES = (
    "training_decision_insert_72t",
    "training_decision_update_72t",
    "training_trade_insert_72t",
    "training_trade_update_72t",
    "training_intelligence_insert_72t",
    "training_intelligence_update_72t",
    "training_counterfactual_insert_72t",
    "training_counterfactual_update_72t",
    "training_shadow_insert_72t",
    "training_shadow_update_72t",
)

TRAINING_TARGET_TABLES = (
    "training_decisions_72t",
    "training_trades_72t",
    "training_trade_metrics_72t",
    "training_counterfactuals_72t",
    "training_shadow_72t",
)


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _can_install(connection: sqlite3.Connection, source: str, target: str) -> bool:
    return (
        _table_exists(connection, "verify_active_run_72s")
        and _table_exists(connection, source)
        and _table_exists(connection, target)
    )


def harden_training_trade_triggers_80(connection: sqlite3.Connection) -> dict[str, int]:
    """Replace legacy Strategy Lab capture triggers with explicit UPSERTs.

    SQLite applies the conflict policy of an outer write statement to trigger
    statements in surprising ways. OTR persistence uses INSERT ... ON CONFLICT
    DO UPDATE for paper trades, strategy setups and trade-intelligence rows, so
    legacy trigger bodies using INSERT OR IGNORE / INSERT OR REPLACE can still
    raise UNIQUE errors when replay revisits an existing setup.

    Operation 8.0 treats every Strategy Lab capture trigger as one persistence
    contract: remove historical variants and recreate each available source/
    target pair with its own explicit ON CONFLICT(run_id, setup_id) DO UPDATE.
    """
    summary = {"dropped": 0, "installed": 0}

    rows = connection.execute(
        """
        SELECT name, COALESCE(sql, '')
        FROM sqlite_master
        WHERE type='trigger'
        """
    ).fetchall()
    target_markers = tuple(name.lower() for name in TRAINING_TARGET_TABLES)
    canonical = {name.lower() for name in CANONICAL_TRIGGER_NAMES}
    for name, sql in rows:
        name_text = str(name or "")
        sql_text = str(sql or "").lower()
        name_lower = name_text.lower()
        if name_lower not in canonical and not any(marker in sql_text for marker in target_markers):
            continue
        connection.execute(f"DROP TRIGGER IF EXISTS {_quote_identifier(name_text)}")
        summary["dropped"] += 1

    if _can_install(connection, "strategy_setups", "training_decisions_72t"):
        connection.executescript(
            """
            CREATE TRIGGER training_decision_insert_72t
            AFTER INSERT ON strategy_setups
            BEGIN
              INSERT INTO training_decisions_72t(
                run_id,setup_id,build,symbol,timeframe,direction,created_at,
                trigger_type,entry_price,stop_price,target_price,risk_reward,status,
                payload_json,last_seen_at
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.created_at,NEW.trigger_type,NEW.entry_price,NEW.stop_price,
                NEW.target_price,NEW.risk_reward,NEW.status,NEW.payload_json,datetime('now')
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,symbol=excluded.symbol,timeframe=excluded.timeframe,
                direction=excluded.direction,created_at=excluded.created_at,
                trigger_type=excluded.trigger_type,entry_price=excluded.entry_price,
                stop_price=excluded.stop_price,target_price=excluded.target_price,
                risk_reward=excluded.risk_reward,status=excluded.status,
                payload_json=excluded.payload_json,last_seen_at=excluded.last_seen_at;
            END;

            CREATE TRIGGER training_decision_update_72t
            AFTER UPDATE ON strategy_setups
            BEGIN
              INSERT INTO training_decisions_72t(
                run_id,setup_id,build,symbol,timeframe,direction,created_at,
                trigger_type,entry_price,stop_price,target_price,risk_reward,status,
                payload_json,last_seen_at
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.created_at,NEW.trigger_type,NEW.entry_price,NEW.stop_price,
                NEW.target_price,NEW.risk_reward,NEW.status,NEW.payload_json,datetime('now')
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,symbol=excluded.symbol,timeframe=excluded.timeframe,
                direction=excluded.direction,created_at=excluded.created_at,
                trigger_type=excluded.trigger_type,entry_price=excluded.entry_price,
                stop_price=excluded.stop_price,target_price=excluded.target_price,
                risk_reward=excluded.risk_reward,status=excluded.status,
                payload_json=excluded.payload_json,last_seen_at=excluded.last_seen_at;
            END;
            """
        )
        summary["installed"] += 2

    if _can_install(connection, "paper_trades", "training_trades_72t"):
        connection.executescript(
            """
            CREATE TRIGGER training_trade_insert_72t
            AFTER INSERT ON paper_trades
            BEGIN
              INSERT INTO training_trades_72t(
                run_id,setup_id,build,symbol,timeframe,direction,status,opened_at,
                closed_at,result,result_r,risk_dollars,result_dollars,updated_at
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.status,NEW.opened_at,NEW.closed_at,NEW.result,NEW.result_r,
                NEW.risk_dollars,NEW.result_dollars,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,symbol=excluded.symbol,timeframe=excluded.timeframe,
                direction=excluded.direction,status=excluded.status,
                opened_at=excluded.opened_at,closed_at=excluded.closed_at,
                result=excluded.result,result_r=excluded.result_r,
                risk_dollars=excluded.risk_dollars,result_dollars=excluded.result_dollars,
                updated_at=excluded.updated_at;
            END;

            CREATE TRIGGER training_trade_update_72t
            AFTER UPDATE ON paper_trades
            BEGIN
              INSERT INTO training_trades_72t(
                run_id,setup_id,build,symbol,timeframe,direction,status,opened_at,
                closed_at,result,result_r,risk_dollars,result_dollars,updated_at
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.status,NEW.opened_at,NEW.closed_at,NEW.result,NEW.result_r,
                NEW.risk_dollars,NEW.result_dollars,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,symbol=excluded.symbol,timeframe=excluded.timeframe,
                direction=excluded.direction,status=excluded.status,
                opened_at=excluded.opened_at,closed_at=excluded.closed_at,
                result=excluded.result,result_r=excluded.result_r,
                risk_dollars=excluded.risk_dollars,result_dollars=excluded.result_dollars,
                updated_at=excluded.updated_at;
            END;
            """
        )
        summary["installed"] += 2

    if _can_install(connection, "trade_intelligence", "training_trade_metrics_72t"):
        connection.executescript(
            """
            CREATE TRIGGER training_intelligence_insert_72t
            AFTER INSERT ON trade_intelligence
            BEGIN
              INSERT INTO training_trade_metrics_72t(
                run_id,setup_id,build,symbol,timeframe,strategy,trigger_type,entry_type,
                result,result_r,risk_reward,displacement_body_ratio,
                displacement_range_ratio,fvg_age_bars,htf_timeframe,htf_bias,mfe_r,
                mae_r,duration_seconds,outcome_class,fingerprint_json,closed_at,updated_at
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.strategy,
                NEW.trigger_type,NEW.entry_type,NEW.result,NEW.result_r,NEW.risk_reward,
                NEW.displacement_body_ratio,NEW.displacement_range_ratio,
                NEW.fvg_age_bars,NEW.htf_timeframe,NEW.htf_bias,NEW.mfe_r,NEW.mae_r,
                NEW.duration_seconds,NEW.outcome_class,NEW.fingerprint_json,
                NEW.closed_at,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,symbol=excluded.symbol,timeframe=excluded.timeframe,
                strategy=excluded.strategy,trigger_type=excluded.trigger_type,
                entry_type=excluded.entry_type,result=excluded.result,
                result_r=excluded.result_r,risk_reward=excluded.risk_reward,
                displacement_body_ratio=excluded.displacement_body_ratio,
                displacement_range_ratio=excluded.displacement_range_ratio,
                fvg_age_bars=excluded.fvg_age_bars,htf_timeframe=excluded.htf_timeframe,
                htf_bias=excluded.htf_bias,mfe_r=excluded.mfe_r,mae_r=excluded.mae_r,
                duration_seconds=excluded.duration_seconds,
                outcome_class=excluded.outcome_class,
                fingerprint_json=excluded.fingerprint_json,closed_at=excluded.closed_at,
                updated_at=excluded.updated_at;
            END;

            CREATE TRIGGER training_intelligence_update_72t
            AFTER UPDATE ON trade_intelligence
            BEGIN
              INSERT INTO training_trade_metrics_72t(
                run_id,setup_id,build,symbol,timeframe,strategy,trigger_type,entry_type,
                result,result_r,risk_reward,displacement_body_ratio,
                displacement_range_ratio,fvg_age_bars,htf_timeframe,htf_bias,mfe_r,
                mae_r,duration_seconds,outcome_class,fingerprint_json,closed_at,updated_at
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.strategy,
                NEW.trigger_type,NEW.entry_type,NEW.result,NEW.result_r,NEW.risk_reward,
                NEW.displacement_body_ratio,NEW.displacement_range_ratio,
                NEW.fvg_age_bars,NEW.htf_timeframe,NEW.htf_bias,NEW.mfe_r,NEW.mae_r,
                NEW.duration_seconds,NEW.outcome_class,NEW.fingerprint_json,
                NEW.closed_at,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,symbol=excluded.symbol,timeframe=excluded.timeframe,
                strategy=excluded.strategy,trigger_type=excluded.trigger_type,
                entry_type=excluded.entry_type,result=excluded.result,
                result_r=excluded.result_r,risk_reward=excluded.risk_reward,
                displacement_body_ratio=excluded.displacement_body_ratio,
                displacement_range_ratio=excluded.displacement_range_ratio,
                fvg_age_bars=excluded.fvg_age_bars,htf_timeframe=excluded.htf_timeframe,
                htf_bias=excluded.htf_bias,mfe_r=excluded.mfe_r,mae_r=excluded.mae_r,
                duration_seconds=excluded.duration_seconds,
                outcome_class=excluded.outcome_class,
                fingerprint_json=excluded.fingerprint_json,closed_at=excluded.closed_at,
                updated_at=excluded.updated_at;
            END;
            """
        )
        summary["installed"] += 2

    if _can_install(connection, "counterfactual_setups", "training_counterfactuals_72t"):
        connection.executescript(
            """
            CREATE TRIGGER training_counterfactual_insert_72t
            AFTER INSERT ON counterfactual_setups
            BEGIN
              INSERT INTO training_counterfactuals_72t(
                run_id,setup_id,build,symbol,timeframe,direction,created_at,
                blocked_status,blocked_reason,outcome,resolved_at,max_favorable_r,
                max_adverse_r,last_checked
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.created_at,NEW.blocked_status,NEW.blocked_reason,NEW.outcome,
                NEW.resolved_at,NEW.max_favorable_r,NEW.max_adverse_r,NEW.last_checked
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,symbol=excluded.symbol,timeframe=excluded.timeframe,
                direction=excluded.direction,created_at=excluded.created_at,
                blocked_status=excluded.blocked_status,blocked_reason=excluded.blocked_reason,
                outcome=excluded.outcome,resolved_at=excluded.resolved_at,
                max_favorable_r=excluded.max_favorable_r,
                max_adverse_r=excluded.max_adverse_r,last_checked=excluded.last_checked;
            END;

            CREATE TRIGGER training_counterfactual_update_72t
            AFTER UPDATE ON counterfactual_setups
            BEGIN
              INSERT INTO training_counterfactuals_72t(
                run_id,setup_id,build,symbol,timeframe,direction,created_at,
                blocked_status,blocked_reason,outcome,resolved_at,max_favorable_r,
                max_adverse_r,last_checked
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.direction,
                NEW.created_at,NEW.blocked_status,NEW.blocked_reason,NEW.outcome,
                NEW.resolved_at,NEW.max_favorable_r,NEW.max_adverse_r,NEW.last_checked
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,symbol=excluded.symbol,timeframe=excluded.timeframe,
                direction=excluded.direction,created_at=excluded.created_at,
                blocked_status=excluded.blocked_status,blocked_reason=excluded.blocked_reason,
                outcome=excluded.outcome,resolved_at=excluded.resolved_at,
                max_favorable_r=excluded.max_favorable_r,
                max_adverse_r=excluded.max_adverse_r,last_checked=excluded.last_checked;
            END;
            """
        )
        summary["installed"] += 2

    if _can_install(connection, "shadow_trades", "training_shadow_72t"):
        connection.executescript(
            """
            CREATE TRIGGER training_shadow_insert_72t
            AFTER INSERT ON shadow_trades
            BEGIN
              INSERT INTO training_shadow_72t(
                run_id,setup_id,build,source_setup_id,profile,symbol,timeframe,
                direction,strategy,status,result,result_r,mfe_r,mae_r,closed_at,updated_at
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.source_setup_id,NEW.profile,
                NEW.symbol,NEW.timeframe,NEW.direction,NEW.strategy,NEW.status,
                NEW.result,NEW.result_r,NEW.mfe_r,NEW.mae_r,NEW.closed_at,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,source_setup_id=excluded.source_setup_id,
                profile=excluded.profile,symbol=excluded.symbol,timeframe=excluded.timeframe,
                direction=excluded.direction,strategy=excluded.strategy,status=excluded.status,
                result=excluded.result,result_r=excluded.result_r,mfe_r=excluded.mfe_r,
                mae_r=excluded.mae_r,closed_at=excluded.closed_at,updated_at=excluded.updated_at;
            END;

            CREATE TRIGGER training_shadow_update_72t
            AFTER UPDATE ON shadow_trades
            BEGIN
              INSERT INTO training_shadow_72t(
                run_id,setup_id,build,source_setup_id,profile,symbol,timeframe,
                direction,strategy,status,result,result_r,mfe_r,mae_r,closed_at,updated_at
              )
              SELECT run_id,NEW.setup_id,'8.0',NEW.source_setup_id,NEW.profile,
                NEW.symbol,NEW.timeframe,NEW.direction,NEW.strategy,NEW.status,
                NEW.result,NEW.result_r,NEW.mfe_r,NEW.mae_r,NEW.closed_at,NEW.updated_at
              FROM verify_active_run_72s WHERE slot=1
              ON CONFLICT(run_id,setup_id) DO UPDATE SET
                build=excluded.build,source_setup_id=excluded.source_setup_id,
                profile=excluded.profile,symbol=excluded.symbol,timeframe=excluded.timeframe,
                direction=excluded.direction,strategy=excluded.strategy,status=excluded.status,
                result=excluded.result,result_r=excluded.result_r,mfe_r=excluded.mfe_r,
                mae_r=excluded.mae_r,closed_at=excluded.closed_at,updated_at=excluded.updated_at;
            END;
            """
        )
        summary["installed"] += 2

    connection.commit()
    return summary


__all__ = ["harden_training_trade_triggers_80"]
