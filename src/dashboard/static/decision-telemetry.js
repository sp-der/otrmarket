(() => {
  const labels = {
    accepted: "Accepted",
    session: "Session",
    cooldown: "Cooldown",
    post_loss: "Post-loss",
    risk_exposure: "Risk / exposure",
    risk_reward: "R:R",
    context: "Context",
    entry_geometry: "Entry geometry",
    confirmation: "Confirmation",
    quality_other: "Other quality",
  };

  function ensurePanel() {
    let panel = document.getElementById("decisionTelemetryPanel");
    if (panel) return panel;
    const overview = document.getElementById("overviewView");
    if (!overview) return null;
    panel = document.createElement("section");
    panel.id = "decisionTelemetryPanel";
    panel.className = "panel";
    panel.innerHTML = `
      <div class="panel-head">
        <div>
          <div class="section-kicker">WHY WE DIDN'T TRADE</div>
          <h2>Decision Funnel</h2>
        </div>
        <span id="decisionTelemetryDay" class="tiny-chip">WAITING</span>
      </div>
      <div id="decisionTelemetryGrid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px"></div>`;
    const marketPanel = overview.querySelector(".market-panel");
    if (marketPanel) overview.insertBefore(panel, marketPanel);
    else overview.prepend(panel);
    return panel;
  }

  function render(data) {
    const panel = ensurePanel();
    if (!panel) return;
    const day = document.getElementById("decisionTelemetryDay");
    const grid = document.getElementById("decisionTelemetryGrid");
    if (!day || !grid) return;
    day.textContent = data?.trading_day || "NO SETUPS";
    const markets = data?.markets || [];
    grid.innerHTML = markets.map((m) => {
      const buckets = Object.entries(m.buckets || {})
        .filter(([key]) => key !== "accepted")
        .sort((a, b) => Number(b[1]) - Number(a[1]));
      const blockers = buckets.length
        ? buckets.map(([key, value]) => `<div style="display:flex;justify-content:space-between;gap:10px"><span>${labels[key] || key}</span><strong>${value}</strong></div>`).join("")
        : '<div class="muted">No rejection buckets yet.</div>';
      const latest = (m.latest_reasons || []).slice(0, 2).map((reason) => `<div style="font-size:11px;line-height:1.35;color:#9a9a9a;margin-top:6px">${escapeHtml(reason)}</div>`).join("");
      return `
        <article style="border:1px solid rgba(255,255,255,.09);border-radius:12px;padding:14px;background:rgba(255,255,255,.02)">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <strong style="font-size:18px">${m.symbol}</strong>
            <span class="tiny-chip">${m.accepted || 0} ACCEPTED</span>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px">
            <div><span class="muted" style="display:block;font-size:11px">Candidates</span><strong style="font-size:22px">${m.candidates || 0}</strong></div>
            <div><span class="muted" style="display:block;font-size:11px">Blocked</span><strong style="font-size:22px">${m.blocked || 0}</strong></div>
          </div>
          <div style="display:grid;gap:5px;font-size:12px">${blockers}</div>
          ${latest}
        </article>`;
    }).join("") || '<div class="empty-state">No decision telemetry yet.</div>';
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function refresh() {
    try {
      const response = await fetch(`/market/api/snapshot?telemetry=${Date.now()}`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) return;
      const snapshot = await response.json();
      render(snapshot.decision_telemetry || {});
    } catch (_) {
      // Main dashboard owns connection status; this panel quietly retries.
    }
  }

  function boot() {
    ensurePanel();
    refresh();
    setInterval(refresh, 5000);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
