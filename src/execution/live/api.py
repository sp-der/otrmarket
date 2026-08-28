from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.storage.database import get_connection

from .config import ExecutionConfig
from .models import ExecutionMode, utc_now
from .safety import bridge_dispatch_ready
from .store import ensure_schema, execution_status, poll_commands, record_bridge_snapshot, record_event, set_state


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
            ready = bridge_dispatch_ready(connection, config)
            if not ready.allowed:
                return {"ok": True, "dispatch_ready": False, "reason": ready.reason, "code": ready.code, "mode": config.mode.value, "account": config.account, "commands": []}
            commands = poll_commands(connection, account=config.account, mode=config.mode.value, max_items=limit, redelivery_seconds=config.claimed_redelivery_seconds)
            return {"ok": True, "dispatch_ready": True, "mode": config.mode.value, "account": config.account, "commands": commands}
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
        }
        status["broker_transmission_possible"] = bool(config.armed and config.mode != ExecutionMode.PAPER and (config.mode != ExecutionMode.LIVE or (config.live_allowed and config.certified)))
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
            set_state(
                connection,
                "kill_switch_audit",
                {
                    "enabled": payload.enabled,
                    "reason": payload.reason,
                    "changed_at": utc_now().isoformat(),
                },
            )
            return {"ok": True, "enabled": payload.enabled, "reason": payload.reason}
        finally:
            connection.close()

    return router
