/* Operation 5.8: scanner clarity layer.
 *
 * The six scanner booleans are cumulative progress for the active setup
 * sequence. A later FVG can fail the 50-79% zone after an earlier FVG already
 * passed it, so the rail may correctly stay at 5/6 while the current candidate
 * is rejected. This layer makes that distinction explicit instead of making
 * the dashboard look contradictory.
 */

function scannerClarityState(d) {
  const stage = String(d?.stage || "WAITING");
  const note = String(d?.note || "");
  const noteLower = note.toLowerCase();
  const score = Number(d?.score || 0);
  const outsideZoneNow =
    stage === "WAIT_VALID_RR" &&
    Boolean(d?.retracement) &&
    (noteLower.includes("outside the 50-79") || noteLower.includes("outside 50-79"));

  if (outsideZoneNow) {
    return {
      short: "Current FVG outside 50-79%",
      current: "Outside 50-79%",
      tier: "Not graded yet",
      window: "15-bar entry window active",
      detail:
        `The ${score}/6 score is sequence memory: a prior qualifying FVG already passed 50-79%. ` +
        "This current FVG is outside the zone, so it is rejected while the bot keeps searching. R:R is still unresolved.",
    };
  }

  if (stage === "WAIT_VALID_RR") {
    return {
      short: "50-79% passed · R:R unresolved",
      current: "Zone passed · geometry rejected",
      tier: "A/A+ or B+ after geometry",
      window: "15-bar entry window active",
      detail:
        note ||
        "A qualifying FVG passed the 50-79% zone, but the current stop/target geometry is not valid yet. The scanner is looking for another candidate.",
    };
  }

  if (stage === "WAIT_QUALIFYING_FVG") {
    return {
      short: "Current FVG outside 50-79%",
      current: "Outside 50-79%",
      tier: "Not graded yet",
      window: "15-bar entry window active",
      detail:
        "An FVG exists, but this candidate is outside the required displacement retracement zone. It does not advance to R:R.",
    };
  }

  if (stage === "WAIT_ENTRY_FVG") {
    return {
      short: "Waiting for entry FVG",
      current: "No qualifying FVG yet",
      tier: "Not graded yet",
      window: "15-bar entry window active",
      detail: note || "Displacement is confirmed. Waiting for a same-direction entry FVG.",
    };
  }

  if (stage === "WAIT_DISPLACEMENT") {
    return {
      short: "Waiting for displacement",
      current: "Signal passed · displacement pending",
      tier: "Not graded yet",
      window: "15-bar displacement window",
      detail: note || "The signal is confirmed and the scanner is waiting for displacement.",
    };
  }

  if (stage === "WAIT_SIGNAL") {
    return {
      short: "Waiting for signal",
      current: "PD array passed · signal pending",
      tier: "Not graded yet",
      window: "16-bar signal window",
      detail: note || "The PD array is active. Waiting for liquidity sweep or SMT confirmation.",
    };
  }

  if (stage === "WAIT_PD_ARRAY") {
    return {
      short: "Waiting for PD array",
      current: "No active sequence yet",
      tier: "Not graded yet",
      window: "Starts after PD touch",
      detail: note || "Waiting for price to touch an active PD array.",
    };
  }

  if (stage === "SETUP_READY") {
    return {
      short: "Geometry passed · setup ready",
      current: "Valid candidate",
      tier: "Quality gate next",
      window: "Sequence complete",
      detail:
        note ||
        "The six-stage sequence and trade geometry passed. A/A+ or reduced-risk B+ classification is handled by the execution-quality gate.",
    };
  }

  if (stage === "EXPIRED") {
    return {
      short: "Sequence expired",
      current: "Candidate discarded",
      tier: "No execution",
      window: "Expired",
      detail: note || "The setup-development window expired. Waiting for a new sequence.",
    };
  }

  return {
    short: prettyStage(stage),
    current: prettyStage(stage),
    tier: "Not graded yet",
    window: "Waiting",
    detail: note || "Waiting for strategy data.",
  };
}

function scannerClarityGrid(d) {
  const clarity = scannerClarityState(d);
  const score = Number(d?.score || 0);
  return `
    <div class="scanner-clarity-grid">
      <div class="scanner-clarity-item">
        <span>SEQUENCE</span>
        <strong>${score}/6 reached</strong>
      </div>
      <div class="scanner-clarity-item">
        <span>CURRENT CANDIDATE</span>
        <strong>${clarity.current}</strong>
      </div>
      <div class="scanner-clarity-item">
        <span>EXECUTION TIER</span>
        <strong>${clarity.tier}</strong>
      </div>
      <div class="scanner-clarity-item">
        <span>WINDOW</span>
        <strong>${clarity.window}</strong>
      </div>
    </div>`;
}

// Replace the full scanner timeframe card with a version that separates
// cumulative sequence progress from the candidate being evaluated right now.
scannerTimeframeCard = function scannerTimeframeCardClarity(d, symbol, timeframe, runtime) {
  if (!d) return scannerEmptyCard(symbol, timeframe, runtime);
  const direction = d.direction ? String(d.direction).toUpperCase() : "--";
  const trigger = d.trigger_type ? String(d.trigger_type).replaceAll("_", " ") : "--";
  const stage = d.stage || "WAITING";
  const score = Number(d.score || 0);
  const clarity = scannerClarityState(d);
  const stageClass = stage === "SETUP_READY" ? "is-ready" : stage === "EXPIRED" ? "is-expired" : score >= 3 ? "is-advanced" : "";

  return `
    <article class="scanner-timeframe-card ${stageClass}">
      <div class="scanner-tf-top">
        <div class="scanner-tf-name">${timeframe}</div>
        <span class="scanner-stage-chip">${scannerStageChip(stage)}</span>
      </div>
      <div class="scanner-state-row">
        <div>
          <div class="scanner-direction">${direction}</div>
          <div class="scanner-primary-state">${clarity.short}</div>
        </div>
        <div class="scanner-score-block"><strong>${score}/6</strong><span>sequence · ${fmtTime(d.market_time)}</span></div>
      </div>
      <div class="scanner-progress-caption">SEQUENCE MEMORY · a lit step means it passed at least once during this active setup</div>
      ${scannerProgress(d)}
      ${scannerClarityGrid(d)}
      <div class="scanner-detail-line scanner-detail-clear">${clarity.detail}</div>
      <div class="scanner-tf-foot"><span>Trigger</span><strong>${trigger}</strong></div>
    </article>`;
};

// Make the market-level summary describe the current candidate rather than
// repeating a stage label that can look inconsistent with cumulative progress.
scannerMarketSection = function scannerMarketSectionClarity(symbol, diagnostics, runtime, timeframeFilter) {
  if (symbol === "BTC-USD") return "";
  const byTimeframe = new Map((diagnostics || []).filter((d) => d.symbol === symbol).map((d) => [d.timeframe, d]));
  const visibleTimeframes = scannerTimeframeOrder.filter((tf) => timeframeFilter === "all" || timeframeFilter === tf);
  const candidates = visibleTimeframes.map((tf) => byTimeframe.get(tf)).filter(Boolean);
  const best = [...candidates].sort((a, b) => {
    const scoreDiff = Number(b.score || 0) - Number(a.score || 0);
    if (scoreDiff) return scoreDiff;
    return Number(scannerStageOrder[b.stage] || 0) - Number(scannerStageOrder[a.stage] || 0);
  })[0];
  const modeLabel = runtime?.mode === "REPLAY" ? "REPLAY SCAN" : "ACTIVE SCAN";
  const summary = best
    ? `Best sequence ${Number(best.score || 0)}/6 · ${scannerClarityState(best).short}`
    : "Waiting for scanner history";

  return `
    <section class="scanner-market-section" data-scanner-symbol="${symbol}">
      <div class="scanner-market-head">
        <div class="scanner-market-identity">
          <div class="scanner-market-code">${scannerSymbolCode[symbol] || symbol}</div>
          <div>
            <h3>${labelMap[symbol] || symbol}</h3>
            <div class="scanner-market-summary">${summary}</div>
          </div>
        </div>
        <span class="scanner-market-mode">${modeLabel}</span>
      </div>
      <div class="scanner-timeframe-grid">
        ${visibleTimeframes.map((tf) => scannerTimeframeCard(byTimeframe.get(tf), symbol, tf, runtime)).join("")}
      </div>
    </section>`;
};

scannerPreviewCard = function scannerPreviewCardClarity(d) {
  const direction = d.direction ? String(d.direction).toUpperCase() : "--";
  const trigger = d.trigger_type ? String(d.trigger_type).replaceAll("_", " ") : "--";
  const clarity = scannerClarityState(d);
  return `
    <article class="scanner-card scanner-preview-card">
      <div class="scanner-card-head">
        <div>
          <div class="scanner-title">${labelMap[d.symbol] || d.symbol} · ${d.timeframe}</div>
          <div class="scanner-meta">${direction} · ${clarity.short}</div>
        </div>
        <span class="score-chip">${Number(d.score || 0)}/6</span>
      </div>
      ${scannerProgress(d)}
      <div class="scanner-preview-current"><span>Current</span><strong>${clarity.current}</strong></div>
      <div class="scanner-foot"><span>Trigger</span><strong>${trigger}</strong></div>
    </article>`;
};

(function installScannerClarityStyles() {
  const style = document.createElement("style");
  style.textContent = `
    .scanner-progress-caption {
      margin: 20px 0 8px;
      color: #6f6f73;
      font-size: 10px;
      font-weight: 800;
      letter-spacing: .13em;
    }
    .scanner-clarity-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin: 18px 0 0;
    }
    .scanner-clarity-item {
      min-width: 0;
      border: 1px solid rgba(255,255,255,.08);
      border-radius: 10px;
      background: rgba(255,255,255,.025);
      padding: 10px 12px;
    }
    .scanner-clarity-item span,
    .scanner-preview-current span {
      display: block;
      color: #737378;
      font-size: 9px;
      font-weight: 800;
      letter-spacing: .12em;
      margin-bottom: 5px;
    }
    .scanner-clarity-item strong {
      display: block;
      color: #e9e9eb;
      font-size: 12px;
      line-height: 1.35;
    }
    .scanner-detail-clear {
      color: #a2a2a8;
      line-height: 1.55;
    }
    .scanner-preview-current {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid rgba(255,255,255,.07);
    }
    .scanner-preview-current span { margin: 0; }
    .scanner-preview-current strong {
      color: #d9d9dc;
      font-size: 11px;
      text-align: right;
    }
    @media (max-width: 720px) {
      .scanner-clarity-grid { grid-template-columns: 1fr; }
    }
  `;
  document.head.appendChild(style);
})();

/* Operation 5.9: completed-decision trail + futures-only active UI. */
const activeFuturesSymbols = new Set(["NQ", "ES", "GC"]);

function setupDecisionReason(setup) {
  const metadata = setup?.metadata || {};
  return (
    metadata?.execution_quality_gate?.reason ||
    metadata?.evaluation_guard?.reason ||
    metadata?.geometry_rejection ||
    metadata?.post_loss_quality?.reason ||
    "Candidate completed and was recorded by the execution engine."
  );
}

function setupDecisionStatus(setup) {
  const raw = String(setup?.status || "RECORDED").toUpperCase();
  const reason = setupDecisionReason(setup).toLowerCase();
  // Historical 5.8 rows used SESSION_BLOCKED for the post-loss quality test.
  // Present those with the correct semantic label without rewriting history.
  if (raw === "SESSION_BLOCKED" && reason.includes("after a loss")) return "QUALITY_BLOCKED";
  if (raw === "PENDING") return "REGISTERED";
  return raw;
}

function setupCompletionLabel(setup) {
  const strategy = String(setup?.metadata?.strategy || "ICT_CONFLUENCE");
  if (strategy === "REJECTION_BLOCK_10_10") return "10/10 COMPLETE";
  if (strategy === "ICT_CONFLUENCE") return "6/6 COMPLETE";
  return "SETUP COMPLETE";
}

function scannerDecisionCard(setup) {
  const status = setupDecisionStatus(setup);
  const trigger = String(setup?.trigger_type || "--").replaceAll("_", " ");
  const rr = setup?.risk_reward === null || setup?.risk_reward === undefined ? "--" : `${Number(setup.risk_reward).toFixed(2)}R`;
  const reason = setupDecisionReason(setup);
  return `
    <article class="scanner-decision-card">
      <div class="scanner-decision-head">
        <div>
          <div class="scanner-decision-kicker">${setupCompletionLabel(setup)}</div>
          <strong>${labelMap[setup.symbol] || setup.symbol} · ${String(setup.direction || "").toUpperCase()}</strong>
          <span>${setup.timeframe || "--"} · ${trigger} · ${fmtTime(setup.created_at)}</span>
        </div>
        ${statusChip(status, null)}
      </div>
      <div class="scanner-decision-metrics">
        <span>ENTRY <strong>${fmtPrice(setup.entry_price)}</strong></span>
        <span>R:R <strong>${rr}</strong></span>
      </div>
      <div class="scanner-decision-reason"><span>DECISION REASON</span>${reason}</div>
    </article>`;
}

function ensureScannerDecisionPanel() {
  let panel = document.getElementById("scannerDecisionPanel");
  if (panel) return panel;
  const scannerCards = document.getElementById("scannerCards");
  const scannerPanel = scannerCards?.closest(".panel");
  if (!scannerPanel) return null;
  panel = document.createElement("section");
  panel.id = "scannerDecisionPanel";
  panel.className = "panel scanner-decision-panel";
  panel.innerHTML = `
    <div class="panel-head">
      <div>
        <div class="section-kicker">COMPLETED CANDIDATES</div>
        <h2>Recent Scanner Decisions</h2>
      </div>
      <span class="tiny-chip">WHY IT DID / DIDN'T TRADE</span>
    </div>
    <div id="scannerDecisionCards" class="scanner-decision-list"></div>`;
  scannerPanel.insertAdjacentElement("afterend", panel);
  return panel;
}

function renderScannerDecisionHistory(setups) {
  const panel = ensureScannerDecisionPanel();
  if (!panel) return;
  const root = document.getElementById("scannerDecisionCards");
  const decisions = (setups || [])
    .filter((s) => activeFuturesSymbols.has(s.symbol))
    .slice(0, 8);
  root.innerHTML = decisions.length
    ? decisions.map(scannerDecisionCard).join("")
    : '<div class="empty-state">Completed 6/6 decisions will remain here after the live scanner resets.</div>';
}

const renderMarketsBefore59 = renderMarkets;
renderMarkets = function renderMarkets59(markets) {
  return renderMarketsBefore59((markets || []).filter((m) => activeFuturesSymbols.has(m.symbol)));
};

const renderTradesBefore59 = renderTrades;
renderTrades = function renderTrades59(trades) {
  return renderTradesBefore59((trades || []).filter((t) => activeFuturesSymbols.has(t.symbol)));
};

const setupCardBefore59 = setupCard;
setupCard = function setupCard59(setup) {
  const displaySetup = { ...setup, status: setupDecisionStatus(setup) };
  const reason = setupDecisionReason(setup);
  const card = setupCardBefore59(displaySetup);
  return card.replace(
    "</article>",
    `<div class="setup-decision-reason"><span>WHY</span>${reason}</div></article>`
  );
};

const renderSetupsBefore59 = renderSetups;
renderSetups = function renderSetups59(setups) {
  const active = (setups || []).filter((s) => activeFuturesSymbols.has(s.symbol));
  renderSetupsBefore59(active);
  renderScannerDecisionHistory(active);
};

(function installDecisionTrail() {
  document.querySelectorAll('option[value="BTC-USD"]').forEach((option) => option.remove());
  ensureScannerDecisionPanel();
  const style = document.createElement("style");
  style.textContent = `
    .setup-decision-reason {
      margin-top: 14px;
      padding-top: 13px;
      border-top: 1px solid rgba(255,255,255,.07);
      color: #a4a4a9;
      font-size: 12px;
      line-height: 1.5;
    }
    .setup-decision-reason span,
    .scanner-decision-reason span {
      display: block;
      margin-bottom: 5px;
      color: #6f6f73;
      font-size: 9px;
      font-weight: 800;
      letter-spacing: .13em;
    }
    .scanner-decision-list {
      display: grid;
      gap: 10px;
    }
    .scanner-decision-card {
      border: 1px solid rgba(255,255,255,.09);
      border-radius: 14px;
      background: rgba(255,255,255,.018);
      padding: 16px;
    }
    .scanner-decision-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }
    .scanner-decision-head strong {
      display: block;
      color: #f3f3f4;
      font-size: 15px;
      margin: 2px 0 4px;
    }
    .scanner-decision-head > div > span {
      color: #838388;
      font-size: 11px;
    }
    .scanner-decision-kicker {
      color: #b8b8bc;
      font-size: 9px;
      font-weight: 900;
      letter-spacing: .14em;
    }
    .scanner-decision-metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 14px;
    }
    .scanner-decision-metrics span {
      border: 1px solid rgba(255,255,255,.06);
      border-radius: 9px;
      padding: 9px 10px;
      color: #707076;
      font-size: 9px;
      font-weight: 800;
      letter-spacing: .1em;
    }
    .scanner-decision-metrics strong {
      display: block;
      margin-top: 4px;
      color: #e9e9eb;
      font-size: 12px;
      letter-spacing: 0;
    }
    .scanner-decision-reason {
      margin-top: 13px;
      color: #b0b0b5;
      font-size: 12px;
      line-height: 1.5;
    }
    @media (max-width: 720px) {
      .scanner-decision-head { align-items: flex-start; }
      .scanner-decision-metrics { grid-template-columns: 1fr 1fr; }
    }
  `;
  document.head.appendChild(style);
})();
