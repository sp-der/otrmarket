/* Operation 7.2J: restore detailed scanner + simplify Gold setups.
 *
 * Scanner returns to the prior six-step diagnostic presentation and final-decision
 * sync. The active dashboard remains Gold-only. The Setups page becomes the
 * compact, plain-English surface instead of the Scanner page.
 */

window.otrScannerDecisionBySetupId = new Map();

const GOLD_FOCUS_SYMBOL = "GC";
const byId72J = (id) => document.getElementById(id);
const goldOnly72J = (items) => (items || []).filter((item) => String(item?.symbol || "").toUpperCase() === GOLD_FOCUS_SYMBOL);

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

/* Restore the original detailed scanner decision sync. */
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

/* Keep the restored scanner detailed, but Gold-only. */
if (typeof activeFuturesSymbols !== "undefined") {
  activeFuturesSymbols.clear();
  activeFuturesSymbols.add(GOLD_FOCUS_SYMBOL);
}
if (typeof scannerMarketOrder !== "undefined" && Array.isArray(scannerMarketOrder)) {
  scannerMarketOrder.splice(0, scannerMarketOrder.length, GOLD_FOCUS_SYMBOL);
}

function cleanTrigger72J(value) {
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
  if (map[raw]) return map[raw];
  if (!raw) return "--";
  return raw.replaceAll("_", " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}

function simpleSetupStatus72J(status) {
  const map = {
    REGISTERED: "TRADE ARMED",
    QUALITY_BLOCKED: "SKIPPED",
    SESSION_BLOCKED: "SKIPPED",
    GUARD_BLOCKED: "RISK LOCK",
    RISK_REJECTED: "BAD R:R",
    MISSED_EXTENDED: "MISSED",
    INVALIDATED: "CANCELED",
  };
  return map[String(status || "").toUpperCase()] || String(status || "RECORDED").replaceAll("_", " ");
}

function humanWhy(reason, status) {
  const raw = String(reason || "").toLowerCase();
  const code = String(status || "").toUpperCase();

  if (code === "REGISTERED") return "All checks passed. OTR is waiting for the planned Gold entry.";
  if (code === "MISSED_EXTENDED" || raw.includes("extended") || raw.includes("no chase") || raw.includes("too far")) {
    return "Price moved too far before a safe entry, so OTR did not chase it.";
  }
  if (raw.includes("after a loss") || raw.includes("post-loss") || raw.includes("post loss")) {
    return "After the last loss, this setup needed stronger confirmation.";
  }
  if (raw.includes("cooldown") || raw.includes("reset window")) {
    return "Gold was still in the reset period after the previous trade.";
  }
  if ((raw.includes("daily") && raw.includes("stop")) || raw.includes("max loss") || raw.includes("mll") || raw.includes("trade limit") || raw.includes("session slot") || raw.includes("evaluation")) {
    return "The account or evaluation risk limit would not allow this trade.";
  }
  if (raw.includes("risk/reward") || raw.includes("risk reward") || raw.includes("r:r") || raw.includes("geometry") || (raw.includes("stop") && raw.includes("target"))) {
    return "The entry, stop, and target did not offer enough reward for the risk.";
  }
  if (raw.includes("reversal") && (raw.includes("confirm") || raw.includes("higher") || raw.includes("15m") || raw.includes("30m"))) {
    return "The reversal did not have enough confirmation from the larger charts.";
  }
  if (code === "SESSION_BLOCKED" || (raw.includes("outside") && raw.includes("session"))) {
    return "The setup formed outside the trading conditions allowed for that session.";
  }
  if (code === "QUALITY_BLOCKED" || raw.includes("quality") || raw.includes("grade")) {
    return "The setup formed, but its overall quality was not strong enough.";
  }
  if (code === "GUARD_BLOCKED") return "The setup was valid, but the account risk guard blocked another trade.";
  if (code === "RISK_REJECTED") return "The stop and target did not create a safe trade.";
  if (code === "INVALIDATED") return "Price broke the setup before entry, so OTR canceled it.";
  return "One of the final safety checks kept this setup from trading.";
}

function compactSetupCard72J(setup) {
  const status = setupDecisionStatus(setup);
  const displayStatus = simpleSetupStatus72J(status);
  const trigger = cleanTrigger72J(setup?.trigger_type);
  const rr = setup?.risk_reward === null || setup?.risk_reward === undefined
    ? "--"
    : `${Number(setup.risk_reward).toFixed(2)}R`;
  const reason = humanWhy(setupDecisionReason(setup), status);
  const direction = String(setup?.direction || "--").toUpperCase();
  const chipClass = String(displayStatus).toLowerCase().replace(/[^a-z]+/g, "-");

  return `
    <article class="setup-card setup-card-simple">
      <div class="setup-simple-head">
        <div>
          <div class="setup-title">Gold Futures · ${direction}</div>
          <div class="setup-meta">${setup.timeframe || "--"} · ${trigger} · ${fmtTime(setup.created_at)}</div>
        </div>
        <span class="status-chip ${chipClass}">${displayStatus}</span>
      </div>
      <div class="setup-simple-why"><span>WHY</span><strong>${reason}</strong></div>
      <div class="setup-simple-numbers">
        <span>Entry <strong>${fmtPrice(setup.entry_price)}</strong></span>
        <span>Stop <strong>${fmtPrice(setup.stop_price)}</strong></span>
        <span>Target <strong>${fmtPrice(setup.target_price)}</strong></span>
        <span>R:R <strong>${rr}</strong></span>
      </div>
    </article>`;
}

/* Preserve existing setup-side effects (including scanner decision history),
 * then replace only the visible Setups page with the simplified Gold cards. */
const renderSetupsBefore72J = renderSetups;
renderSetups = function renderGoldSetups(setups) {
  const filtered = goldOnly72J(setups);
  window.otrScannerDecisionBySetupId = new Map(
    filtered
      .filter((setup) => setup?.setup_id)
      .map((setup) => [String(setup.setup_id), setup])
  );

  const result = renderSetupsBefore72J(filtered);

  const latest = filtered[0];
  const latestRoot = byId72J("latestSetup");
  if (latest && latestRoot) {
    const status = setupDecisionStatus(latest);
    latestRoot.className = "latest-card latest-card-simple";
    latestRoot.innerHTML = `
      <strong>Gold Futures · ${String(latest.direction || "--").toUpperCase()}</strong>
      <div class="latest-row"><span>Status</span><strong>${simpleSetupStatus72J(status)}</strong></div>
      <div class="latest-row"><span>Why</span><strong>${humanWhy(setupDecisionReason(latest), status)}</strong></div>`;
  }

  const triggerFilter = byId72J("setupTriggerFilter")?.value || "all";
  const visible = filtered.filter((setup) => triggerFilter === "all" || setup.trigger_type === triggerFilter);
  const root = byId72J("setupCards");
  if (root) {
    root.innerHTML = visible.length
      ? visible.map(compactSetupCard72J).join("")
      : '<div class="empty-state">No Gold setups match this filter.</div>';
  }

  return result;
};

function forceGoldSelect72J(id, label) {
  const select = byId72J(id);
  if (!select) return;
  select.innerHTML = `<option value="GC">${label}</option>`;
  select.value = GOLD_FOCUS_SYMBOL;
  select.disabled = true;
}

function applyGoldDom72J() {
  forceGoldSelect72J("chartSymbol", "GC / MGC · Gold only");
  forceGoldSelect72J("tradeSymbolFilter", "Gold Futures");
  forceGoldSelect72J("setupSymbolFilter", "Gold Futures");
  forceGoldSelect72J("scannerSymbolFilter", "Gold Futures");

  const pairCard = byId72J("pairChartCard");
  if (pairCard) pairCard.classList.add("hidden");
  const pairChip = byId72J("chartSync");
  if (pairChip) pairChip.classList.add("hidden");
  const primaryName = byId72J("primaryChartName");
  if (primaryName) primaryName.textContent = "GC";
  const primaryCanvas = byId72J("primaryExecutionCanvas");
  if (primaryCanvas) primaryCanvas.setAttribute("aria-label", "Gold bot execution candlestick chart");

  const marketHeading = byId72J("marketGrid")?.closest(".panel")?.querySelector("h2");
  if (marketHeading) marketHeading.textContent = "Gold Market";

  const setupPanel = byId72J("setupCards")?.closest(".panel");
  const setupHeading = setupPanel?.querySelector("h2");
  if (setupHeading) setupHeading.textContent = "Gold Setups";
  const setupKicker = setupPanel?.querySelector(".section-kicker");
  if (setupKicker) setupKicker.textContent = "QUICK DECISIONS";

  const scannerPanel = byId72J("scannerCards")?.closest(".panel");
  const scannerHeading = scannerPanel?.querySelector("h2");
  if (scannerHeading) scannerHeading.textContent = "Gold Scanner";
  const scannerKicker = scannerPanel?.querySelector(".section-kicker");
  if (scannerKicker) scannerKicker.textContent = "GC / MGC FOCUS";
}

/* Hard-filter the visible dashboard snapshot so NQ/ES cannot reappear in UI. */
if (typeof render === "function") {
  const renderBeforeGold72J = render;
  render = function renderGoldOnlySnapshot(snapshot) {
    const filtered = {
      ...(snapshot || {}),
      markets: goldOnly72J(snapshot?.markets),
      trades: goldOnly72J(snapshot?.trades),
      setups: goldOnly72J(snapshot?.setups),
      diagnostics: goldOnly72J(snapshot?.diagnostics),
      candles: goldOnly72J(snapshot?.candles),
    };
    const result = renderBeforeGold72J(filtered);
    applyGoldDom72J();
    return result;
  };
}

applyGoldDom72J();
document.addEventListener("DOMContentLoaded", applyGoldDom72J, { once: true });

(function install72JStyles() {
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
    .setup-card-simple {
      padding: 15px 16px;
    }
    .setup-simple-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }
    .setup-simple-why {
      margin-top: 12px;
      padding: 10px 12px;
      border: 1px solid rgba(255,255,255,.07);
      border-radius: 10px;
      background: rgba(255,255,255,.02);
    }
    .setup-simple-why span {
      display: block;
      color: #6d6d73;
      font-size: 9px;
      font-weight: 800;
      letter-spacing: .12em;
      margin-bottom: 4px;
    }
    .setup-simple-why strong {
      display: block;
      color: #d7d7db;
      font-size: 12px;
      font-weight: 600;
      line-height: 1.45;
    }
    .setup-simple-numbers {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }
    .setup-simple-numbers span {
      color: #727278;
      font-size: 10px;
    }
    .setup-simple-numbers strong {
      display: block;
      margin-top: 3px;
      color: #ececee;
      font-size: 12px;
    }
    .latest-card-simple .latest-row:last-child strong {
      max-width: 72%;
      text-align: right;
      line-height: 1.35;
    }
    select:disabled {
      opacity: .8;
      cursor: default;
    }
    @media (max-width: 720px) {
      .setup-simple-numbers { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .setup-simple-head { align-items: center; }
    }
  `;
  document.head.appendChild(style);
})();
