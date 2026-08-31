/* Operation 7.2I2: hard Gold-only dashboard + compact scanner.
 *
 * This file runs after app.js and scanner-clarity.js. It deliberately filters
 * the visible dashboard snapshot to GC while leaving the backend/history intact.
 * NQ/ES can still exist in storage and market ingestion, but they do not render
 * in the active dashboard during the Gold-only evaluation phase.
 */

(() => {
  "use strict";

  const GOLD = "GC";
  window.otrScannerDecisionBySetupId = new Map();

  const byId = (id) => document.getElementById(id);
  const goldOnly = (items) => (items || []).filter((item) => String(item?.symbol || "").toUpperCase() === GOLD);

  function setupDecisionReason(setup) {
    const metadata = setup?.metadata || {};
    return String(
      metadata?.execution_quality_gate?.reason ||
      metadata?.evaluation_guard?.reason ||
      metadata?.geometry_rejection ||
      metadata?.post_loss_quality?.reason ||
      metadata?.one_minute_reversal_guard_72?.reason ||
      "Candidate completed and reached the final safety checks."
    );
  }

  function setupDecisionStatus(setup) {
    const raw = String(setup?.status || "RECORDED").toUpperCase();
    const reason = setupDecisionReason(setup).toLowerCase();
    if (raw === "SESSION_BLOCKED" && reason.includes("after a loss")) return "QUALITY_BLOCKED";
    if (raw === "PENDING") return "REGISTERED";
    return raw;
  }

  function scannerDecisionForDiagnostic(d) {
    const setupId = String(d?.setup_id || "");
    if (!setupId) return null;
    return window.otrScannerDecisionBySetupId.get(setupId) || null;
  }

  function scannerDecisionLabel(status) {
    const labels = {
      REGISTERED: "TRADE ARMED",
      QUALITY_BLOCKED: "SKIPPED",
      SESSION_BLOCKED: "SKIPPED",
      GUARD_BLOCKED: "RISK LOCK",
      RISK_REJECTED: "BAD R:R",
      MISSED_EXTENDED: "MISSED",
      INVALIDATED: "CANCELED",
    };
    return labels[String(status || "").toUpperCase()] || "FINAL CHECK";
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
    if (map[raw]) return map[raw];
    if (!raw) return "--";
    return raw.replaceAll("_", " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
  }

  function humanWhy(reason, status) {
    const raw = String(reason || "").toLowerCase();
    const code = String(status || "").toUpperCase();

    if (code === "REGISTERED") return "All checks passed. Waiting for the planned Gold entry.";
    if (code === "MISSED_EXTENDED" || raw.includes("extended") || raw.includes("no chase") || raw.includes("too far")) {
      return "Price moved too far before entry, so OTR refused to chase it.";
    }
    if (raw.includes("after a loss") || raw.includes("post-loss") || raw.includes("post loss")) {
      return "After the last loss, this setup needed stronger confirmation.";
    }
    if (raw.includes("cooldown") || raw.includes("reset window")) {
      return "Gold was still in the reset window after the previous trade.";
    }
    if (raw.includes("daily") && raw.includes("stop") || raw.includes("max loss") || raw.includes("mll") || raw.includes("trade limit") || raw.includes("session slot") || raw.includes("daily primary-trade limit")) {
      return "The evaluation risk limit was already reached.";
    }
    if (raw.includes("risk/reward") || raw.includes("risk reward") || raw.includes("r:r") || raw.includes("geometry") || (raw.includes("stop") && raw.includes("target"))) {
      return "The entry, stop and target did not offer enough reward for the risk.";
    }
    if (raw.includes("reversal") && (raw.includes("confirm") || raw.includes("higher") || raw.includes("15m") || raw.includes("30m"))) {
      return "The reversal did not have enough confirmation from the larger charts.";
    }
    if (code === "SESSION_BLOCKED" || raw.includes("outside") && raw.includes("session")) {
      return "The setup formed outside the trading conditions allowed for this session.";
    }
    if (code === "QUALITY_BLOCKED" || raw.includes("quality") || raw.includes("grade")) {
      return "The setup formed, but the overall quality was not strong enough.";
    }
    if (code === "GUARD_BLOCKED") return "The setup was valid, but the account risk guard blocked another trade.";
    if (code === "RISK_REJECTED") return "The stop and target did not create a safe trade.";
    if (code === "INVALIDATED") return "Price broke the setup before entry, so OTR canceled it.";
    return "One of the final safety checks kept this setup from trading.";
  }

  function scannerModel(d) {
    if (!d) {
      return {
        chip: "WAITING",
        title: "Waiting for candles",
        why: "OTR needs fresh Gold candles before it can judge this timeframe.",
      };
    }

    const stage = String(d.stage || "WAITING");
    const setup = scannerDecisionForDiagnostic(d);
    if (stage === "SETUP_READY" && setup) {
      const status = setupDecisionStatus(setup);
      return {
        chip: scannerDecisionLabel(status),
        title: status === "REGISTERED" ? "Trade armed" : "Setup finished",
        why: humanWhy(setupDecisionReason(setup), status),
      };
    }

    const states = {
      WARMUP: ["WARMUP", "Warming up", "OTR needs more Gold candles before judging a setup."],
      WAIT_PD_ARRAY: ["WAITING", "Waiting for key zone", "Gold has not reached an area OTR wants to trade from yet."],
      WAIT_SIGNAL: ["CONFIRM", "Waiting for confirmation", "Price reached a key area. OTR now wants a clean sweep or confirmation."],
      WAIT_DISPLACEMENT: ["CONFIRM", "Waiting for strong move", "A signal appeared, but OTR still needs a strong move to confirm direction."],
      WAIT_ENTRY_FVG: ["ENTRY WATCH", "Waiting for pullback", "Direction is confirmed. OTR is waiting for a clean entry pullback."],
      WAIT_QUALIFYING_FVG: ["NO ENTRY", "Pullback outside zone", "A pullback formed outside the preferred entry area, so OTR is waiting."],
      WAIT_VALID_RR: ["R:R CHECK", "Risk/reward not ready", "The setup is close, but the stop and target are not good enough yet."],
      SETUP_READY: ["FINAL CHECK", "Setup complete", "The setup formed and is going through the final quality and risk checks."],
      EXPIRED: ["RESET", "Setup expired", "The setup did not finish in time, so OTR reset and moved on."],
    };
    const state = states[stage] || ["WATCHING", "Watching Gold", "OTR is waiting for the next clean Gold setup."];
    return { chip: state[0], title: state[1], why: state[2] };
  }

  function compactScannerRow(d, timeframe) {
    const model = scannerModel(d);
    const direction = d?.direction ? String(d.direction).toUpperCase() : "--";
    const score = Number(d?.score || 0);
    const trigger = cleanTrigger(d?.trigger_type);
    return `
      <article class="gold-scan-row">
        <div class="gold-scan-left">
          <strong class="gold-scan-tf">${timeframe}</strong>
          <span class="scanner-stage-chip">${model.chip}</span>
        </div>
        <div class="gold-scan-main">
          <div class="gold-scan-title">${direction !== "--" ? `${direction} · ` : ""}${model.title}</div>
          <div class="gold-scan-why"><span>WHY</span>${model.why}</div>
        </div>
        <div class="gold-scan-right">
          <strong>${score}/6</strong>
          <span>${trigger === "--" ? "" : trigger}</span>
        </div>
      </article>`;
  }

  function renderGoldDiagnostics(diagnostics, runtime = {}) {
    const rows = goldOnly(diagnostics);
    const timeframeFilter = byId("scannerTimeframeFilter")?.value || "all";
    const order = ["1m", "5m", "15m", "1h"];
    const byTimeframe = new Map(rows.map((d) => [String(d.timeframe), d]));
    const visible = order.filter((tf) => timeframeFilter === "all" || timeframeFilter === tf);
    const ranked = [...rows].sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
    const best = ranked[0];

    const preview = byId("scannerPreview");
    if (preview) {
      preview.innerHTML = best
        ? `<article class="scanner-card gold-preview-card">
            <div class="scanner-card-head">
              <div><div class="scanner-title">Gold Futures · ${best.timeframe}</div><div class="scanner-meta">${scannerModel(best).title}</div></div>
              <span class="score-chip">${Number(best.score || 0)}/6</span>
            </div>
            <div class="gold-preview-why"><span>WHY</span>${scannerModel(best).why}</div>
          </article>`
        : '<div class="empty-state">OTR is waiting for fresh Gold scanner data.</div>';
    }

    const cards = byId("scannerCards");
    if (cards) {
      cards.innerHTML = `
        <section class="scanner-market-section gold-only-scanner">
          <div class="scanner-market-head">
            <div class="scanner-market-identity">
              <div class="scanner-market-code">GC</div>
              <div><h3>Gold Futures</h3><div class="scanner-market-summary">${best ? `${Number(best.score || 0)}/6 · ${scannerModel(best).title}` : "Waiting for scanner history"}</div></div>
            </div>
            <span class="scanner-market-mode">${runtime?.mode === "REPLAY" ? "GOLD REPLAY" : "GOLD ACTIVE"}</span>
          </div>
          <div class="gold-scan-list">${visible.map((tf) => compactScannerRow(byTimeframe.get(tf), tf)).join("")}</div>
        </section>`;
    }

    const oldDecisionPanel = byId("scannerDecisionPanel");
    if (oldDecisionPanel) oldDecisionPanel.remove();
  }

  function forceGoldSelect(id, label) {
    const select = byId(id);
    if (!select) return;
    select.innerHTML = `<option value="GC">${label}</option>`;
    select.value = GOLD;
    select.disabled = true;
  }

  function applyGoldDom() {
    forceGoldSelect("chartSymbol", "GC / MGC · Gold only");
    forceGoldSelect("tradeSymbolFilter", "Gold Futures");
    forceGoldSelect("setupSymbolFilter", "Gold Futures");
    forceGoldSelect("scannerSymbolFilter", "Gold Futures");

    const pairCard = byId("pairChartCard");
    if (pairCard) pairCard.classList.add("hidden");
    const pairChip = byId("chartSync");
    if (pairChip) pairChip.classList.add("hidden");
    const primaryName = byId("primaryChartName");
    if (primaryName) primaryName.textContent = "GC";
    const primaryCanvas = byId("primaryExecutionCanvas");
    if (primaryCanvas) primaryCanvas.setAttribute("aria-label", "Gold bot execution candlestick chart");

    const marketHeading = byId("marketGrid")?.closest(".panel")?.querySelector("h2");
    if (marketHeading) marketHeading.textContent = "Gold Market";
    const scannerHeading = byId("scannerCards")?.closest(".panel")?.querySelector("h2");
    if (scannerHeading) scannerHeading.textContent = "Gold Scanner";
    const scannerKicker = byId("scannerCards")?.closest(".panel")?.querySelector(".section-kicker");
    if (scannerKicker) scannerKicker.textContent = "GC / MGC FOCUS";
  }

  /* Populate decision lookup before the scanner renders. */
  if (typeof renderSetups === "function") {
    const baseRenderSetups = renderSetups;
    renderSetups = function renderGoldSetups(setups) {
      const filtered = goldOnly(setups);
      window.otrScannerDecisionBySetupId = new Map(
        filtered.filter((s) => s?.setup_id).map((s) => [String(s.setup_id), s])
      );
      return baseRenderSetups(filtered);
    };
  }

  /* Hard-filter the actual dashboard snapshot. This is the important part: the
   * old UI cannot render NQ/ES because it never receives them here. */
  if (typeof render === "function") {
    const baseRender = render;
    render = function renderGoldOnlySnapshot(snapshot) {
      const filtered = {
        ...(snapshot || {}),
        markets: goldOnly(snapshot?.markets),
        trades: goldOnly(snapshot?.trades),
        setups: goldOnly(snapshot?.setups),
        diagnostics: goldOnly(snapshot?.diagnostics),
        candles: goldOnly(snapshot?.candles),
      };
      const result = baseRender(filtered);
      applyGoldDom();
      renderGoldDiagnostics(filtered.diagnostics || [], filtered.runtime || {});
      return result;
    };
  }

  /* Replace scanner rendering outright so older scanner layers cannot re-add
   * verbose grids or non-Gold sections. */
  renderDiagnostics = renderGoldDiagnostics;

  applyGoldDom();
  document.addEventListener("DOMContentLoaded", applyGoldDom, { once: true });

  const style = document.createElement("style");
  style.textContent = `
    #scannerDecisionPanel { display: none !important; }
    .gold-only-scanner .scanner-timeframe-grid,
    .gold-only-scanner .scanner-clarity-grid,
    .gold-only-scanner .scan-rail { display: none !important; }
    .gold-scan-list { display: grid; gap: 8px; margin-top: 12px; }
    .gold-scan-row {
      display: grid;
      grid-template-columns: 118px minmax(0, 1fr) 90px;
      gap: 14px;
      align-items: center;
      padding: 12px 14px;
      border: 1px solid rgba(255,255,255,.08);
      border-radius: 12px;
      background: rgba(255,255,255,.018);
    }
    .gold-scan-left { display: flex; align-items: center; gap: 9px; min-width: 0; }
    .gold-scan-tf { font-size: 14px; color: #f0f0f2; min-width: 24px; }
    .gold-scan-main { min-width: 0; }
    .gold-scan-title { color: #e1e1e4; font-size: 12px; font-weight: 700; margin-bottom: 4px; }
    .gold-scan-why { color: #929298; font-size: 11px; line-height: 1.4; }
    .gold-scan-why span,
    .gold-preview-why span { color: #66666c; font-size: 9px; font-weight: 800; letter-spacing: .12em; margin-right: 7px; }
    .gold-scan-right { text-align: right; min-width: 0; }
    .gold-scan-right strong { display: block; color: #e5e5e7; font-size: 12px; }
    .gold-scan-right span { display: block; color: #77777d; font-size: 9px; margin-top: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .gold-preview-card { max-width: 620px; }
    .gold-preview-why { margin-top: 12px; color: #a1a1a7; font-size: 11px; line-height: 1.45; }
    select:disabled { opacity: .8; cursor: default; }
    @media (max-width: 760px) {
      .gold-scan-row { grid-template-columns: 90px minmax(0,1fr); }
      .gold-scan-right { grid-column: 2; text-align: left; display: flex; gap: 8px; align-items: baseline; }
      .gold-scan-right strong, .gold-scan-right span { display: inline; margin: 0; }
    }
  `;
  document.head.appendChild(style);
})();
