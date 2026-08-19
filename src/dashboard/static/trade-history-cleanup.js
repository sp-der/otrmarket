const REALIZED_RESULTS_65 = new Set(["WIN", "LOSS"]);
const MAX_AUDIT_ROWS_65 = 30;

function isRealizedTrade65(trade) {
  return REALIZED_RESULTS_65.has(String(trade?.result || "").toUpperCase());
}

function ensureTradeFilters65() {
  const resultFilter = document.getElementById("tradeResultFilter");
  if (!resultFilter || resultFilter.dataset.realizedOnly65 === "1") return;
  resultFilter.innerHTML = `
    <option value="all">All realized</option>
    <option value="WIN">Wins</option>
    <option value="LOSS">Losses</option>`;
  resultFilter.dataset.realizedOnly65 = "1";
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
          <tr><th>Market</th><th>TF</th><th>Direction</th><th>Status</th><th>Entry</th><th>Stop</th><th>Target</th><th>Exit</th><th>Result</th><th>R</th><th>P/L</th><th>Opened</th><th>Closed</th></tr>
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

  const html = attempts.length
    ? attempts.map((trade) => tradeRow(trade, false)).join("")
    : '<tr><td colspan="13" class="empty-state">No missed or rejected attempts recorded.</td></tr>';

  if (body.innerHTML !== html) body.innerHTML = html;
}

ensureTradeFilters65();
ensureAttemptAuditPanel65();

// Operation 6.5.2: lightweight hook only. No MutationObserver and no full-history
// duplicate table. The base dashboard renderer receives only realized WIN/LOSS
// rows while the audit view is capped to the latest 30 non-executed attempts.
if (typeof renderTrades === "function") {
  const renderTradesBase65 = renderTrades;
  renderTrades = function renderTradesRealizedOnly65(trades) {
    ensureTradeFilters65();
    const allTrades = trades || [];
    renderTradesBase65(allTrades.filter(isRealizedTrade65));
    renderAttemptAudit65(allTrades);
  };
}
