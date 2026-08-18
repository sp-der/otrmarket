from __future__ import annotations

import json
from datetime import datetime, timezone

from src.execution.paper import PaperPosition
from src.strategies.models import Displacement, FairValueGap, StrategySetup


def _parse_dt(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fair_value_gap(data: dict) -> FairValueGap:
    return FairValueGap(
        symbol=str(data["symbol"]),
        timeframe=str(data["timeframe"]),
        direction=str(data["direction"]),
        lower=float(data["lower"]),
        upper=float(data["upper"]),
        formed_at=_parse_dt(data["formed_at"]),
        candle1_time=_parse_dt(data["candle1_time"]),
        candle3_time=_parse_dt(data["candle3_time"]),
    )


def _displacement(data: dict) -> Displacement:
    return Displacement(
        symbol=str(data["symbol"]),
        timeframe=str(data["timeframe"]),
        direction=str(data["direction"]),
        candle_time=_parse_dt(data["candle_time"]),
        low=float(data["low"]),
        high=float(data["high"]),
        body_ratio=float(data["body_ratio"]),
        range_ratio=float(data["range_ratio"]),
    )


def setup_from_payload(payload: str | dict) -> StrategySetup:
    data = json.loads(payload) if isinstance(payload, str) else dict(payload)
    return StrategySetup(
        setup_id=str(data["setup_id"]),
        symbol=str(data["symbol"]),
        timeframe=str(data["timeframe"]),
        direction=str(data["direction"]),
        created_at=_parse_dt(data["created_at"]),
        pd_array=_fair_value_gap(data["pd_array"]),
        trigger_type=str(data["trigger_type"]),
        trigger_details=dict(data.get("trigger_details") or {}),
        displacement=_displacement(data["displacement"]),
        entry_fvg=_fair_value_gap(data["entry_fvg"]),
        entry_price=float(data["entry_price"]),
        stop_price=float(data["stop_price"]),
        target_price=float(data["target_price"]),
        risk_reward=float(data["risk_reward"]),
        status=str(data.get("status") or "PENDING"),
        metadata=dict(data.get("metadata") or {}),
    )


def restore_active_paper_positions(connection, executor) -> tuple[int, list[str]]:
    """Rehydrate persisted PENDING/OPEN paper positions after a process restart."""
    rows = connection.execute(
        """
        SELECT p.setup_id, p.status, p.opened_at, p.closed_at, p.exit_price,
               p.result, p.result_r, p.risk_dollars, p.result_dollars,
               p.guard_reason, s.payload_json
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id = p.setup_id
        WHERE p.status IN ('PENDING', 'OPEN')
        ORDER BY p.updated_at ASC
        """
    ).fetchall()

    restored = 0
    errors: list[str] = []
    for row in rows:
        setup_id = str(row[0])
        if setup_id in executor.positions:
            continue
        try:
            setup = setup_from_payload(row[10])
            setup.status = str(row[1])
            position = PaperPosition(
                setup=setup,
                status=str(row[1]),
                opened_at=_parse_dt(row[2]),
                closed_at=_parse_dt(row[3]),
                exit_price=float(row[4]) if row[4] is not None else None,
                result_r=float(row[6]) if row[6] is not None else None,
                result=str(row[5]) if row[5] is not None else None,
                risk_dollars=float(row[7]) if row[7] is not None else None,
                result_dollars=float(row[8]) if row[8] is not None else None,
                guard_reason=str(row[9]) if row[9] is not None else None,
            )
            executor.positions[setup_id] = position
            restored += 1
        except Exception as exc:
            errors.append(f"{setup_id}: {exc}")
    return restored, errors


def restore_recent_stale_watches(connection, continuation_engine) -> int:
    """Restore recent stale-entry thesis watches without reopening invalid orders."""
    rows = connection.execute(
        """
        SELECT p.updated_at, s.payload_json
        FROM paper_trades p
        JOIN strategy_setups s ON s.setup_id = p.setup_id
        WHERE p.result = 'STALE_MOVE_BEFORE_ENTRY'
        ORDER BY p.updated_at DESC
        LIMIT 24
        """
    ).fetchall()

    restored = 0
    seen = set()
    for updated_at, payload_json in rows:
        try:
            setup = setup_from_payload(payload_json)
            key = (setup.symbol, setup.timeframe)
            if key in seen:
                continue
            latest = connection.execute(
                """
                SELECT close_time
                FROM candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY close_time DESC
                LIMIT 1
                """,
                key,
            ).fetchone()
            if not latest:
                continue
            event_time = _parse_dt(updated_at)
            latest_time = _parse_dt(latest[0])
            max_age_seconds = continuation_engine.max_watch_seconds(setup.timeframe)
            age = (latest_time - event_time).total_seconds()
            if age < 0 or age > max_age_seconds:
                continue
            if continuation_engine.arm_from_stale(setup, event_time):
                restored += 1
                seen.add(key)
        except Exception:
            continue
    return restored
