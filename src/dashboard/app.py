from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from src.bridge.futures import normalize_bridge_symbol, source_name
from src.dashboard.queries_59 import DashboardRepository
from src.storage.database import get_connection, save_quotes_batch
from src.storage.intelligence import intelligence_snapshot
from src.storage.learning import learning_snapshot
from src.research.dashboard import ResearchDashboardRepository


ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
STATIC_DIR = Path(__file__).resolve().parent / "static"
DB_PATH = Path(os.getenv("OTR_DB_PATH", ROOT / "data" / "otrmarket.db"))
RESEARCH_DB_PATH = Path(os.getenv("OTR_RESEARCH_DB_PATH", ROOT / "data" / "otr_backtests.db"))
HISTORICAL_DB_PATH = Path(os.getenv("OTR_HISTORICAL_DB_PATH", ROOT / "data" / "otr_historical.db"))

BASE_PATH = "/market"
COOKIE_NAME = "otr_market_session"
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("DASHBOARD_SESSION_SECRET", "").strip() or DASHBOARD_PASSWORD
BRIDGE_KEY = os.getenv("OTR_BRIDGE_KEY", "").strip()
CHART_SYMBOLS = {"NQ", "ES", "GC"}
CHART_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h"}

repository = DashboardRepository(DB_PATH)
research_repository = ResearchDashboardRepository(RESEARCH_DB_PATH, HISTORICAL_DB_PATH)

app = FastAPI(
    title="OTR Market Dashboard",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.mount(f"{BASE_PATH}/assets", StaticFiles(directory=STATIC_DIR), name="market-assets")


class LoginPayload(BaseModel):
    password: str


class BridgeTickPayload(BaseModel):
    symbol: str
    contract: str = ""
    timestamp: str
    last: float
    bid: float | None = None
    ask: float | None = None
    volume: int | None = Field(default=None, ge=0)


class BridgeBatchPayload(BaseModel):
    ticks: list[BridgeTickPayload] = Field(min_length=1, max_length=5000)


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
    # Keep verbose research intelligence off the normal dashboard. The data is
    # still stored and available from protected APIs when we need to audit it.
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # Force clients to fetch the final Operation 6.5 trade-history renderer.
    # Keeping this server-side avoids another stale Safari/desktop asset after
    # the load-order repair while leaving the static source file simple.
    html = html.replace(
        "/market/assets/trade-history-cleanup.js?v=6.5.2",
        "/market/assets/trade-history-cleanup.js?v=6.5.4",
    )
    # Operation 7.0 added the independent decision-funnel panel. Operation 8.1
    # keeps the same asset path but bumps the query version so every browser
    # receives the Gold candidate-to-fill conversion renderer immediately.
    if "decision-telemetry.js" not in html:
        html = html.replace(
            "</body>",
            '<script src="/market/assets/decision-telemetry.js?v=8.1-conversion1"></script>\n</body>',
        )
    return HTMLResponse(html)


@app.get(f"{BASE_PATH}/research")
@app.get(f"{BASE_PATH}/research/")
async def research_index():
    return HTMLResponse((STATIC_DIR / "research.html").read_text(encoding="utf-8"))


@app.get(f"{BASE_PATH}/api/research/runs")
async def research_runs(request: Request):
    require_http_auth(request)
    return {"runs": research_repository.list_runs(), "read_only": True}


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}")
async def research_run_detail(run_id: str, request: Request):
    require_http_auth(request)
    detail = research_repository.run_detail(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    return detail


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/equity")
async def research_equity(run_id: str, request: Request):
    require_http_auth(request)
    return {"items": research_repository.equity(run_id), "read_only": True}


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/trades")
async def research_trades(
    run_id: str, request: Request, market: str = "", strategy_type: str = "",
    timeframe: str = "", setup_grade: str = "", direction: str = "",
    session: str = "", result: str = "", recovery_state: str = "",
):
    require_http_auth(request)
    return {"items": research_repository.trades(run_id, {
        "symbol": market, "strategy_type": strategy_type, "timeframe": timeframe,
        "setup_grade": setup_grade, "direction": direction, "session": session,
        "result": result, "recovery_state": recovery_state,
    })}


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/trades/{{trade_id}}")
async def research_trade_detail(run_id: str, trade_id: str, request: Request):
    require_http_auth(request)
    detail = research_repository.trade_detail(run_id, trade_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Research trade not found")
    return detail


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/decisions")
async def research_decisions(
    run_id: str, request: Request, symbol: str = "", timeframe: str = "",
    strategy_type: str = "", grade: str = "", decision: str = "",
):
    require_http_auth(request)
    return {"items": research_repository.decisions(run_id, {
        "symbol": symbol, "timeframe": timeframe, "strategy_type": strategy_type,
        "grade": grade, "decision": decision,
    })}


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/decisions/{{decision_id}}")
async def research_decision_detail(run_id: str, decision_id: str, request: Request):
    require_http_auth(request)
    detail = research_repository.decision_detail(run_id, decision_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Research decision not found")
    return detail


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/summary")
async def research_summary(run_id: str, request: Request):
    require_http_auth(request)
    summary = research_repository.summary(run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    return summary


@app.get(f"{BASE_PATH}/api/snapshot")
async def snapshot(request: Request):
    require_http_auth(request)
    return repository.snapshot()


@app.get(f"{BASE_PATH}/api/intelligence")
async def intelligence(request: Request):
    require_http_auth(request)
    connection = get_connection()
    try:
        return intelligence_snapshot(connection)
    finally:
        connection.close()


@app.get(f"{BASE_PATH}/api/learning")
async def learning(request: Request):
    require_http_auth(request)
    connection = get_connection()
    try:
        return learning_snapshot(connection)
    finally:
        connection.close()


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
        secure=os.getenv("DASHBOARD_SECURE_COOKIE", "1").strip().lower() not in {"0", "false", "no", "off"},
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        path="/",
    )
    return {"ok": True}


@app.post(f"{BASE_PATH}/api/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.post(f"{BASE_PATH}/api/bridge/ticks")
async def bridge_ticks(payload: BridgeBatchPayload, request: Request):
    provided = request.headers.get("x-otr-bridge-key", "")
    if BRIDGE_KEY and not hmac.compare_digest(provided, BRIDGE_KEY):
        raise HTTPException(status_code=401, detail="Invalid bridge key")

    rows = []
    futures_seen = set()
    for tick in payload.ticks:
        normalized = normalize_bridge_symbol(tick.symbol)
        if normalized not in CHART_SYMBOLS:
            continue
        futures_seen.add(normalized)
        rows.append((
            normalized,
            tick.contract,
            tick.timestamp,
            tick.last,
            tick.bid,
            tick.ask,
            tick.volume,
            source_name(normalized),
        ))

    if not rows:
        return {"accepted": 0, "symbols": []}

    connection = get_connection()
    try:
        inserted = save_quotes_batch(connection, rows)
    finally:
        connection.close()
    return {"accepted": inserted, "symbols": sorted(futures_seen)}


async def _ws_authorized(websocket: WebSocket) -> bool:
    return valid_cookie(websocket.cookies.get(COOKIE_NAME))


@app.websocket(f"{BASE_PATH}/ws")
async def market_ws(websocket: WebSocket):
    if not await _ws_authorized(websocket):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(repository.snapshot())
            await asyncio.sleep(2)
    except (WebSocketDisconnect, RuntimeError):
        return
