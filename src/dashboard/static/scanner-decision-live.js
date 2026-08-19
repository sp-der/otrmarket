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
