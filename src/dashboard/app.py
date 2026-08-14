from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.dashboard.queries import DashboardRepository


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DB_PATH = Path(os.getenv("OTR_DB_PATH", ROOT / "data" / "otrmarket.db"))

BASE_PATH = "/market"
COOKIE_NAME = "otr_market_session"
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET", "").strip() or DASHBOARD_PASSWORD

repository = DashboardRepository(DB_PATH)

app = FastAPI(
    title="OTR Market Dashboard",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount(f"{BASE_PATH}/assets", StaticFiles(directory=STATIC_DIR), name="market-assets")


class LoginPayload(BaseModel):
    password: str


def auth_required() -> bool:
    return bool(DASHBOARD_PASSWORD)


def valid_cookie(cookie_value: str | None) -> bool:
    if not auth_required():
        return True
    if not cookie_value or not SESSION_SECRET:
        return False
    expected = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        b"otr-market-dashboard",
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(cookie_value, expected)


def require_http_auth(request: Request) -> None:
    if not valid_cookie(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Dashboard authentication required")


def session_token() -> str:
    return hmac.new(
        SESSION_SECRET.encode("utf-8"),
        b"otr-market-dashboard",
        hashlib.sha256,
    ).hexdigest()


@app.get("/")
async def root_redirect():
    return RedirectResponse(url=f"{BASE_PATH}/")


@app.get(BASE_PATH)
@app.get(f"{BASE_PATH}/")
async def dashboard_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get(f"{BASE_PATH}/api/health")
async def health():
    return {
        "ok": True,
        "database_exists": DB_PATH.exists(),
        "mode": "paper",
    }


@app.get(f"{BASE_PATH}/api/auth-status")
async def auth_status(request: Request):
    return {
        "required": auth_required(),
        "authenticated": valid_cookie(request.cookies.get(COOKIE_NAME)),
    }


@app.post(f"{BASE_PATH}/api/login")
async def login(payload: LoginPayload, response: Response):
    if not auth_required():
        return {"ok": True}

    if not hmac.compare_digest(payload.password, DASHBOARD_PASSWORD):
        raise HTTPException(status_code=401, detail="Incorrect password")

    response.set_cookie(
        COOKIE_NAME,
        session_token(),
        httponly=True,
        samesite="strict",
        secure=os.getenv("DASHBOARD_SECURE_COOKIE", "0") == "1",
        max_age=60 * 60 * 24 * 30,
        path=BASE_PATH,
    )
    return {"ok": True}


@app.post(f"{BASE_PATH}/api/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path=BASE_PATH)
    return {"ok": True}


@app.get(f"{BASE_PATH}/api/snapshot")
async def snapshot(request: Request):
    require_http_auth(request)
    try:
        return repository.snapshot()
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Dashboard snapshot failed: {exc}"},
        )


@app.websocket(f"{BASE_PATH}/ws")
async def websocket_stream(websocket: WebSocket):
    if not valid_cookie(websocket.cookies.get(COOKIE_NAME)):
        await websocket.close(code=4401)
        return

    await websocket.accept()

    try:
        while True:
            await websocket.send_json(repository.snapshot())
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
