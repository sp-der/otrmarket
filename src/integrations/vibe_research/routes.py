from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from src.integrations.vibe_research.presentation import normalize_vibe_snapshot
from src.integrations.vibe_research.worker import (
    start_vibe_research_worker,
    vibe_research_snapshot,
)
from src.storage.database import get_connection


def _page_html() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OTR Vibe Research</title>
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:#080808;color:#f5f5f5}main{max-width:1100px;margin:auto;padding:28px 18px 60px}h1{font-size:clamp(28px,5vw,46px);letter-spacing:-.04em;margin:0 0 6px}.sub{color:#999;line-height:1.5;max-width:850px}.badges{margin:16px 0}.badge{display:inline-block;border:1px solid #303030;background:#111;border-radius:999px;padding:7px 10px;margin:0 7px 7px 0;font-size:11px;color:#aaa}.grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:18px 0}.card{background:#111;border:1px solid #252525;border-radius:14px;padding:14px}.label{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#888}.value{font-size:22px;font-weight:850;margin-top:5px}.ok{color:#76e59a}.warn{color:#ffd479}.muted{color:#888}table{width:100%;border-collapse:collapse;border:1px solid #252525;background:#0f0f0f}th,td{padding:10px;border-bottom:1px solid #242424;text-align:left;font-size:12px;vertical-align:top}th{color:#888;text-transform:uppercase;font-size:10px;letter-spacing:.08em}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0e0e0e;border:1px solid #252525;border-radius:12px;padding:12px}.links a{color:#ddd;margin-right:14px}@media(max-width:800px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}table{display:block;overflow-x:auto}}
</style></head><body><main>
<h1>Vibe Research Sidecar</h1>
<div class="sub">Vibe-Trading analyzes closed Gold replay evidence after Nautilus reaches an execution verdict. It is isolated from OTR's Python runtime, receives no broker authority, and cannot change Operation 8.1 or risk.</div>
<div class="badges"><span class="badge">VIBE-TRADING 0.1.15</span><span class="badge">NON-AUTHORITATIVE</span><span class="badge">READ-ONLY RESEARCH</span><span class="badge">NAUTILUS-FIRST</span></div>
<div class="links"><a href="/market/research-lab">Research Lab</a><a href="/market/nautilus-parity">Nautilus Parity</a></div>
<div class="grid">
<div class="card"><div class="label">Worker</div><div class="value" id="worker">...</div></div>
<div class="card"><div class="label">Package</div><div class="value" id="package">...</div></div>
<div class="card"><div class="label">Provider</div><div class="value" id="provider">...</div></div>
<div class="card"><div class="label">Captured</div><div class="value" id="captured">0</div></div>
<div class="card"><div class="label">Findings</div><div class="value" id="findings">0</div></div>
</div>
<div id="message" class="sub"></div>
<h2>Recent Jobs</h2><table><thead><tr><th>Setup</th><th>Status</th><th>Updated</th><th>Provider</th><th>Detail</th></tr></thead><tbody id="jobs"></tbody></table>
<h2>Recent Findings</h2><pre id="results">No findings yet.</pre>
<script>
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
async function load(){const r=await fetch('/market/api/vibe-research',{credentials:'same-origin'});if(!r.ok)throw new Error('HTTP '+r.status);const d=await r.json(),c=d.counts||{};$('worker').textContent=d.worker_running?'RUNNING':d.enabled?'STARTING':'OFF';$('worker').className='value '+(d.worker_running?'ok':'warn');$('package').textContent=d.package_installed?'v'+d.package_version:'MISSING';$('provider').textContent=d.provider_status;$('provider').className='value '+(d.provider_status==='READY'?'ok':'warn');$('captured').textContent=Object.values(c).reduce((a,b)=>a+Number(b||0),0);$('findings').textContent=(d.recent_findings||[]).length;$('message').textContent=d.provider_status==='READY'?'AI research is active. Closed Gold trades are analyzed after Nautilus certification.':'Replay evidence capture is active. AI analysis will automatically start once an unattended Vibe provider is configured.';$('jobs').innerHTML=(d.recent_jobs||[]).map(j=>`<tr><td>${esc(j.setup_id)}</td><td>${esc(j.status)}</td><td>${esc(j.updated_at)}</td><td>${esc(j.provider||'not configured')}</td><td class="muted">${esc(j.last_error)}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">No closed-trade research jobs yet.</td></tr>';$('results').textContent=JSON.stringify(d.recent_findings||[],null,2)}load().catch(e=>$('message').textContent=e.message);setInterval(()=>load().catch(()=>{}),5000);
</script></main></body></html>"""


def install_vibe_research_routes() -> None:
    from src.dashboard import app as dashboard

    start_vibe_research_worker()
    api_path = f"{dashboard.BASE_PATH}/api/vibe-research"
    page_path = f"{dashboard.BASE_PATH}/vibe-research"
    existing = {getattr(route, "path", None) for route in dashboard.app.routes}

    async def snapshot(request: Request, limit: int = 12):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            raw = vibe_research_snapshot(connection, recent_limit=max(1, min(int(limit), 50)))
            return normalize_vibe_snapshot(raw)
        finally:
            connection.close()

    async def page(request: Request):
        dashboard.require_http_auth(request)
        return HTMLResponse(_page_html())

    if api_path not in existing:
        dashboard.app.add_api_route(api_path, snapshot, methods=["GET"], name="vibe_research_snapshot")
    if page_path not in existing:
        dashboard.app.add_api_route(page_path, page, methods=["GET"], name="vibe_research_page", response_class=HTMLResponse)
