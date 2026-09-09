from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from src.research.lab_v01 import (
    closed_gold_trades,
    ensure_research_lab81,
    refresh_research_lab81,
    research_lab_snapshot81,
)
from src.storage.database import get_connection


def _page_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>OTR Research Lab</title>
<style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0;background:#080808;color:#f4f4f4}
main{max-width:1180px;margin:0 auto;padding:28px 18px 64px}
h1{font-size:clamp(28px,5vw,48px);letter-spacing:-.045em;margin:0 0 4px}.sub{color:#929292;line-height:1.5;max-width:850px;margin-bottom:20px}
.badge{display:inline-flex;border:1px solid #2d2d2d;background:#111;border-radius:999px;padding:7px 10px;font-size:12px;color:#aaa;margin:0 8px 8px 0}
.controls{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0 18px}button{background:#f2f2f2;color:#0b0b0b;border:0;border-radius:10px;padding:10px 14px;font:inherit;font-weight:800;cursor:pointer}button.secondary{background:#171717;color:#fff;border:1px solid #303030}
.grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-bottom:22px}.card{border:1px solid #252525;background:#111;border-radius:14px;padding:14px}.label{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#888}.value{font-size:25px;font-weight:850;margin-top:5px}.small{font-size:12px;color:#888;margin-top:3px}
.section{margin-top:25px}h2{font-size:18px;margin:0 0 10px}table{width:100%;border-collapse:collapse;background:#0f0f0f;border:1px solid #252525}th,td{padding:10px;border-bottom:1px solid #242424;text-align:left;vertical-align:top;font-size:13px}th{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#838383}.ok{color:#76e59a}.warn{color:#ffd479}.bad{color:#ff8b8b}.muted{color:#858585}
#status{min-height:20px;color:#9b9b9b;margin-bottom:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0e0e0e;border:1px solid #252525;border-radius:12px;padding:12px}
@media(max-width:900px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:620px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}table{display:block;overflow-x:auto}.value{font-size:21px}}
</style>
</head>
<body><main>
<h1>OTR Research Lab <span class="muted">v0.1</span></h1>
<div class="sub">Evidence layer for the current Gold replay. It can inspect trades, score samples, store research snapshots, and prepare counterfactual geometry. It cannot alter Operation 8.1, place broker orders, or change risk.</div>
<div><span class="badge">GC ONLY</span><span class="badge">NON-AUTHORITATIVE</span><span class="badge">20 SAMPLE EVIDENCE GATE</span><span class="badge">NAUTILUS CERTIFICATION</span></div>
<div class="controls"><button id="capture">Capture Evidence</button><button class="secondary" id="refresh">Refresh</button><a href="/market/nautilus-parity"><button class="secondary" type="button">Nautilus Parity</button></a></div>
<div id="status"></div>
<div class="grid">
<div class="card"><div class="label">Resolved Samples</div><div class="value" id="samples">0</div><div class="small" id="progress">0 / 20</div></div>
<div class="card"><div class="label">W / L</div><div class="value" id="wl">0 / 0</div><div class="small" id="wr">Win rate n/a</div></div>
<div class="card"><div class="label">Net P/L</div><div class="value" id="pnl">$0</div><div class="small">paper replay</div></div>
<div class="card"><div class="label">Expectancy</div><div class="value" id="expr">n/a</div><div class="small">average realized R</div></div>
<div class="card"><div class="label">Max Drawdown</div><div class="value" id="dd">$0</div><div class="small">closed-trade curve</div></div>
<div class="card"><div class="label">Nautilus Path</div><div class="value" id="parity">0 / 0</div><div class="small" id="evidence">NOT EVALUABLE</div></div>
</div>
<div class="section"><h2>Research Hypotheses</h2><table><thead><tr><th>ID</th><th>Question</th><th>Metric</th><th>Status</th></tr></thead><tbody id="hypotheses"></tbody></table></div>
<div class="section"><h2>Recent Closed Gold Trades</h2><table><thead><tr><th>Closed</th><th>Setup</th><th>Grade</th><th>Family</th><th>TF</th><th>Result</th><th>R</th><th>P/L</th><th>Nautilus</th></tr></thead><tbody id="trades"></tbody></table></div>
<div class="section"><h2>Evidence Segments</h2><pre id="segments">Loading...</pre></div>
</main>
<script>
const $=id=>document.getElementById(id);
const money=v=>v==null?'n/a':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(v);
const pct=v=>v==null?'n/a':(100*v).toFixed(1)+'%';
const esc=v=>String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
function paint(d){const b=d.baseline||{};$('samples').textContent=b.samples??0;$('progress').textContent=`${b.samples??0} / ${b.minimum_samples??20}`;$('wl').textContent=`${b.wins??0} / ${b.losses??0}`;$('wr').textContent='Win rate '+pct(b.win_rate);$('pnl').textContent=money(b.net_pnl);$('expr').textContent=b.expectancy_r==null?'n/a':Number(b.expectancy_r).toFixed(2)+'R';$('dd').textContent=money(b.max_drawdown);$('parity').textContent=`${b.parity_path_matches??0} / ${b.parity_samples??0}`;$('evidence').textContent=b.evidence_status||'NOT_EVALUABLE';
$('hypotheses').innerHTML=(d.hypotheses||[]).map(h=>`<tr><td>${esc(h.hypothesis_id)}</td><td><b>${esc(h.title)}</b><div class="muted">${esc(h.question)}</div></td><td>${esc(h.metric)}</td><td>${esc(h.status)}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">No hypotheses.</td></tr>';
$('trades').innerHTML=(d.recent_trades||[]).map(t=>{const c=t.execution_certification==='MATCH'?'ok':t.execution_certification==='DIFF'?'bad':'warn';return `<tr><td>${esc(t.closed_at)}</td><td>${esc(t.setup_id)}</td><td>${esc(t.grade)}</td><td>${esc(t.setup_family)}</td><td>${esc(t.timeframe)}</td><td>${esc(t.result)}</td><td>${t.result_r==null?'n/a':esc(Number(t.result_r).toFixed(2))+'R'}</td><td>${money(t.result_dollars)}</td><td class="${c}">${esc(t.execution_certification)}</td></tr>`}).join('')||'<tr><td colspan="9" class="muted">No closed Gold trades yet.</td></tr>';
const concise={setup_family:d.segments?.setup_family,grade:d.segments?.grade,timeframe:d.segments?.timeframe,regime:d.segments?.regime};$('segments').textContent=JSON.stringify(concise,null,2)}
async function load(){const r=await fetch('/market/api/research-lab',{credentials:'same-origin'});if(!r.ok)throw new Error('Research Lab HTTP '+r.status);paint(await r.json())}
async function capture(){$('capture').disabled=true;$('status').textContent='Capturing current evidence snapshot...';try{const r=await fetch('/market/api/research-lab/refresh',{method:'POST',credentials:'same-origin'});const d=await r.json();if(!r.ok)throw new Error(JSON.stringify(d));$('status').textContent=`Captured ${d.closed_gold_trades} closed Gold trades and ${d.counterfactual_candidates} geometry candidates. No strategy mutations.`;await load()}catch(e){$('status').textContent='Capture failed: '+e.message}finally{$('capture').disabled=false}}
$('capture').onclick=capture;$('refresh').onclick=()=>load().catch(e=>$('status').textContent=e.message);load().catch(e=>$('status').textContent=e.message);
</script></body></html>"""


def install_research_lab_routes() -> None:
    """Register protected, non-authoritative Research Lab surfaces once."""
    from src.dashboard import app as dashboard

    connection = get_connection()
    try:
        ensure_research_lab81(connection)
    finally:
        connection.close()

    root = f"{dashboard.BASE_PATH}/api/research-lab"
    trades_path = f"{root}/trades"
    refresh_path = f"{root}/refresh"
    page_path = f"{dashboard.BASE_PATH}/research-lab"
    existing = {getattr(route, "path", None) for route in dashboard.app.routes}

    async def snapshot(request: Request, limit: int = 25):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            return research_lab_snapshot81(connection, recent_limit=max(1, min(int(limit), 100)))
        finally:
            connection.close()

    async def trades(request: Request, limit: int = 10):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            return {
                "authoritative": False,
                "strategy_mutation_allowed": False,
                "recent": closed_gold_trades(connection, limit=max(1, min(int(limit), 100))),
            }
        finally:
            connection.close()

    async def refresh(request: Request):
        dashboard.require_http_auth(request)
        connection = get_connection()
        try:
            return refresh_research_lab81(connection)
        finally:
            connection.close()

    async def page(request: Request):
        dashboard.require_http_auth(request)
        return HTMLResponse(_page_html())

    if root not in existing:
        dashboard.app.add_api_route(root, snapshot, methods=["GET"], name="research_lab_v01_snapshot")
    if trades_path not in existing:
        dashboard.app.add_api_route(trades_path, trades, methods=["GET"], name="research_lab_v01_trades")
    if refresh_path not in existing:
        dashboard.app.add_api_route(refresh_path, refresh, methods=["POST"], name="research_lab_v01_refresh")
    if page_path not in existing:
        dashboard.app.add_api_route(page_path, page, methods=["GET"], name="research_lab_v01_page", response_class=HTMLResponse)
