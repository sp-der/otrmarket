/* Operation 6.4 dashboard decision sync.
 *
 * A diagnostic can legitimately remain at SETUP_READY because that row records
 * completed scanner sequence state. The matching strategy_setup row records the
 * execution-quality decision that happened immediately afterward. This layer
 * joins the two by setup_id so the live card shows the final decision instead
 * of looking like an approved trade is still waiting to fire.
 */

window.otrScannerDecisionBySetupId = new Map();

function scannerDecisionForDiagnostic(d) {
  const setupId = String(d?.setup_id || "");
  if (!setupId) return null;
  return window.otrScannerDecisionBySetupId.get(setupId) || null;
}

function scannerDecisionLabel(status) {
  const labels = {
    REGISTERED: "TRADE ARMED",
    QUALITY_BLOCKED: "QUALITY BLOCKED",
    SESSION_BLOCKED: "SESSION BLOCKED",
    GUARD_BLOCKED: "GUARD BLOCKED",
    RISK_REJECTED: "RISK REJECTED",
    MISSED_EXTENDED: "MISSED / EXTENDED",
    INVALIDATED: "INVALIDATED",
  };
  return labels[status] || String(status || "DECIDED").replaceAll("_", " ");
}

function scannerExecutionTier(setup, status) {
  const metadata = setup?.metadata || {};
  const rawTier =
    metadata?.execution_tier ||
    metadata?.session_tier ||
    metadata?.a_plus_context?.quality_grade ||
    null;

  if (status === "REGISTERED") {
    return rawTier ? String(rawTier).replaceAll("_", " ") : "Approved for execution";
  }
  return "No execution";
}

const scannerClarityStateBeforeDecisionSync = scannerClarityState;
scannerClarityState = function scannerClarityStateDecisionSync(d) {
  const base = scannerClarityStateBeforeDecisionSync(d);
  if (String(d?.stage || "") !== "SETUP_READY") return base;

  const setup = scannerDecisionForDiagnostic(d);
  if (!setup) return base;

  const status = setupDecisionStatus(setup);
  const reason = setupDecisionReason(setup);
  const label = scannerDecisionLabel(status);

  if (status === "REGISTERED") {
    return {
      short: "Quality passed · trade armed",
      current: "Pending entry",
      tier: scannerExecutionTier(setup, status),
      window: "Execution active",
      detail: reason,
    };
  }

  if (status === "MISSED_EXTENDED") {
    return {
      short: "Setup complete · move already extended",
      current: "Missed / extended",
      tier: "No chase · continuation watch",
      window: "Decision complete",
      detail: reason,
    };
  }

  return {
    short: `Setup complete · ${label.toLowerCase()}`,
    current: label,
    tier: scannerExecutionTier(setup, status),
    window: "Decision complete",
    detail: reason,
  };
};

const scannerTimeframeCardBeforeDecisionSync = scannerTimeframeCard;
scannerTimeframeCard = function scannerTimeframeCardDecisionSync(d, symbol, timeframe, runtime) {
  let card = scannerTimeframeCardBeforeDecisionSync(d, symbol, timeframe, runtime);
  if (!d || String(d.stage || "") !== "SETUP_READY") return card;

  const setup = scannerDecisionForDiagnostic(d);
  if (!setup) return card;

  const status = setupDecisionStatus(setup);
  const label = scannerDecisionLabel(status);
  const cardClass = status === "REGISTERED" ? "is-execution-armed" : "is-decision-blocked";

  card = card.replace(
    'class="scanner-timeframe-card is-ready"',
    `class="scanner-timeframe-card ${cardClass}"`
  );
  card = card.replace(
    '<span class="scanner-stage-chip">SETUP READY</span>',
    `<span class="scanner-stage-chip scanner-decision-chip ${cardClass}">${label}</span>`
  );
  return card;
};

const renderSetupsBeforeDecisionSync = renderSetups;
renderSetups = function renderSetupsDecisionSync(setups) {
  window.otrScannerDecisionBySetupId = new Map(
    (setups || [])
      .filter((setup) => setup?.setup_id)
      .map((setup) => [String(setup.setup_id), setup])
  );
  return renderSetupsBeforeDecisionSync(setups);
};

(function installScannerDecisionSyncStyles() {
  const style = document.createElement("style");
  style.textContent = `
    .scanner-timeframe-card.is-decision-blocked {
      opacity: .82;
      border-color: rgba(255,255,255,.10);
    }
    .scanner-timeframe-card.is-execution-armed {
      border-color: rgba(255,255,255,.24);
    }
    .scanner-decision-chip {
      max-width: 170px;
      white-space: normal;
      text-align: center;
      line-height: 1.2;
    }
  `;
  document.head.appendChild(style);
})();

/* Operation 7.2I: Gold focus + plain-language scanner.
 *
 * NQ/ES remain available to the market bridge and historical database, but the
 * active dashboard is intentionally narrowed to GC while the strategy is being
 * evaluated on Gold. Scanner cards keep the useful six-step rail, remove the
 * extra diagnostic grid, and translate engine reasons into short human text.
 */
(function installGoldFocus72I() {
  const GOLD = "GC";

  if (typeof activeFuturesSymbols !== "undefined") {
    activeFuturesSymbols.clear();
    activeFuturesSymbols.add(GOLD);
  }
  if (typeof scannerMarketOrder !== "undefined" && Array.isArray(scannerMarketOrder)) {
    scannerMarketOrder.splice(0, scannerMarketOrder.length, GOLD);
  }

  function cleanTrigger(value) {
    const raw = String(value || "").toUpperCase();
    const map = {
      LIQUIDITY_SWEEP: "Liquidity sweep",
      SMT: "SMT confirmation",
      SMT_DIVERGENCE: "SMT confirmation",
      MSS: "Market shift",
      MSS_REVERSAL: "Reversal signal",
      REJECTION_BLOCK: "Rejection block",
      CONTINUATION: "Continuation",
    };
    return map[raw] || (raw ? raw.replaceAll("_", " ").toLowerCase().replace(/^./, (c) => c.toUpperCase()) : "--");
  }

  function humanDecisionWhy(rawReason, status) {
    const raw = String(rawReason || "").toLowerCase();
    const code = String(status || "").toUpperCase();

    if (code === "REGISTERED") {
      return "All checks passed. The bot is waiting for the planned Gold entry.";
    }
    if (code === "MISSED_EXTENDED" || raw.includes("extended") || raw.includes("no chase") || raw.includes("too far")) {
      return "Price moved too far before a safe entry, so the bot refused to chase it.";
    }
    if (raw.includes("after a loss") || raw.includes("post-loss") || raw.includes("post loss")) {
      return "After the last loss, this setup needed stronger confirmation before risking another trade.";
    }
    if (raw.includes("cooldown") || raw.includes("reset window")) {
      return "The setup arrived during the reset period after the last trade, so the bot waited.";
    }
    if (raw.includes("daily stop") || raw.includes("max loss") || raw.includes("mll") || raw.includes("trade limit") || raw.includes("session slot") || raw.includes("evaluation")) {
      return "The setup was skipped because the evaluation risk limit was already reached.";
    }
    if (raw.includes("risk/reward") || raw.includes("risk reward") || raw.includes("r:r") || raw.includes("geometry") || raw.includes("stop") && raw.includes("target")) {
      return "The entry, stop, and target did not offer a good enough reward for the risk.";
    }
    if (raw.includes("reversal") && (raw.includes("confirm") || raw.includes("higher") || raw.includes("15m") || raw.includes("30m"))) {
      return "The short-term reversal did not have enough confirmation from the larger charts.";
    }
    if (raw.includes("session") || code === "SESSION_BLOCKED") {
      return "The setup formed outside the trading conditions allowed for this session.";
    }
    if (raw.includes("quality") || raw.includes("grade") || code === "QUALITY_BLOCKED") {
      return "The setup completed, but its overall quality was not strong enough to risk a trade.";
    }
    if (code === "GUARD_BLOCKED") {
      return "The setup was valid, but the account risk guard would not allow another trade.";
    }
    if (code === "RISK_REJECTED") {
      return "The setup formed, but the stop and target did not create a safe trade.";
    }
    if (code === "INVALIDATED") {
      return "Price broke the setup before entry, so the bot canceled it.";
    }
    return "The setup finished, but one of the final safety checks kept it from trading.";
  }

  function goldScannerModel(d) {
    const stage = String(d?.stage || "WAITING");
    const setup = scannerDecisionForDiagnostic(d);
    const status = setup ? setupDecisionStatus(setup) : null;
    const rawReason = setup ? setupDecisionReason(setup) : String(d?.note || "");

    if (stage === "SETUP_READY" && setup) {
      return {
        title: status === "REGISTERED" ? "Trade armed" : scannerDecisionLabel(status).toLowerCase().replace(/^./, (c) => c.toUpperCase()),
        chip: scannerDecisionLabel(status),
        why: humanDecisionWhy(rawReason, status),
      };
    }

    const states = {
      WARMUP: {
        title: "Warming up",
        chip: "WARMUP",
        why: "The bot needs more Gold candles before it can judge a setup.",
      },
      WAIT_PD_ARRAY: {
        title: "Waiting for a key zone",
        chip: "WAITING",
        why: "Gold has not reached an area where the bot wants to look for an entry yet.",
      },
      WAIT_SIGNAL: {
        title: "Waiting for confirmation",
        chip: "1 / 6+",
        why: "Price reached a key zone. The bot now wants a clear sweep or confirmation signal.",
      },
      WAIT_DISPLACEMENT: {
        title: "Waiting for a strong move",
        chip: "CONFIRMING",
        why: "A signal appeared, but the bot still needs a strong move to confirm direction.",
      },
      WAIT_ENTRY_FVG: {
        title: "Waiting for an entry pullback",
        chip: "ENTRY WATCH",
        why: "Direction is confirmed. The bot is waiting for a clean pullback area before entering.",
      },
      WAIT_QUALIFYING_FVG: {
        title: "Pullback outside entry zone",
        chip: "NO ENTRY",
        why: "A pullback formed, but price is outside the preferred entry area, so the bot is waiting.",
      },
      WAIT_VALID_RR: {
        title: "Risk/reward not ready",
        chip: "CHECKING R:R",
        why: "The setup is close, but the stop and target do not offer a good enough trade yet.",
      },
      SETUP_READY: {
        title: "Setup complete",
        chip: "FINAL CHECK",
        why: "The full setup formed and is going through the final quality and risk checks.",
      },
      EXPIRED: {
        title: "Setup expired",
        chip: "RESET",
        why: "The setup did not finish in time. The bot reset and is waiting for a new one.",
      },
    };
    return states[stage] || {
      title: "Watching Gold",
      chip: "WAITING",
      why: "The bot is watching for the next clean Gold setup.",
    };
  }

  scannerTimeframeCard = function scannerTimeframeCardGold72I(d, symbol, timeframe, runtime) {
    if (!d) {
      return `
        <article class="scanner-timeframe-card is-idle gold-scanner-card">
          <div class="scanner-tf-top">
            <div class="scanner-tf-name">${timeframe}</div>
            <span class="scanner-stage-chip">WAITING</span>
          </div>
          <div class="scanner-primary-state">Waiting for Gold candles</div>
          ${scannerProgress(null)}
          <div class="gold-scanner-why"><span>WHY</span><strong>The bot needs fresh candles on this timeframe before it can judge a setup.</strong></div>
        </article>`;
    }

    const model = goldScannerModel(d);
    const direction = d.direction ? String(d.direction).toUpperCase() : "--";
    const score = Number(d.score || 0);
    const trigger = cleanTrigger(d.trigger_type);
    const stage = String(d.stage || "WAITING");
    const setup = scannerDecisionForDiagnostic(d);
    const status = setup ? setupDecisionStatus(setup) : null;
    const cardClass = status === "REGISTERED" ? "is-execution-armed" : (stage === "SETUP_READY" && status ? "is-decision-blocked" : score >= 3 ? "is-advanced" : "");

    return `
      <article class="scanner-timeframe-card gold-scanner-card ${cardClass}">
        <div class="scanner-tf-top">
          <div class="scanner-tf-name">${timeframe}</div>
          <span class="scanner-stage-chip">${model.chip}</span>
        </div>
        <div class="scanner-state-row gold-scanner-state">
          <div>
            <div class="scanner-direction">${direction}</div>
            <div class="scanner-primary-state">${model.title}</div>
          </div>
          <div class="scanner-score-block"><strong>${score}/6</strong><span>${fmtTime(d.market_time)}</span></div>
        </div>
        ${scannerProgress(d)}
        <div class="gold-scanner-why"><span>WHY</span><strong>${model.why}</strong></div>
        ${trigger !== "--" ? `<div class="gold-scanner-trigger"><span>Signal</span><strong>${trigger}</strong></div>` : ""}
      </article>`;
  };

  scannerPreviewCard = function scannerPreviewCardGold72I(d) {
    const model = goldScannerModel(d);
    const direction = d.direction ? String(d.direction).toUpperCase() : "--";
    return `
      <article class="scanner-card scanner-preview-card gold-scanner-preview">
        <div class="scanner-card-head">
          <div>
            <div class="scanner-title">Gold Futures · ${d.timeframe}</div>
            <div class="scanner-meta">${direction} · ${model.title}</div>
          </div>
          <span class="score-chip">${Number(d.score || 0)}/6</span>
        </div>
        ${scannerProgress(d)}
        <div class="gold-scanner-why compact"><span>WHY</span><strong>${model.why}</strong></div>
      </article>`;
  };

  scannerMarketSection = function scannerMarketSectionGold72I(symbol, diagnostics, runtime, timeframeFilter) {
    if (symbol !== GOLD) return "";
    const byTimeframe = new Map((diagnostics || []).filter((d) => d.symbol === GOLD).map((d) => [d.timeframe, d]));
    const visibleTimeframes = scannerTimeframeOrder.filter((tf) => timeframeFilter === "all" || timeframeFilter === tf);
    const candidates = visibleTimeframes.map((tf) => byTimeframe.get(tf)).filter(Boolean);
    const best = [...candidates].sort((a, b) => Number(b.score || 0) - Number(a.score || 0))[0];
    const summary = best ? `${Number(best.score || 0)}/6 · ${goldScannerModel(best).title}` : "Waiting for Gold scanner history";
    const modeLabel = runtime?.mode === "REPLAY" ? "GOLD REPLAY" : "GOLD ACTIVE";

    return `
      <section class="scanner-market-section gold-market-section" data-scanner-symbol="GC">
        <div class="scanner-market-head">
          <div class="scanner-market-identity">
            <div class="scanner-market-code">GC</div>
            <div>
              <h3>Gold Futures</h3>
              <div class="scanner-market-summary">${summary}</div>
            </div>
          </div>
          <span class="scanner-market-mode">${modeLabel}</span>
        </div>
        <div class="scanner-timeframe-grid">
          ${visibleTimeframes.map((tf) => scannerTimeframeCard(byTimeframe.get(tf), GOLD, tf, runtime)).join("")}
        </div>
      </section>`;
  };

  const renderDiagnosticsBeforeGold72I = renderDiagnostics;
  renderDiagnostics = function renderDiagnosticsGold72I(diagnostics, runtime = {}) {
    const goldOnly = (diagnostics || []).filter((d) => d.symbol === GOLD);
    return renderDiagnosticsBeforeGold72I(goldOnly, runtime);
  };

  const renderSystemBeforeGold72I = renderSystem;
  renderSystem = function renderSystemGold72I(snapshot) {
    const goldSnapshot = {
      ...(snapshot || {}),
      candles: (snapshot?.candles || []).filter((c) => c.symbol === GOLD),
    };
    return renderSystemBeforeGold72I(goldSnapshot);
  };

  scannerDecisionCard = function scannerDecisionCardGold72I(setup) {
    const status = setupDecisionStatus(setup);
    const reason = humanDecisionWhy(setupDecisionReason(setup), status);
    const trigger = cleanTrigger(setup?.trigger_type);
    const rr = setup?.risk_reward === null || setup?.risk_reward === undefined ? "--" : `${Number(setup.risk_reward).toFixed(2)}R`;
    return `
      <article class="scanner-decision-card gold-decision-card">
        <div class="scanner-decision-head">
          <div>
            <div class="scanner-decision-kicker">${setupCompletionLabel(setup)}</div>
            <strong>Gold Futures · ${String(setup.direction || "").toUpperCase()}</strong>
            <span>${setup.timeframe || "--"} · ${trigger} · ${fmtTime(setup.created_at)}</span>
          </div>
          ${statusChip(status, null)}
        </div>
        <div class="gold-decision-rr"><span>R:R</span><strong>${rr}</strong></div>
        <div class="gold-scanner-why"><span>WHY</span><strong>${reason}</strong></div>
      </article>`;
  };

  renderScannerDecisionHistory = function renderScannerDecisionHistoryGold72I(setups) {
    const panel = ensureScannerDecisionPanel();
    if (!panel) return;
    const root = document.getElementById("scannerDecisionCards");
    const decisions = (setups || []).filter((s) => s.symbol === GOLD).slice(0, 4);
    root.innerHTML = decisions.length
      ? decisions.map(scannerDecisionCard).join("")
      : '<div class="empty-state">Completed Gold decisions will appear here with a short explanation.</div>';
  };

  function makeGoldOnlySelect(id, label) {
    const select = document.getElementById(id);
    if (!select) return;
    select.innerHTML = `<option value="GC">${label}</option>`;
    select.value = "GC";
    select.disabled = true;
  }

  function applyGoldOnlyDom() {
    makeGoldOnlySelect("chartSymbol", "GC / MGC · Gold only");
    makeGoldOnlySelect("tradeSymbolFilter", "Gold Futures · Focus mode");
    makeGoldOnlySelect("setupSymbolFilter", "Gold Futures · Focus mode");
    makeGoldOnlySelect("scannerSymbolFilter", "Gold Futures · Focus mode");

    const primaryName = document.getElementById("primaryChartName");
    if (primaryName) primaryName.textContent = "GC";
    const primaryCanvas = document.getElementById("primaryExecutionCanvas");
    if (primaryCanvas) primaryCanvas.setAttribute("aria-label", "Gold bot execution candlestick chart");
    const pairCard = document.getElementById("pairChartCard");
    if (pairCard) pairCard.classList.add("hidden");

    const scannerPanel = document.getElementById("scannerCards")?.closest(".panel");
    const scannerHeading = scannerPanel?.querySelector("h2");
    if (scannerHeading) scannerHeading.textContent = "Gold Scanner";
    const scannerKicker = scannerPanel?.querySelector(".section-kicker");
    if (scannerKicker) scannerKicker.textContent = "GC / MGC FOCUS";

    const marketPanelHeading = document.getElementById("marketGrid")?.closest(".panel")?.querySelector("h2");
    if (marketPanelHeading) marketPanelHeading.textContent = "Gold Market";
  }

  applyGoldOnlyDom();
  document.addEventListener("DOMContentLoaded", applyGoldOnlyDom, { once: true });

  const style = document.createElement("style");
  style.textContent = `
    .gold-scanner-card .scanner-clarity-grid,
    .gold-scanner-card .scanner-progress-caption,
    .gold-scanner-card .scanner-detail-clear {
      display: none !important;
    }
    .gold-scanner-card {
      padding: 16px;
    }
    .gold-scanner-card .scan-rail,
    .gold-scanner-preview .scan-rail {
      margin-top: 14px;
    }
    .gold-scanner-state {
      margin-top: 10px;
    }
    .gold-scanner-why {
      margin-top: 13px;
      padding: 11px 12px;
      border: 1px solid rgba(255,255,255,.07);
      border-radius: 10px;
      background: rgba(255,255,255,.025);
    }
    .gold-scanner-why.compact {
      padding: 9px 10px;
    }
    .gold-scanner-why span,
    .gold-decision-rr span,
    .gold-scanner-trigger span {
      display: block;
      color: #747478;
      font-size: 9px;
      font-weight: 800;
      letter-spacing: .13em;
      margin-bottom: 5px;
    }
    .gold-scanner-why strong {
      display: block;
      color: #d8d8dc;
      font-size: 12px;
      font-weight: 600;
      line-height: 1.45;
    }
    .gold-scanner-trigger {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      margin-top: 10px;
      color: #aaaab0;
      font-size: 11px;
    }
    .gold-scanner-trigger span { margin: 0; }
    .gold-scanner-trigger strong { color: #d9d9dc; font-size: 11px; }
    .gold-market-section .scanner-timeframe-grid {
      gap: 10px;
    }
    .gold-decision-card {
      padding: 14px;
    }
    .gold-decision-rr {
      margin-top: 10px;
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
    }
    .gold-decision-rr span { margin: 0; }
    .gold-decision-rr strong { color: #ececee; font-size: 12px; }
    select:disabled {
      opacity: .78;
      cursor: default;
    }
  `;
  document.head.appendChild(style);
})();
