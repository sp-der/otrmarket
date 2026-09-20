"""Read-only, protected dashboard/API surfaces for Operation 8.2 -- SHADOW
ONLY. Every response is explicitly marked authoritative=false / shadow_only
=true so no client can mistake this for an execution decision.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from src.otr8.confluence_intelligence import store as store_mod
from src.otr8.confluence_intelligence.service import (
    recent_snapshots,
    snapshot_with_dashboard_context,
)
from src.research.run_scope import current_run_id
from src.storage.database import get_connection


def _resolve_route_run_id(connection, run_id: str | None) -> str | None:
    """?run_id= omitted -> the active run (default). ?run_id=ALL -> every run,
    pooled, an explicit opt-in. ?run_id=<id> -> that one historical run.
    Mirrors src.research.lab_v01_routes._resolve_route_run_id so the two
    research surfaces behave identically for operators.
    """
    if run_id is None:
        return current_run_id(connection)
    if run_id.strip().upper() == "ALL":
        return None
    return run_id


def _page_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>OTR Confluence Intelligence</title>
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:#080808;color:#f4f4f4}
main{max-width:1180px;margin:0 auto;padding:28px 18px 64px}
h1{font-size:clamp(28px,5vw,48px);letter-spacing:-.045em;margin:0 0 4px}.sub{color:#929292;line-height:1.5;max-width:850px;margin-bottom:20px}
.badge{display:inline-flex;border:1px solid #2d2d2d;background:#111;border-radius:999px;padding:7px 10px;font-size:12px;color:#aaa;margin:0 8px 8px 0}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 18px}button{background:#f2f2f2;color:#0b0b0b;border:0;border-radius:10px;padding:10px 14px;font:inherit;font-weight:800;cursor:pointer}button.secondary{background:#171717;color:#fff;border:1px solid #303030}
.section{margin-top:25px}h2{font-size:18px;margin:0 0 10px}table{width:100%;border-collapse:collapse;background:#0f0f0f;border:1px solid #252525}th,td{padding:10px;border-bottom:1px solid #242424;text-align:left;vertical-align:top;font-size:13px}th{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#838383}.ok{color:#76e59a}.warn{color:#ffd479}.bad{color:#ff8b8b}.muted{color:#858585}
#status{min-height:20px;color:#9b9b9b;margin-bottom:8px}
@media(max-width:620px){table{display:block;overflow-x:auto}}
</style>
</head>
<body><main>
<h1>OTR Confluence Intelligence <span class="muted">Operation 8.2</span></h1>
<div class="sub">Shadow multi-timeframe research layer. For every GC setup Operation 8.1 evaluates, it records what a stronger confluence-intelligence layer would have concluded -- local quality, HTF alignment/conflict, similar-setup history, and (once enough evidence exists) a meta-model opinion. It never places, blocks, resizes, or approves a trade; Operation 8.1 remains the sole authoritative trading engine.</div>
<div><span class="badge">GC ONLY</span><span class="badge">SHADOW ONLY</span><span class="badge">NON-AUTHORITATIVE</span></div>
<div class="controls"><button class="secondary" id="refresh">Refresh</button><a href="/market/research-lab"><button class="secondary" type="button">Research Lab</button></a></div>
<div id="status"></div>
<div class="section"><h2>Recent GC Setups: OTR Decision vs. Shadow Confluence Score</h2>
<table><thead><tr><th>Captured</th><th>Setup</th><th>OTR Status</th><th>OTR Result</th><th>Local Quality</th><th>HTF Alignment</th><th>HTF Conflict</th><th>Combined</th><th>Shadow Action</th><th>Similar Setups</th><th>Model</th><th>MFE</th><th>MAE</th></tr></thead><tbody id="rows"></tbody></table>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const num=(v,d)=>v==null?'n/a':Number(v).toFixed(d??1);
function paint(d){
 const rows=d.recent||[];
 $('rows').innerHTML=rows.map(r=>{
  const conflict=r.htf_conflict_level==='HIGH'?'bad':r.htf_conflict_level==='MODERATE'?'warn':'ok';
  const action=r.shadow_action==='REJECT'?'bad':r.shadow_action==='DOWNGRADE'||r.shadow_action==='WAIT'?'warn':'ok';
  return `<tr><td>${esc(r.captured_at)}</td><td>${esc(r.setup_id)}</td><td>${esc(r.otr_trade_status||r.otr_status)}</td><td>${esc(r.otr_result)}</td><td>${num(r.local_quality_score,0)}</td><td>${num(r.htf_alignment_score,0)}</td><td class="${conflict}">${esc(r.htf_conflict_level)}</td><td>${num(r.combined_shadow_score,1)}</td><td class="${action}">${esc(r.shadow_action)}</td><td>${esc(r.similar_setup_status)}</td><td>${esc(r.model_status)}</td><td>${num(r.mfe_r,2)}</td><td>${num(r.mae_r,2)}</td></tr>`;
 }).join('')||'<tr><td colspan="13" class="muted">No GC confluence snapshots yet.</td></tr>';
}
async function load(){const r=await fetch('/market/api/confluence-intelligence',{credentials:'same-origin'});if(!r.ok)throw new Error('Confluence Intelligence HTTP '+r.status);paint(await r.json())}
$('refresh').onclick=()=>load().catch(e=>$('status').textContent=e.message);
load().catch(e=>$('status').textContent=e.message);
</script></body></html>"""


def install_confluence_intelligence_routes() -> None:
    """Register protected, read-only Operation 8.2 surfaces.

    Never mutates strategy_setups/paper_trades and never affects Operation
    8.1 execution -- this only reads confluence_snapshots_v82 (and joins it,
    at read time, with paper_trades/nautilus_shadow_parity for display).
    """
    from src.dashboard import app as dashboard

    connection = get_connection()
    try:
        store_mod.ensure_schema(connection)
    finally:
        connection.close()

    root = f"{dashboard.BASE_PATH}/api/confluence-intelligence"
    detail_path = root + "/{setup_id}"
    page_path = f"{dashboard.BASE_PATH}/confluence-intelligence"
    existing = {getattr(route, "path", None) for route in dashboard.app.routes}

    async def list_snapshots_route(request: Request, limit: int = 25, run_id: str | None = None):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            resolved_run_id = _resolve_route_run_id(connection, run_id)
            return {
                "authoritative": False,
                "shadow_only": True,
                "recent": recent_snapshots(
                    connection, run_id=resolved_run_id, limit=max(1, min(int(limit), 200))
                ),
            }
        finally:
            connection.close()

    async def snapshot_detail_route(request: Request, setup_id: str):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            snapshot = snapshot_with_dashboard_context(connection, setup_id)
            if snapshot is None:
                raise HTTPException(status_code=404, detail="No confluence snapshot for this setup_id")
            snapshot["authoritative"] = False
            snapshot["shadow_only"] = True
            return snapshot
        finally:
            connection.close()

    async def page(request: Request):
        dashboard.require_http_auth(request)
        return HTMLResponse(_page_html())

    if root not in existing:
        dashboard.app.add_api_route(
            root, list_snapshots_route, methods=["GET"], name="confluence_intelligence_snapshots"
        )
    if detail_path not in existing:
        dashboard.app.add_api_route(
            detail_path, snapshot_detail_route, methods=["GET"], name="confluence_intelligence_snapshot_detail"
        )
    if page_path not in existing:
        dashboard.app.add_api_route(
            page_path, page, methods=["GET"], name="confluence_intelligence_page", response_class=HTMLResponse
        )
