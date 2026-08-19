const REALIZED_RESULTS_65 = new Set(["WIN", "LOSS"]);
const MAX_AUDIT_ROWS_65 = 30;

function isRealizedTrade65(trade) {
  return REALIZED_RESULTS_65.has(String(trade?.result || "").toUpperCase());
}

function ensureTradeFilters65() {
  const resultFilter = document.getElementById("tradeResultFilter");
  if (!resultFilter) return;
  if (resultFilter.dataset.realizedOnly65 !== "1") {
    resultFilter.innerHTML = `
      <option value="all">All realized</option>
      <option value="WIN">Wins</option>
      <option value="LOSS">Losses</option>`;
    resultFilter.dataset.realizedOnly65 = "1";
  }
  if (!["all", "WIN", "LOSS"].includes(resultFilter.value)) resultFilter.value = "all";
}

function ensureAttemptAuditPanel65() {
  const setupsView = document.getElementById("setupsView");
  if (!setupsView || document.getElementById("nonExecutedTradesBody65")) return;

  const panel = document.createElement("section");
  panel.className = "panel";
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <div class="section-kicker">EXECUTION AUDIT</div>
        <h2>Missed / Rejected Attempts</h2>
      </div>
      <span class="tiny-chip">LATEST ${MAX_AUDIT_ROWS_65}</span>
    </div>
    <div class="table-wrap tall-table">
      <table>
        <thead>
          <tr><th>Market</th><th>TF</th><th>Direction</th><th>Status</th><th>Entry</th><th>Stop</th><th>Target</th><th>Exit</th><th>Result</th><th>R</th><th>P/L</th><th>Opened</th><th>Closed</th><th>Duration</th></tr>
        </thead>
        <tbody id="nonExecutedTradesBody65"></tbody>
      </table>
    </div>`;
  setupsView.appendChild(panel);
}

function renderAttemptAudit65(trades) {
  ensureAttemptAuditPanel65();
  const body = document.getElementById("nonExecutedTradesBody65");
  if (!body) return;

  const attempts = (trades || [])
    .filter((trade) => !isRealizedTrade65(trade))
    .slice(0, MAX_AUDIT_ROWS_65);

  body.innerHTML = attempts.length
    ? attempts.map((trade) => tradeRow(trade, false)).join("")
    : '<tr><td colspan="14" class="empty-state">No missed or rejected attempts recorded.</td></tr>';
}

function installRealizedTradeHistory65() {
  ensureTradeFilters65();
  ensureAttemptAuditPanel65();

  if (typeof renderTrades !== "function" || renderTrades.__realizedOnly65 === true) return;

  const finalDashboardRenderTrades65 = renderTrades;
  const realizedOnlyRenderer65 = function realizedOnlyRenderer65(trades) {
    ensureTradeFilters65();
    const allTrades = trades || [];
    const realizedTrades = allTrades.filter(isRealizedTrade65);

    // The final dashboard renderer owns BOTH the Overview recent-activity table
    // and the full Trades journal. Feeding only realized rows here guarantees
    // WIN/LOSS-only history in both places, regardless of the timing UI layer.
    finalDashboardRenderTrades65(realizedTrades);
    renderAttemptAudit65(allTrades);
  };
  realizedOnlyRenderer65.__realizedOnly65 = true;
  renderTrades = realizedOnlyRenderer65;

  if (typeof state !== "undefined" && state?.snapshot) {
    renderTrades(state.snapshot.trades || []);
  }
}

function buildRuleRow65(rule) {
  const source = rule.source ? `<small class="muted">${rule.source}</small>` : "";
  return `<div><span>${rule.name}</span><strong>${rule.value}${source}</strong></div>`;
}

async function ensureActiveBuildPanel65() {
  const systemView = document.getElementById("systemView");
  if (!systemView || document.getElementById("activeBuildRules65")) return;

  const panel = document.createElement("section");
  panel.id = "activeBuildRules65";
  panel.className = "panel";
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <div class="section-kicker">LIVE BUILD AUDIT</div>
        <h2>Active Build / Trading Rules</h2>
      </div>
      <span id="activeBuildBadge65" class="tiny-chip">LOADING</span>
    </div>
    <div id="activeBuildMeta65" class="system-list">
      <div><span>Status</span><strong>Loading runtime manifest…</strong></div>
    </div>
    <div class="divider"></div>
    <div class="section-kicker">VERIFIED EXECUTION RULES</div>
    <div id="activeBuildRuleList65" class="system-list"></div>`;
  systemView.insertBefore(panel, systemView.firstChild);

  try {
    const response = await fetch(`/market/assets/runtime-build.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const manifest = await response.json();
    const build = manifest.build || {};
    const rules = manifest.rules || [];

    const badge = document.getElementById("activeBuildBadge65");
    if (badge) badge.textContent = build.operation || "ACTIVE";

    const meta = document.getElementById("activeBuildMeta65");
    if (meta) {
      const shortSha = String(build.commit_sha || "unknown").slice(0, 10);
      meta.innerHTML = `
        <div><span>Engine</span><strong class="mono">${build.engine_module || "unknown"}</strong></div>
        <div><span>Operation</span><strong>${build.operation || "--"}</strong></div>
        <div><span>Deploy commit</span><strong class="mono">${shortSha}</strong></div>
        <div><span>Execution mode</span><strong>${build.execution_mode || "PAPER"}</strong></div>
        <div><span>Manifest generated</span><strong>${build.generated_at ? fmtTime(build.generated_at) : "--"}</strong></div>`;
    }

    const list = document.getElementById("activeBuildRuleList65");
    if (list) list.innerHTML = rules.map(buildRuleRow65).join("");
  } catch (error) {
    const badge = document.getElementById("activeBuildBadge65");
    if (badge) badge.textContent = "MANIFEST ERROR";
    const meta = document.getElementById("activeBuildMeta65");
    if (meta) meta.innerHTML = `<div><span>Status</span><strong>Runtime manifest unavailable: ${String(error)}</strong></div>`;
  }
}

function finalizeDashboard65() {
  installRealizedTradeHistory65();
  ensureActiveBuildPanel65();
}

// Important: trading-days.js installs its own trade renderer from a later
// DOMContentLoaded callback. Running on window.load and then queueing one final
// task guarantees every deferred script and every DOMContentLoaded/load handler
// has finished before OTR locks the final WIN/LOSS-only renderer in place.
if (document.readyState === "complete") {
  window.setTimeout(finalizeDashboard65, 0);
} else {
  window.addEventListener("load", () => window.setTimeout(finalizeDashboard65, 0), { once: true });
}
