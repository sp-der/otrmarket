(() => {
  const API = "/market/api/intelligence";
  let timer = null;

  function num(value, digits = 2) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits) : "--";
  }

  function pct(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(1)}%` : "--";
  }

  function money(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "--";
    const body = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return `${n < 0 ? "-" : n > 0 ? "+" : ""}$${body}`;
  }

  function duration(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds)) return "--";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.round(seconds % 60);
    return hours ? `${hours}h ${minutes}m ${secs}s` : `${minutes}m ${secs}s`;
  }

  function ensureStyles() {
    if (document.getElementById("otrIntelligenceStyles")) return;
    const style = document.createElement("style");
    style.id = "otrIntelligenceStyles";
    style.textContent = `
      .otr-intel-panel{margin-top:24px;border:1px solid rgba(255,255,255,.13);border-radius:26px;background:#090909;padding:24px;overflow:hidden}
      .otr-intel-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:18px}
      .otr-intel-head h2{margin:5px 0 0;font-size:27px}.otr-intel-chip{border:1px solid rgba(255,255,255,.18);border-radius:999px;padding:8px 12px;font-size:11px;letter-spacing:.12em;font-weight:800;color:#d8d8d8;white-space:nowrap}
      .otr-intel-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.otr-intel-card{border:1px solid rgba(255,255,255,.1);border-radius:20px;padding:18px;background:#0e0e0e}
      .otr-intel-card h3{margin:0 0 14px;font-size:17px}.otr-intel-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.otr-intel-stat span{display:block;color:#777;font-size:10px;text-transform:uppercase;letter-spacing:.12em;margin-bottom:5px}.otr-intel-stat strong{font-size:17px}
      .otr-intel-edge{margin-top:14px;border:1px solid rgba(255,255,255,.09);border-radius:17px;padding:14px 16px;color:#aaa;font-size:13px;line-height:1.55}.otr-intel-edge strong{color:#fff}
      .otr-forensics{margin-top:18px}.otr-forensics h3{font-size:16px;margin:0 0 10px}.otr-forensics-wrap{overflow:auto;border:1px solid rgba(255,255,255,.09);border-radius:16px}.otr-forensics table{width:100%;border-collapse:collapse;min-width:720px}.otr-forensics th,.otr-forensics td{padding:12px 14px;text-align:left;border-bottom:1px solid rgba(255,255,255,.07);font-size:12px}.otr-forensics th{color:#777;text-transform:uppercase;letter-spacing:.1em;font-size:9px}.otr-forensics tr:last-child td{border-bottom:0}.otr-class{font-weight:800;letter-spacing:.06em}.otr-class.INSTANT_STOP,.otr-class.EARLY_STOP{color:#ff7777}.otr-class.GAVE_BACK_EDGE{color:#f5c06a}.otr-class.WIN{color:#72d79a}
      @media(max-width:800px){.otr-intel-panel{padding:18px;border-radius:20px}.otr-intel-grid{grid-template-columns:1fr}.otr-intel-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.otr-intel-head{align-items:center}.otr-intel-head h2{font-size:23px}}
    `;
    document.head.appendChild(style);
  }

  function ensurePanel() {
    if (document.getElementById("otrTradeIntelligence")) return true;
    const anchor = document.getElementById("lucidEvalWrap") || document.querySelector("#overviewView .prop-guard-panel");
    if (!anchor) return false;
    anchor.insertAdjacentHTML("afterend", `
      <section id="otrTradeIntelligence" class="otr-intel-panel">
        <div class="otr-intel-head">
          <div><div class="section-kicker">OPERATION 5.3</div><h2>Trade Intelligence</h2></div>
          <span class="otr-intel-chip">LIVE 5.3 vs 4.8 SHADOW</span>
        </div>
        <div id="otrIntelBody"><div class="empty-state">Collecting trade fingerprints and excursion data...</div></div>
      </section>
    `);
    return true;
  }

  function statsCard(title, s, shadow = false) {
    return `
      <article class="otr-intel-card">
        <h3>${title}</h3>
        <div class="otr-intel-stats">
          <div class="otr-intel-stat"><span>Closed</span><strong>${Number(s?.closed || 0)}</strong></div>
          <div class="otr-intel-stat"><span>Win rate</span><strong>${pct(s?.win_rate)}</strong></div>
          <div class="otr-intel-stat"><span>Total R</span><strong>${num(s?.total_r)}R</strong></div>
          <div class="otr-intel-stat"><span>${shadow ? "Model P/L" : "P/L"}</span><strong>${money(s?.total_dollars)}</strong></div>
          <div class="otr-intel-stat"><span>Avg MFE</span><strong>${num(s?.avg_mfe_r)}R</strong></div>
          <div class="otr-intel-stat"><span>Avg MAE</span><strong>${num(s?.avg_mae_r)}R</strong></div>
          <div class="otr-intel-stat"><span>Instant stops</span><strong>${Number(s?.instant_stops || 0)}</strong></div>
          <div class="otr-intel-stat"><span>Early stops</span><strong>${Number(s?.early_stops || 0)}</strong></div>
          <div class="otr-intel-stat"><span>Gave back edge</span><strong>${Number(s?.gave_back_edge || 0)}</strong></div>
        </div>
      </article>`;
  }

  function edgeRead(live, shadow) {
    if (!live?.closed && !shadow?.closed) {
      return "Waiting for closed trades. MFE shows how far a trade moved in our favor before exit; MAE shows how far it moved against us.";
    }
    if (!live?.closed) return "The 4.8 shadow has outcomes, but the live A/A+ engine has not closed a trade yet. Keep collecting before changing rules.";
    if (!shadow?.closed) return "Live 5.3 has outcomes, but the 4.8 shadow has not closed enough comparable candidates yet.";
    const liveAvg = Number(live.total_r || 0) / Math.max(1, Number(live.closed || 0));
    const shadowAvg = Number(shadow.total_r || 0) / Math.max(1, Number(shadow.closed || 0));
    if (liveAvg > shadowAvg + 0.25) return "So far the filtered live engine is producing more R per closed trade than the 4.8 shadow. That suggests the newer filters are removing enough bad candidates to justify their selectivity.";
    if (shadowAvg > liveAvg + 0.25) return "So far the 4.8 shadow is producing more R per closed trade. That is a signal to inspect which 5.x filters are rejecting winners, not a reason to blindly loosen everything.";
    return "Live and shadow expectancy are currently close. The loss-forensics rows below should be more useful than total P/L until the sample grows.";
  }

  function forensics(rows) {
    const losses = (rows || []).filter((r) => r.result === "LOSS").slice(0, 8);
    if (!losses.length) return '<div class="empty-state">No closed live losses to diagnose yet.</div>';
    return `
      <div class="otr-forensics-wrap"><table>
        <thead><tr><th>Market</th><th>TF</th><th>Entry</th><th>Duration</th><th>MFE</th><th>MAE</th><th>Diagnosis</th></tr></thead>
        <tbody>${losses.map((r) => `
          <tr>
            <td>${r.symbol}</td><td>${r.timeframe}</td><td>${String(r.entry_type || "--").replaceAll("_", " ")}</td>
            <td>${duration(r.duration_seconds)}</td><td>${num(r.mfe_r)}R</td><td>${num(r.mae_r)}R</td>
            <td><span class="otr-class ${r.outcome_class || ""}">${String(r.outcome_class || "UNCLASSIFIED").replaceAll("_", " ")}</span></td>
          </tr>`).join("")}</tbody>
      </table></div>`;
  }

  function render(data) {
    ensureStyles();
    if (!ensurePanel()) return;
    const live = data?.live || {};
    const shadow = data?.shadow_48 || {};
    const root = document.getElementById("otrIntelBody");
    if (!root) return;
    root.innerHTML = `
      <div class="otr-intel-grid">
        ${statsCard("Live A/A+ Engine", live, false)}
        ${statsCard("Operation 4.8 Shadow", shadow, true)}
      </div>
      <div class="otr-intel-edge"><strong>Current read:</strong> ${edgeRead(live, shadow)}</div>
      <div class="otr-forensics"><h3>Loss Forensics</h3>${forensics(data?.recent_live || [])}</div>
    `;
  }

  async function poll() {
    try {
      const response = await fetch(API, { credentials: "same-origin", cache: "no-store" });
      if (response.ok) render(await response.json());
    } catch (_) {
      // Primary dashboard connection UI remains the source of truth.
    } finally {
      timer = window.setTimeout(poll, 2000);
    }
  }

  function start() {
    ensureStyles();
    ensurePanel();
    if (timer) window.clearTimeout(timer);
    poll();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
