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
    # Operation 7.0 adds an independent decision-funnel panel. Injecting it here
    # keeps the existing dashboard renderer untouched and cache-busts the new UI.
    if "decision-telemetry.js" not in html:
        html = html.replace(
            "</body>",
            '<script src="/market/assets/decision-telemetry.js?v=7.0"></script>\n</body>',
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
    return {"items": research_repository.equity(run_id)}


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
    reason: str = "", session: str = "", recovery_state: str = "",
):
    require_http_auth(request)
    return {"items": research_repository.decisions(run_id, {
        "symbol": symbol, "timeframe": timeframe, "strategy_type": strategy_type,
        "grade": grade, "decision": decision, "reason": reason,
        "session": session, "recovery_state": recovery_state,
    })}


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/blocked")
async def research_blocked(run_id: str, request: Request):
    require_http_auth(request)
    return {"items": research_repository.blocked_setups(run_id)}


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/pending-expirations")
async def research_pending_expirations(run_id: str, request: Request):
    require_http_auth(request)
    return research_repository.pending_expirations(run_id)


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/risk-audits")
async def research_risk_audits(run_id: str, request: Request):
    require_http_auth(request)
    return {"items": research_repository.risk_audits(run_id)}


@app.get(f"{BASE_PATH}/api/research/runs/{{run_id}}/recovery")
async def research_recovery(run_id: str, request: Request):
    require_http_auth(request)
    return {"items": research_repository.recovery_timeline(run_id)}


@app.get(f"{BASE_PATH}/api/research/coverage")
async def research_coverage(request: Request, capture_id: str = ""):
    require_http_auth(request)
    return research_repository.coverage(capture_id or None)


@app.get(f"{BASE_PATH}/api/research/experiments")
async def research_experiments(request: Request):
    require_http_auth(request)
    return {"items": research_repository.experiments(), "read_only": True}


@app.get(f"{BASE_PATH}/api/research/experiments/{{experiment_id}}")
async def research_experiment_detail(experiment_id: str, request: Request):
    require_http_auth(request)
    detail = research_repository.experiment_detail(experiment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Research experiment not found")
    return detail


def engine_process_status() -> tuple[bool, int | None]:
    pid_file = Path(os.getenv("OTR_RUNTIME_DIR", "/tmp/otrmarket")) / "engine.pid"
    if not pid_file.exists():
        return False, None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        cmdline = Path(f"/proc/{pid}/cmdline")
        if not cmdline.exists():
            return False, pid
        command = cmdline.read_bytes().replace(b"\0", b" ").decode(errors="replace")
        return "src.main" in command, pid
    except Exception:
        return False, None


@app.get(f"{BASE_PATH}/api/health")
async def health(response: Response):
    require_engine = os.getenv("OTR_REQUIRE_ENGINE_HEALTH", "0") == "1"
    engine_running, engine_pid = engine_process_status()
    ok = (not require_engine) or engine_running
    if not ok:
        response.status_code = 503
    return {
        "ok": ok,
        "database_exists": DB_PATH.exists(),
        "mode": "paper",
        "bridge_configured": bool(BRIDGE_KEY),
        "engine_running": engine_running if require_engine else None,
        "engine_pid": engine_pid if require_engine else None,
    }


def require_bridge_key(request: Request) -> None:
    if not BRIDGE_KEY:
        raise HTTPException(status_code=503, detail="OTR bridge key is not configured")
    supplied = request.headers.get("X-OTR-Bridge-Key", "")
    if not hmac.compare_digest(supplied, BRIDGE_KEY):
        raise HTTPException(status_code=401, detail="Invalid OTR bridge key")


@app.post(f"{BASE_PATH}/api/bridge/ticks")
async def bridge_ticks(payload: BridgeBatchPayload, request: Request):
    require_bridge_key(request)

    rows = []
    accepted = {"NQ": 0, "ES": 0, "GC": 0}
    for item in payload.ticks:
        try:
            symbol = normalize_bridge_symbol(item.symbol)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        rows.append(
            (
                item.timestamp,
                item.timestamp,
                source_name(item.contract),
                symbol,
                float(item.last),
                float(item.bid) if item.bid is not None else None,
                float(item.ask) if item.ask is not None else None,
            )
        )
        accepted[symbol] += 1

    connection = get_connection()
    try:
        inserted = save_quotes_batch(connection, rows)
    finally:
        connection.close()

    return {"ok": True, "inserted": inserted, "symbols": accepted}


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
        return JSONResponse(status_code=500, content={"detail": f"Dashboard snapshot failed: {exc}"})


@app.get(f"{BASE_PATH}/api/intelligence")
async def intelligence(request: Request):
    require_http_auth(request)
    try:
        return intelligence_snapshot(DB_PATH)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": f"Trade intelligence snapshot failed: {exc}"})


@app.get(f"{BASE_PATH}/api/learning")
async def learning(request: Request):
    require_http_auth(request)
    try:
        return learning_snapshot(DB_PATH)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"detail": f"Market learning snapshot failed: {exc}"})


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
