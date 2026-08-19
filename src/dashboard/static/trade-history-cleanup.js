const REALIZED_RESULTS_65 = new Set(["WIN", "LOSS"]);
const NON_EXECUTED_SETUP_STATUSES_65 = new Set([
  "MISSED_EXTENDED",
  "QUALITY_BLOCKED",
  "SESSION_BLOCKED",
  "GUARD_BLOCKED",
  "RISK_REJECTED",
  "INVALIDATED",
  "EXPIRED",
  "CANCELLED",
  "INVALIDATED_BEFORE_ENTRY",
]);

function isRealizedTrade65(trade) {
  return REALIZED_RESULTS_65.has(String(trade?.result || "").toUpperCase());
}

function isNonExecutedSetup65(setup) {
  return NON_EXECUTED_SETUP_STATUSES_65.has(String(setup?.status || "").toUpperCase());
}

function syncHtml65(root, html) {
  if (root && root.innerHTML !== html) root.innerHTML = html;
}

const resultFilter65 = $("tradeResultFilter");
if (resultFilter65) {
  resultFilter65.innerHTML = `
    <option value="all">All realized</option>
    <option value="WIN">Wins</option>
    <option value="LOSS">Losses</option>`;
}

function ensureNonExecutedTradePanel65() {
  const tradesView = $("tradesView");
  if (!tradesView || $("nonExecutedTradesBody65")) return;

  const panel = document.createElement("section");
  panel.className = "panel";
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <div class="section-kicker">EXECUTION AUDIT</div>
        <h2>Missed / Rejected Attempts</h2>
      </div>
      <span class="tiny-chip">NO REALIZED P/L</span>
    </div>
    <div class="table-wrap tall-table">
      <table>
        <thead>
          <tr><th>Market</th><th>TF</th><th>Direction</th><th>Status</th><th>Entry</th><th>Stop</th><th>Target</th><th>Exit</th><th>Result</th><th>R</th><th>P/L</th><th>Opened</th><th>Closed</th></tr>
        </thead>
        <tbody id="nonExecutedTradesBody65"></tbody>
      </table>
    </div>`;
  tradesView.appendChild(panel);
}

function enforceRealizedTradeHistory65() {
  const trades = state?.snapshot?.trades || [];
  const symbolFilter = $("tradeSymbolFilter")?.value || "all";
  const resultFilter = $("tradeResultFilter")?.value || "all";

  const realized = trades.filter(isRealizedTrade65);
  const recent = realized.slice(0, 8);
  syncHtml65(
    $("overviewTradesBody"),
    recent.length
      ? recent.map((t) => tradeRow(t, true)).join("")
      : '<tr><td colspan="10" class="empty-state">No realized paper trades yet.</td></tr>',
  );

  const visibleRealized = realized.filter((t) => {
    const symbolOk = symbolFilter === "all" || t.symbol === symbolFilter;
    const marker = String(t.result || "").toUpperCase();
    const resultOk = resultFilter === "all" || marker === resultFilter;
    return symbolOk && resultOk;
  });
  syncHtml65(
    $("tradesBody"),
    visibleRealized.length
      ? visibleRealized.map((t) => tradeRow(t, false)).join("")
      : '<tr><td colspan="13" class="empty-state">No realized trades match these filters.</td></tr>',
  );

  ensureNonExecutedTradePanel65();
  const nonExecuted = trades.filter((t) => {
    const symbolOk = symbolFilter === "all" || t.symbol === symbolFilter;
    return symbolOk && !isRealizedTrade65(t);
  });
  syncHtml65(
    $("nonExecutedTradesBody65"),
    nonExecuted.length
      ? nonExecuted.map((t) => tradeRow(t, false)).join("")
      : '<tr><td colspan="13" class="empty-state">No missed or rejected attempts for this market.</td></tr>',
  );
}

const setupsView65 = $("setupsView");
if (setupsView65 && !$("missedSetupCards")) {
  const primaryPanel = setupsView65.querySelector(".panel");
  const primaryTitle = primaryPanel?.querySelector("h2");
  if (primaryTitle) primaryTitle.textContent = "Active / Executable Setups";

  const auditPanel = document.createElement("section");
  auditPanel.className = "panel";
  auditPanel.innerHTML = `
    <div class="panel-head">
      <div>
        <div class="section-kicker">EXECUTION AUDIT</div>
        <h2>Missed / Rejected Setups</h2>
      </div>
      <span class="tiny-chip">NO REALIZED P/L</span>
    </div>
    <div id="missedSetupCards" class="setup-grid"></div>`;
  setupsView65.appendChild(auditPanel);
}

function enforceSetupSplit65() {
  const all = state?.snapshot?.setups || [];
  const symbolFilter = $("setupSymbolFilter")?.value || "all";
  const triggerFilter = $("setupTriggerFilter")?.value || "all";
  const filtered = all.filter((s) => {
    const symbolOk = symbolFilter === "all" || s.symbol === symbolFilter;
    const triggerOk = triggerFilter === "all" || s.trigger_type === triggerFilter;
    return symbolOk && triggerOk;
  });

  const active = filtered.filter((s) => !isNonExecutedSetup65(s));
  const rejected = filtered.filter(isNonExecutedSetup65);
  syncHtml65(
    $("setupCards"),
    active.length
      ? active.map(setupCard).join("")
      : '<div class="empty-state">No active or executable setups match these filters.</div>',
  );
  syncHtml65(
    $("missedSetupCards"),
    rejected.length
      ? rejected.map(setupCard).join("")
      : '<div class="empty-state">No missed or rejected setups match these filters.</div>',
  );
}

let scheduled65 = false;
function scheduleCleanup65() {
  if (scheduled65) return;
  scheduled65 = true;
  queueMicrotask(() => {
    scheduled65 = false;
    enforceRealizedTradeHistory65();
    enforceSetupSplit65();
  });
}

for (const id of ["overviewTradesBody", "tradesBody", "setupCards"]) {
  const root = $(id);
  if (root) new MutationObserver(scheduleCleanup65).observe(root, { childList: true });
}

for (const id of ["tradeSymbolFilter", "tradeResultFilter", "setupSymbolFilter", "setupTriggerFilter"]) {
  $(id)?.addEventListener("change", scheduleCleanup65);
}

scheduleCleanup65();
