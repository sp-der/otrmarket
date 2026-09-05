(() => {
  const labels = {
    accepted: "Accepted",
    session: "Session",
    eval_limit: "Eval limit",
    cooldown: "Cooldown",
    post_loss: "Post-loss",
    risk_exposure: "Risk / exposure",
    risk_reward: "R:R",
    context: "Context",
    entry_geometry: "Entry geometry",
    confirmation: "Confirmation",
    quality_other: "Other quality",
    session_blocked: "Session blocked",
    rr_blocked: "R:R blocked",
    context_blocked: "Context blocked",
    quality_blocked: "Quality blocked",
    entry_geometry_blocked: "Entry geometry",
    arbiter_blocked: "Arbiter blocked",
    guard_blocked: "Account guard",
    risk_rejected: "Risk rejected",
    missed_extended: "Missed / extended",
    expired_entry: "Expired before entry",
    waiting_entry: "Waiting for entry",
  };

  const escapeHtml = (value) => String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

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
          <div class="section-kicker">GOLD EXECUTION CONVERSION · OPERATION 8.1</div>
          <h2>Decision Funnel</h2>
        </div>
        <span id="decisionTelemetryDay" class="tiny-chip">WAITING</span>
      </div>
      <div id="decisionTelemetryGrid"></div>`;
    const marketPanel = overview.querySelector(".market-panel");
    if (marketPanel) overview.insertBefore(panel, marketPanel);
    else overview.prepend(panel);
    return panel;
  }

  function percent(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(1)}%` : "--";
  }

  function stageCard(label, value, sub) {
    return `
      <div style="border:1px solid rgba(255,255,255,.08);border-radius:10px;padding:12px;background:rgba(255,255,255,.018);min-width:0">
        <span class="muted" style="display:block;font-size:11px;letter-spacing:.04em;text-transform:uppercase">${escapeHtml(label)}</span>
        <strong style="display:block;font-size:24px;line-height:1.1;margin-top:4px">${Number(value || 0)}</strong>
        <small style="display:block;margin-top:4px;color:#7f8b96;font-size:10px">${escapeHtml(sub || "")}</small>
      </div>`;
  }

  function renderLegacyReasons(telemetry) {
    const gold = (telemetry?.markets || []).find((item) => String(item.symbol || "").toUpperCase() === "GC");
    if (!gold) return "";
    return (gold.latest_decisions || []).slice(0, 3).map((item) => {
      const time = String(item.created_at || "").replace("T", " ").replace("+00:00", "Z");
      return `<div style="font-size:11px;line-height:1.45;color:#aab2ba;border-top:1px solid rgba(255,255,255,.06);padding-top:7px;margin-top:7px"><strong>${escapeHtml(item.timeframe || "")}</strong> · ${escapeHtml(labels[item.bucket] || item.bucket || "Blocked")}<br><span style="color:#68737d">${escapeHtml(time)}</span><br>${escapeHtml(item.reason || "Blocked after qualification")}</div>`;
    }).join("");
  }

  function render(telemetry, conversion) {
    const panel = ensurePanel();
    if (!panel) return;
    const day = document.getElementById("decisionTelemetryDay");
    const grid = document.getElementById("decisionTelemetryGrid");
    if (!day || !grid) return;

    const scope = conversion?.scope || {};
    const funnel = conversion?.funnel || {};
    const ratios = conversion?.conversion || {};
    const dropoffs = Object.entries(conversion?.dropoffs || {})
      .filter(([key, value]) => key !== "waiting_entry" && Number(value) > 0)
      .sort((a, b) => Number(b[1]) - Number(a[1]));
    const timeframeRows = (conversion?.by_timeframe || []).filter((row) => Number(row.detected || 0) > 0);

    day.textContent = scope?.kind === "session"
      ? `${scope.name || "SESSION"} · ${scope.date || ""}`
      : (scope?.date || telemetry?.trading_day || "NO SETUPS");

    const dropoffHtml = dropoffs.length
      ? dropoffs.map(([key, value]) => `<div style="display:flex;justify-content:space-between;gap:12px;padding:3px 0"><span>${escapeHtml(labels[key] || key.replaceAll("_", " "))}</span><strong>${Number(value)}</strong></div>`).join("")
      : '<div class="muted">No terminal drop-offs in this scope yet.</div>';

    const tfHtml = timeframeRows.length
      ? timeframeRows.map((row) => `
          <div style="display:grid;grid-template-columns:52px repeat(5,minmax(42px,1fr));gap:6px;align-items:center;padding:5px 0;border-top:1px solid rgba(255,255,255,.05);font-size:11px">
            <strong>${escapeHtml(row.label)}</strong>
            <span>${Number(row.detected || 0)} det</span>
            <span>${Number(row.qualified || 0)} qual</span>
            <span>${Number(row.selected || 0)} sel</span>
            <span>${Number(row.registered || 0)} reg</span>
            <span>${Number(row.filled || 0)} fill</span>
          </div>`).join("")
      : '<div class="muted">No timeframe conversion data yet.</div>';

    const latest = (conversion?.latest_dropoffs || []).slice(0, 4).map((item) => `
      <div style="font-size:11px;line-height:1.45;color:#abb4bd;border-top:1px solid rgba(255,255,255,.06);padding-top:7px;margin-top:7px">
        <strong>${escapeHtml(item.timeframe || "")} · ${escapeHtml(String(item.strategy || "").replaceAll("_", " "))}</strong>
        <span class="tiny-chip" style="margin-left:6px">${escapeHtml(labels[item.dropoff] || item.dropoff || "DROP")}</span><br>
        ${escapeHtml(item.reason || item.result || item.trace_status || "Decision ended before fill")}
      </div>`).join("");

    grid.innerHTML = `
      <article style="border:1px solid rgba(255,255,255,.10);border-radius:14px;padding:15px;background:rgba(255,255,255,.018)">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px">
          <div><strong style="font-size:19px">GC · GOLD</strong><div class="muted" style="font-size:11px;margin-top:2px">${escapeHtml(scope.name || "TRADING DAY")} ${escapeHtml(scope.start_et && scope.end_et ? `${scope.start_et}-${scope.end_et} ET` : "")}</div></div>
          <span class="tiny-chip">${Number(funnel.filled || 0)} FILLED</span>
        </div>

        <div style="display:grid;grid-template-columns:repeat(6,minmax(90px,1fr));gap:8px;overflow-x:auto;padding-bottom:2px">
          ${stageCard("Detected", funnel.detected, "raw strategy candidates")}
          ${stageCard("Qualified", funnel.qualified, percent(ratios.detected_to_qualified_pct))}
          ${stageCard("Selected", funnel.selected, percent(ratios.qualified_to_selected_pct))}
          ${stageCard("Registered", funnel.registered, percent(ratios.selected_to_registered_pct))}
          ${stageCard("Filled", funnel.filled, percent(ratios.registered_to_fill_pct))}
          ${stageCard("Closed", funnel.closed, `${Number(funnel.wins || 0)}W · ${Number(funnel.losses || 0)}L`)}
        </div>

        <div style="display:grid;grid-template-columns:minmax(240px,.9fr) minmax(320px,1.4fr);gap:14px;margin-top:14px">
          <div style="border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:11px">
            <div class="section-kicker" style="margin-bottom:7px">WHERE OPPORTUNITIES DIED</div>
            <div style="display:grid;gap:2px;font-size:12px">${dropoffHtml}</div>
            ${latest || renderLegacyReasons(telemetry)}
          </div>
          <div style="border:1px solid rgba(255,255,255,.07);border-radius:10px;padding:11px">
            <div style="display:flex;justify-content:space-between;gap:10px;align-items:center;margin-bottom:5px">
              <div class="section-kicker">CONVERSION BY TIMEFRAME</div>
              <strong style="font-size:12px">Selected → Fill ${percent(ratios.selected_to_fill_pct)}</strong>
            </div>
            ${tfHtml}
          </div>
        </div>
      </article>`;
  }

  async function loadJson(url) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function refresh() {
    try {
      const stamp = Date.now();
      const [snapshot, conversion] = await Promise.all([
        loadJson(`/market/api/snapshot?telemetry=${stamp}`),
        loadJson(`/market/api/otr81/conversion?telemetry=${stamp}`).catch(() => null),
      ]);
      render(snapshot?.decision_telemetry || {}, conversion || {});
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
