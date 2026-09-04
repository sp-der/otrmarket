from __future__ import annotations

import sqlite3


CANONICAL_TRIGGER_NAMES = (
    "training_trade_insert_72t",
    "training_trade_update_72t",
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


def harden_training_trade_triggers_80(connection: sqlite3.Connection) -> dict[str, int]:
    """Replace every historical training-trade trigger with one canonical pair.

    SQLite trigger definitions live inside the persistent database. Renaming a
    trigger in source does not delete the old trigger from an existing volume,
    so an obsolete plain-INSERT trigger can continue firing months later and
    violate the (run_id, setup_id) primary key. Operation 8.0 treats the DB
    catalog as part of runtime state: remove every trigger whose SQL writes to
    training_trades_72t, then install exactly two idempotent triggers.
    """
    summary = {"dropped": 0, "installed": 0}
    if not _table_exists(connection, "paper_trades"):
        return summary
    if not _table_exists(connection, "training_trades_72t"):
        return summary
    if not _table_exists(connection, "verify_active_run_72s"):
        return summary

    rows = connection.execute(
        """
        SELECT name, COALESCE(sql, '')
        FROM sqlite_master
        WHERE type='trigger'
        """
    ).fetchall()
    for name, sql in rows:
        name_text = str(name or "")
        sql_text = str(sql or "").lower()
        if "training_trades_72t" not in sql_text and "training_trade" not in name_text.lower():
            continue
        connection.execute(f"DROP TRIGGER IF EXISTS {_quote_identifier(name_text)}")
        summary["dropped"] += 1

    connection.executescript(
        """
        CREATE TRIGGER training_trade_insert_72t
        AFTER INSERT ON paper_trades
        BEGIN
          INSERT OR IGNORE INTO training_trades_72t(
            run_id,setup_id,build,symbol,timeframe,direction,status,opened_at,
            closed_at,result,result_r,risk_dollars,result_dollars,updated_at
          )
          SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.direction,
            NEW.status,NEW.opened_at,NEW.closed_at,NEW.result,NEW.result_r,
            NEW.risk_dollars,NEW.result_dollars,NEW.updated_at
          FROM verify_active_run_72s WHERE slot=1;
          UPDATE training_trades_72t SET
            build='8.0',symbol=NEW.symbol,timeframe=NEW.timeframe,direction=NEW.direction,
            status=NEW.status,opened_at=NEW.opened_at,closed_at=NEW.closed_at,
            result=NEW.result,result_r=NEW.result_r,
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
          SELECT run_id,NEW.setup_id,'8.0',NEW.symbol,NEW.timeframe,NEW.direction,
            NEW.status,NEW.opened_at,NEW.closed_at,NEW.result,NEW.result_r,
            NEW.risk_dollars,NEW.result_dollars,NEW.updated_at
          FROM verify_active_run_72s WHERE slot=1;
          UPDATE training_trades_72t SET
            build='8.0',symbol=NEW.symbol,timeframe=NEW.timeframe,direction=NEW.direction,
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
    summary["installed"] = 2
    return summary


__all__ = ["harden_training_trade_triggers_80"]
