/* Operation 7.2T minimal production monitor.
 * Existing dashboard renderers keep running against their original hidden DOM.
 * This layer only changes what the operator has to look at.
 */
(() => {
  const TF_ORDER = ["1m", "5m", "15m", "1h"];
  const STEPS = [
    ["PD", "pd_array"],
    ["SIGNAL", "signal"],
    ["DISP", "displacement"],
    ["FVG", "entry_fvg"],
    ["ENTRY", "retracement"],
    ["R:R", "rr"],
  ];
  let training = null;
  let snapshot = null;
  let stopped = false;

  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));

  function setupShell() {
    ["setups", "scanner", "system"].forEach((view) => {
      const item = document.querySelector(`.nav-item[data-view="${view}"]`);
      if (item) item.classList.add("minimal72t-nav-hidden");
    });
    const lab = document.querySelector('a.nav-item[href="/market/research"]');
    if (lab) lab.innerHTML = "Strategy Lab";

    const overview = document.getElementById("overviewView");
    if (!overview) return null;
    let root = document.getElementById("otrMinimal72t");
    if (!root) {
      root = document.createElement("div");
      root.id = "otrMinimal72t";
      root.className = "otr-minimal72t";
      overview.prepend(root);
    }
    [...overview.children].forEach((child) => {
      if (child !== root) child.classList.add("minimal72t-hidden");
    });

    const observer = new MutationObserver(() => {
      [...overview.children].forEach((child) => {
        if (child !== root) child.classList.add("minimal72t-hidden");
      });
    });
    if (!overview.dataset.minimal72tObserved) {
      observer.observe(overview, { childList: true });
      overview.dataset.minimal72tObserved = "1";
    }

    const chartTf = document.getElementById("chartTimeframe");
    if (chartTf && ![...chartTf.options].some((option) => option.value === "4h")) {
      const option = document.createElement("option");
      option.value = "4h";
      option.textContent = "4 hour";
      chartTf.appendChild(option);
    }
    return root;
  }

  function marketMode(s) {
    const gc = (s?.markets || []).find((m) => String(m?.symbol || "").toUpperCase() === "GC") || {};
    return String(s?.runtime?.mode || gc?.mode || "WAITING").toUpperCase();
  }

  function goldMarket(s) {
    return (s?.markets || []).find((m) => String(m?.symbol || "").toUpperCase() === "GC") || {};
  }

  function money(value, signed = false) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    const amount = `$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (n < 0) return `-${amount}`;
    return signed && n > 0 ? `+${amount}` : amount;
  }

  function rValue(value, signed = true) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    const prefix = signed && n > 0 ? "+" : "";
    return `${prefix}${n.toFixed(2)}R`;
  }

  function percent(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${n.toFixed(1)}%` : "—";
  }

  function factor(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(2) : "—";
  }

  function pnlClass(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || Math.abs(n) < 1e-9) return "neutral";
    return n > 0 ? "positive" : "negative";
  }

  function marketTime(s, gc) {
    const value = s?.runtime?.market_time || gc?.received_at;
    if (!value) return "Waiting for Gold feed";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
  }

  function scannerNote(d) {
    if (!d) return "Waiting for this timeframe to begin a setup sequence.";
    const stage = String(d.stage || "WAITING").replaceAll("_", " ");
    if (stage === "SETUP READY") return "Sequence complete. Final execution and safety checks decide whether OTR trades it.";
    if (d.note) return String(d.note).slice(0, 145);
    const next = STEPS.find(([, key]) => !d[key]);
    return next ? `Waiting for ${next[0].toLowerCase()} confirmation.` : "Final checks in progress.";
  }

  function scannerCard(tf, diagnostic) {
    const d = diagnostic || {};
    const done = STEPS.reduce((sum, [, key]) => sum + Number(Boolean(d[key])), 0);
    const pct = Math.round(done / STEPS.length * 100);
    const direction = String(d.direction || "waiting").toLowerCase();
    const stage = String(d.stage || "WAITING").replaceAll("_", " ");
    return `
      <article class="otr-scanner-card">
        <div class="otr-scanner-top">
          <div><span class="otr-label">Gold setup</span><div class="otr-tf">${esc(tf)}</div></div>
          <span class="otr-direction ${esc(direction)}">${esc(direction)}</span>
        </div>
        <div class="otr-progress-row">
          <div class="otr-progress"><i style="width:${pct}%"></i></div>
          <span class="otr-progress-count">${done}/${STEPS.length}</span>
        </div>
        <div class="otr-steps">${STEPS.map(([label, key]) => `<span class="otr-step ${d[key] ? "done" : ""}">${label}</span>`).join("")}</div>
        <div class="otr-stage">${esc(stage)}</div>
        <div class="otr-note">${esc(scannerNote(d))}</div>
      </article>`;
  }

  function render() {
    const root = setupShell();
    if (!root || !snapshot) return;
    const gc = goldMarket(snapshot);
    const mode = marketMode(snapshot);
    const stats = snapshot?.stats || {};
    const diagnostics = (snapshot?.diagnostics || []).filter((d) => String(d?.symbol || "").toUpperCase() === "GC");
    const byTf = new Map(diagnostics.map((d) => [String(d.timeframe || "").toLowerCase(), d]));
    const macro = training?.macro_4h || { direction: "unknown", note: "4H context is warming up." };
    const macroDirection = String(macro.direction || "unknown").toLowerCase();
    const runId = training?.run_id || snapshot?.evaluation?.verify_run?.run_id || "—";

    root.innerHTML = `
      <div class="otr-status-grid">
        <article class="otr-status-card primary">
          <div><span class="otr-label">Market mode</span><div class="otr-mode" data-mode="${esc(mode)}">${esc(mode)}</div><span class="otr-sub">${esc(marketTime(snapshot, gc))}</span></div>
          <div><span class="otr-label">Current test</span><div class="otr-sub">${esc(runId)}</div></div>
        </article>
        <article class="otr-status-card"><span class="otr-label">Gold</span><div class="otr-value">${money(gc.price)}</div><span class="otr-sub">GC / MGC focus</span></article>
        <article class="otr-status-card"><span class="otr-label">4H direction</span><div class="otr-value otr-macro-direction ${esc(macroDirection)}">${esc(macroDirection)}</div><span class="otr-sub">Macro context only</span></article>
        <article class="otr-status-card"><span class="otr-label">Feed</span><div class="otr-value">${gc?.age_seconds !== undefined && gc?.age_seconds !== null ? `${Number(gc.age_seconds).toFixed(1)}s` : "—"}</div><span class="otr-sub">Bridge ingress age</span></article>
      </div>

      <div class="otr-performance-grid">
        <article class="otr-performance-card">
          <span class="otr-label">Daily P&amp;L</span>
          <div class="otr-performance-value ${pnlClass(stats.today_dollars)}">${money(stats.today_dollars, true)}</div>
          <span class="otr-sub">${rValue(stats.today_r)} today</span>
        </article>
        <article class="otr-performance-card">
          <span class="otr-label">All-Time P&amp;L</span>
          <div class="otr-performance-value ${pnlClass(stats.total_dollars)}">${money(stats.total_dollars, true)}</div>
          <span class="otr-sub">${Number(stats.closed || 0)} closed Gold trade${Number(stats.closed || 0) === 1 ? "" : "s"}</span>
        </article>
        <article class="otr-performance-card">
          <span class="otr-label">Net R</span>
          <div class="otr-performance-value ${pnlClass(stats.total_r)}">${rValue(stats.total_r)}</div>
          <span class="otr-sub">Profit factor ${factor(stats.profit_factor)}</span>
        </article>
        <article class="otr-performance-card">
          <span class="otr-label">Win Rate</span>
          <div class="otr-performance-value">${percent(stats.win_rate)}</div>
          <span class="otr-sub">${Number(stats.wins || 0)}W / ${Number(stats.losses || 0)}L</span>
        </article>
      </div>

      <div class="otr-section-head">
        <div><span class="otr-label">Setup progress</span><h2>Gold Scanner</h2></div>
        <p>How far each execution timeframe is through the sequence.</p>
      </div>
      <div class="otr-scanner-grid">${TF_ORDER.map((tf) => scannerCard(tf, byTf.get(tf))).join("")}</div>

      <article class="otr-macro-card">
        <div class="otr-macro-badge">4H</div>
        <div><span class="otr-label">Macro direction</span><div class="otr-macro-direction ${esc(macroDirection)}">${esc(macroDirection)}</div><div class="otr-note">${esc(macro.note || "4H context only. It does not create 4H trades.")}</div></div>
        <a class="otr-lab-link" href="/market/research">Open Strategy Lab</a>
      </article>`;
  }

  async function fetchJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${response.status}`);
    return response.json();
  }

  async function tick() {
    if (stopped) return;
    try {
      snapshot = await fetchJson(`/market/api/snapshot?minimal72t=${Date.now()}`);
      render();
    } catch (error) {
      const root = setupShell();
      if (root && !snapshot) root.innerHTML = `<div class="otr-error">Gold monitor is waiting for the dashboard snapshot.</div>`;
    }
    setTimeout(tick, 1200);
  }

  async function trainingTick() {
    if (stopped) return;
    try {
      training = await fetchJson(`/market/api/training?minimal72t=${Date.now()}`);
      render();
    } catch (_) {}
    setTimeout(trainingTick, 5000);
  }

  function start() {
    setupShell();
    tick();
    trainingTick();
  }

  window.addEventListener("beforeunload", () => { stopped = true; });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
