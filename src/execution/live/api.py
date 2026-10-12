from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.storage.database import get_connection

from .config import ExecutionConfig
from .models import utc_now
from .safety import bridge_dispatch_ready
from .store import (
    cancel_directives,
    cancel_pending_commands,
    daily_execution_ledger,
    ensure_schema,
    execution_status,
    get_state,
    latest_market_event_at,
    poll_commands,
    record_bridge_snapshot,
    record_event,
    set_state,
)


BASE = "/market/api"
KILL_SWITCH_RESET_CONFIRMATION = "RESET_EXECUTION_KILL_SWITCH"


class ExecutionEventPayload(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    command_id: str | None = None
    event_type: str = Field(min_length=1, max_length=64)
    broker_order_id: str | None = None
    quantity: int | None = None
    filled_quantity: int | None = None
    price: float | None = None
    message: str | None = None
    occurred_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionEventsPayload(BaseModel):
    events: list[ExecutionEventPayload] = Field(min_length=1, max_length=500)


class BrokerSnapshotPayload(BaseModel):
    bridge_id: str = Field(min_length=1, max_length=128)
    timestamp: str
    account: str = Field(min_length=1, max_length=128)
    positions: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    orders: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class KillSwitchPayload(BaseModel):
    enabled: bool
    reason: str = Field(default="", max_length=500)
    confirmation: str = Field(default="", max_length=128)


def build_router(*, require_http_auth: Callable[[Request], None], require_bridge_key: Callable[[Request], None]) -> APIRouter:
    router = APIRouter()

    @router.get(f"{BASE}/bridge/execution/commands")
    async def execution_commands(request: Request, limit: int = 10):
        require_bridge_key(request)
        config = ExecutionConfig.from_env()
        connection = get_connection()
        try:
            ensure_schema(connection)
            kill_switch, _ = get_state(connection, "kill_switch", False)
            # Cancel directives are delivered even when dispatch is blocked:
            # a kill switch or stale entry must still reach resting orders.
            cancels = cancel_directives(
                connection,
                account=config.account,
                mode=config.mode.value,
                kill_switch=bool(kill_switch) or os.getenv("OTR_EXECUTION_KILL_SWITCH", "0").strip().lower() in {"1", "true", "yes", "on"},
                replay_clock=config.replay_clock,
            )
            ready = bridge_dispatch_ready(connection, config)
            if not ready.allowed:
                return {"ok": True, "dispatch_ready": False, "reason": ready.reason, "code": ready.code, "mode": config.mode.value, "account": config.account, "commands": [], "cancels": cancels}
            commands = poll_commands(connection, account=config.account, mode=config.mode.value, max_items=limit, redelivery_seconds=config.claimed_redelivery_seconds)
            return {"ok": True, "dispatch_ready": True, "mode": config.mode.value, "account": config.account, "commands": commands, "cancels": cancels}
        finally:
            connection.close()

    @router.post(f"{BASE}/bridge/execution/events")
    async def execution_events(payload: ExecutionEventsPayload, request: Request):
        require_bridge_key(request)
        connection = get_connection()
        accepted = []
        try:
            ensure_schema(connection)
            for item in payload.events:
                try:
                    event = item.model_dump() if hasattr(item, "model_dump") else item.dict()
                    if not event.get("occurred_at"):
                        event["occurred_at"] = utc_now().isoformat()
                    accepted.append(record_event(connection, event))
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"ok": True, "accepted": len(accepted)}
        finally:
            connection.close()

    @router.post(f"{BASE}/bridge/execution/snapshot")
    async def execution_snapshot(payload: BrokerSnapshotPayload, request: Request):
        require_bridge_key(request)
        config = ExecutionConfig.from_env()
        connection = get_connection()
        try:
            ensure_schema(connection)
            snapshot = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
            verdict = record_bridge_snapshot(connection, snapshot, configured_account=config.account)
            return {"ok": True, "reconciled": bool(verdict["ok"]), "reason": verdict["reason"], "mode": config.mode.value, "armed": config.armed, "account": config.account}
        finally:
            connection.close()

    @router.get(f"{BASE}/execution/status")
    async def execution_status_route(request: Request):
        require_http_auth(request)
        config = ExecutionConfig.from_env()
        connection = get_connection()
        try:
            status = execution_status(connection)
            dispatch = bridge_dispatch_ready(connection, config)
            ledger_reference = None
            if config.replay_clock and config.allowed_symbols:
                # Replay day attribution follows the replay stream, not today.
                ledger_reference = latest_market_event_at(connection, config.allowed_symbols[0])
            status["daily_ledger"] = daily_execution_ledger(
                connection,
                account=config.account,
                now=ledger_reference,
                tz_name=config.session_tz,
                roll_hour=config.trading_day_roll_hour,
                commission_per_contract_rt=config.commission_per_contract_rt,
                replay_clock=config.replay_clock,
            )
        finally:
            connection.close()
        status["config"] = {
            "mode": config.mode.value,
            "armed": config.armed,
            "account": config.account,
            "sim_account": config.is_sim_account,
            "live_allowed": config.live_allowed,
            "certified": config.certified,
            "max_micros": config.max_micros,
            "max_risk_dollars": config.max_risk_dollars,
            "allowed_symbols": list(config.allowed_symbols),
            "max_active_commands": config.max_active_commands,
            "max_daily_loss_dollars": config.max_daily_loss_dollars,
            "max_trades_per_day": config.max_trades_per_day,
            "max_data_age_seconds": config.max_data_age_seconds,
            "data_clock": config.data_clock,
            "max_signal_lag_seconds": config.max_signal_lag_seconds,
        }
        status["dispatch"] = dispatch.to_dict()
        status["broker_transmission_possible"] = bool(dispatch.allowed)
        return status

    @router.post(f"{BASE}/execution/kill-switch")
    async def execution_kill_switch(payload: KillSwitchPayload, request: Request):
        require_http_auth(request)
        if not payload.enabled and payload.confirmation != KILL_SWITCH_RESET_CONFIRMATION:
            raise HTTPException(
                status_code=400,
                detail=f"Reset requires confirmation={KILL_SWITCH_RESET_CONFIRMATION!r}",
            )
        connection = get_connection()
        try:
            set_state(connection, "kill_switch", payload.enabled)
            cancelled = 0
            if payload.enabled:
                # Undelivered commands must never be dispatched after a later
                # reset; delivered-but-unfilled entries get cancel directives.
                cancelled = cancel_pending_commands(connection, "Kill switch engaged before broker delivery.")
            set_state(
                connection,
                "kill_switch_audit",
                {
                    "enabled": payload.enabled,
                    "reason": payload.reason,
                    "changed_at": utc_now().isoformat(),
                },
            )
            return {"ok": True, "enabled": payload.enabled, "reason": payload.reason, "cancelled_pending": cancelled}
        finally:
            connection.close()

    return router
