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
    finalDashboardRenderTrades65(realizedTrades);
    renderAttemptAudit65(allTrades);
  };
  realizedOnlyRenderer65.__realizedOnly65 = true;
  renderTrades = realizedOnlyRenderer65;

  if (typeof state !== "undefined" && state?.snapshot) {
    renderTrades(state.snapshot.trades || []);
  }
}

// trading-days.js intentionally replaces renderTrades later in the deferred
// script chain to add trade duration. Install this wrapper only after every
// deferred dashboard script has executed so the WIN/LOSS-only journal remains
// the final renderer instead of being overwritten by the timing layer.
window.addEventListener("DOMContentLoaded", installRealizedTradeHistory65, { once: true });
