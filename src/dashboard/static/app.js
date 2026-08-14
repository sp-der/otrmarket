const state = {
  snapshot: null,
  ws: null,
  reconnectTimer: null,
  authRequired: false,
};

const $ = (id) => document.getElementById(id);
const labelMap = { "BTC-USD": "Bitcoin", QQQ: "Nasdaq", SPY: "S&P 500" };

function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtPrice(value) {
  if (value === null || value === undefined) return "--";
  const n = Number(value);
  const digits = n >= 100 ? 2 : 4;
  return "$" + n.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtPct(value) {
  if (value === null || value === undefined) return "--";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(3)}%`;
}

function fmtR(value) {
  if (value === null || value === undefined) return "--";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}R`;
}

function valueClass(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "positive" : "negative";
}

function fmtTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return "Waiting";
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms ago`;
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

function statusChip(status, result) {
  const value = (result || status || "--").toString();
  const cls = value.toLowerCase().replace(/[^a-z]+/g, "-");
  return `<span class="status-chip ${cls}">${value}</span>`;
}

function updateClock() {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  $("clockDate").textContent = now.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function renderMarkets(markets) {
  const root = $("marketGrid");
  if (!markets || markets.length === 0) {
    root.innerHTML = '<div class="empty-state">No market quotes stored yet.</div>';
    return;
  }
  root.innerHTML = markets.map((m) => {
    const live = m.age_seconds !== null && m.age_seconds < 20;
    return `
      <article class="market-card">
        <div class="market-top">
          <div><div class="market-name">${m.name || labelMap[m.symbol] || m.symbol}</div><div class="market-symbol">${m.symbol}</div></div>
          <span class="feed-tag ${live ? "live" : ""}">${live ? "LIVE" : fmtAge(m.age_seconds)}</span>
        </div>
        <div class="market-price">${fmtPrice(m.price)}</div>
        <div class="market-stats">
          <div class="market-stat"><span>1 minute</span><strong class="${valueClass(m.return_1m)}">${fmtPct(m.return_1m)}</strong></div>
          <div class="market-stat"><span>5 minutes</span><strong class="${valueClass(m.return_5m)}">${fmtPct(m.return_5m)}</strong></div>
          <div class="market-stat"><span>Bid / Ask</span><strong>${fmtPrice(m.bid)} / ${fmtPrice(m.ask)}</strong></div>
          <div class="market-stat"><span>Quotes</span><strong>${Number(m.quote_count || 0).toLocaleString()}</strong></div>
        </div>
      </article>`;
  }).join("");
}

function renderMetrics(stats, setupCounts) {
  $("totalR").textContent = fmtR(stats.total_r);
  $("totalR").className = `metric-value ${valueClass(stats.total_r)}`;
  $("closedTradesSub").textContent = `${stats.closed || 0} closed paper trade${stats.closed === 1 ? "" : "s"}`;
  $("winRate").textContent = stats.win_rate === null ? "--" : `${fmtNumber(stats.win_rate, 1)}%`;
  $("winLossSub").textContent = `${stats.wins || 0} wins / ${stats.losses || 0} losses`;
  $("profitFactor").textContent = stats.profit_factor === null ? "--" : fmtNumber(stats.profit_factor, 2);
  $("avgRSub").textContent = `Average R ${fmtR(stats.avg_r)}`;
  $("maxDrawdown").textContent = fmtR(-(Number(stats.max_drawdown_r || 0)));
  $("todayRSub").textContent = `Today ${fmtR(stats.today_r)}`;
  $("pendingCount").textContent = stats.pending || 0;
  $("openCount").textContent = stats.open || 0;
  $("setupCount").textContent = setupCounts?.total || 0;
  $("invalidatedCount").textContent = stats.invalidated || 0;
}

function tradeRow(t, compact = false) {
  const cols = [
    `<td>${labelMap[t.symbol] || t.symbol}</td>`,
    `<td>${t.timeframe || "--"}</td>`,
    `<td>${t.direction || "--"}</td>`,
    `<td>${statusChip(t.status, null)}</td>`,
    `<td>${fmtPrice(t.entry_price)}</td>`,
  ];
  if (!compact) {
    cols.push(`<td>${fmtPrice(t.stop_price)}</td>`);
    cols.push(`<td>${fmtPrice(t.target_price)}</td>`);
  }
  cols.push(`<td>${fmtPrice(t.exit_price)}</td>`);
  cols.push(`<td>${statusChip(t.status, t.result)}</td>`);
  cols.push(`<td class="${valueClass(t.result_r)}">${fmtR(t.result_r)}</td>`);
  if (compact) cols.push(`<td>${fmtTime(t.updated_at)}</td>`);
  else {
    cols.push(`<td>${fmtTime(t.opened_at)}</td>`);
    cols.push(`<td>${fmtTime(t.closed_at)}</td>`);
  }
  return `<tr>${cols.join("")}</tr>`;
}

function renderTrades(trades) {
  const recent = (trades || []).slice(0, 8);
  $("overviewTradesBody").innerHTML = recent.length
    ? recent.map((t) => tradeRow(t, true)).join("")
    : '<tr><td colspan="9" class="empty-state">No paper trades recorded yet.</td></tr>';

  const symbolFilter = $("tradeSymbolFilter").value;
  const resultFilter = $("tradeResultFilter").value;
  const filtered = (trades || []).filter((t) => {
    const symbolOk = symbolFilter === "all" || t.symbol === symbolFilter;
    const marker = t.result || t.status;
    const resultOk = resultFilter === "all" || marker === resultFilter;
    return symbolOk && resultOk;
  });
  $("tradesBody").innerHTML = filtered.length
    ? filtered.map((t) => tradeRow(t, false)).join("")
    : '<tr><td colspan="12" class="empty-state">No trades match these filters.</td></tr>';
}

function setupCard(s) {
  const rr = s.risk_reward === null || s.risk_reward === undefined ? "--" : `${Number(s.risk_reward).toFixed(2)}R`;
  const trigger = (s.trigger_type || "--").replaceAll("_", " ");
  return `
    <article class="setup-card">
      <div class="setup-head">
        <div><div class="setup-title">${labelMap[s.symbol] || s.symbol} · ${String(s.direction || "").toUpperCase()}</div><div class="setup-meta">${s.timeframe} · ${trigger} · ${fmtTime(s.created_at)}</div></div>
        ${statusChip(s.status, null)}
      </div>
      <div class="setup-prices">
        <div class="setup-price"><span>Entry</span><strong>${fmtPrice(s.entry_price)}</strong></div>
        <div class="setup-price"><span>Stop</span><strong>${fmtPrice(s.stop_price)}</strong></div>
        <div class="setup-price"><span>Target</span><strong>${fmtPrice(s.target_price)}</strong></div>
        <div class="setup-price"><span>R:R</span><strong>${rr}</strong></div>
      </div>
      <div class="confluence-line">PD array → ${trigger} → displacement → entry FVG</div>
    </article>`;
}

function renderSetups(setups) {
  const latest = setups?.[0];
  if (!latest) {
    $("latestSetup").className = "latest-card empty-state";
    $("latestSetup").textContent = "No strategy setups yet.";
  } else {
    $("latestSetup").className = "latest-card";
    $("latestSetup").innerHTML = `
      <strong>${labelMap[latest.symbol] || latest.symbol} · ${String(latest.direction).toUpperCase()}</strong>
      <div class="latest-row"><span>Trigger</span><strong>${String(latest.trigger_type).replaceAll("_", " ")}</strong></div>
      <div class="latest-row"><span>Entry</span><strong>${fmtPrice(latest.entry_price)}</strong></div>
      <div class="latest-row"><span>Risk / Reward</span><strong>${Number(latest.risk_reward || 0).toFixed(2)}R</strong></div>`;
  }

  const symbolFilter = $("setupSymbolFilter").value;
  const triggerFilter = $("setupTriggerFilter").value;
  const filtered = (setups || []).filter((s) => {
    const symbolOk = symbolFilter === "all" || s.symbol === symbolFilter;
    const triggerOk = triggerFilter === "all" || s.trigger_type === triggerFilter;
    return symbolOk && triggerOk;
  });
  $("setupCards").innerHTML = filtered.length ? filtered.map(setupCard).join("") : '<div class="empty-state">No setups match these filters.</div>';
}

function renderSystem(snapshot) {
  const db = snapshot.database || {};
  $("dbStatus").textContent = db.ok ? "ONLINE" : "MISSING";
  $("dbStatus").className = db.ok ? "positive" : "negative";
  $("dbSize").textContent = db.size_bytes ? `${(db.size_bytes / 1024 / 1024).toFixed(2)} MB` : "0 MB";
  $("dbPath").textContent = db.path || "--";

  const candles = snapshot.candles || [];
  $("candleSummary").innerHTML = candles.length
    ? candles.map((c) => `<div><span>${labelMap[c.symbol] || c.symbol} · ${c.timeframe}</span><strong>${Number(c.count).toLocaleString()} · ${fmtTime(c.latest)}</strong></div>`).join("")
    : '<div><span>Candles</span><strong>Waiting for completed candles</strong></div>';
}

function drawEquity(points) {
  const canvas = $("equityCanvas");
  const empty = $("chartEmpty");
  const usable = (points || []).filter((p) => Number.isFinite(Number(p.equity_r)));
  if (usable.length < 2) {
    empty.classList.remove("hidden");
    canvas.classList.add("hidden");
    return;
  }
  empty.classList.add("hidden");
  canvas.classList.remove("hidden");

  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(rect.width * dpr));
  canvas.height = Math.max(1, Math.round(rect.height * dpr));
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  const w = rect.width, h = rect.height;
  const pad = { l: 38, r: 12, t: 16, b: 25 };
  const vals = usable.map((p) => Number(p.equity_r));
  let min = Math.min(...vals, 0), max = Math.max(...vals, 0);
  if (max === min) { max += 1; min -= 1; }
  const x = (i) => pad.l + (i / (usable.length - 1)) * (w - pad.l - pad.r);
  const y = (v) => pad.t + ((max - v) / (max - min)) * (h - pad.t - pad.b);

  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(255,255,255,.10)";
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) {
    const gy = pad.t + (i / 3) * (h - pad.t - pad.b);
    ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(w - pad.r, gy); ctx.stroke();
  }

  const zeroY = y(0);
  ctx.strokeStyle = "rgba(255,255,255,.20)";
  ctx.beginPath(); ctx.moveTo(pad.l, zeroY); ctx.lineTo(w - pad.r, zeroY); ctx.stroke();

  const gradient = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
  gradient.addColorStop(0, "rgba(255,255,255,.18)");
  gradient.addColorStop(1, "rgba(255,255,255,0)");
  ctx.beginPath();
  usable.forEach((p, i) => { const px = x(i), py = y(Number(p.equity_r)); if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py); });
  ctx.lineTo(x(usable.length - 1), h - pad.b); ctx.lineTo(x(0), h - pad.b); ctx.closePath(); ctx.fillStyle = gradient; ctx.fill();

  ctx.beginPath();
  usable.forEach((p, i) => { const px = x(i), py = y(Number(p.equity_r)); if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py); });
  ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2; ctx.stroke();

  ctx.fillStyle = "#9a9a9a"; ctx.font = "10px system-ui"; ctx.textAlign = "right";
  ctx.fillText(`${max.toFixed(1)}R`, pad.l - 7, y(max) + 3);
  ctx.fillText(`${min.toFixed(1)}R`, pad.l - 7, y(min) + 3);
  ctx.textAlign = "left"; ctx.fillText(`${usable.length - 1} closed trades`, pad.l, h - 7);
}

function render(snapshot) {
  state.snapshot = snapshot;
  renderMetrics(snapshot.stats || {}, snapshot.setup_counts || {});
  renderMarkets(snapshot.markets || []);
  renderTrades(snapshot.trades || []);
  renderSetups(snapshot.setups || []);
  renderSystem(snapshot);
  drawEquity(snapshot.equity_curve || []);
  $("generatedAt").textContent = snapshot.generated_at ? `Updated ${fmtTime(snapshot.generated_at)}` : "Waiting for data";
}

function setConnection(status) {
  const dot = $("connectionDot");
  dot.classList.remove("live", "offline");
  if (status === "live") dot.classList.add("live");
  if (status === "offline") dot.classList.add("offline");
  $("connectionText").textContent = status === "live" ? "Live dashboard" : status === "offline" ? "Reconnecting" : "Connecting";
}

async function fetchAuthStatus() {
  const response = await fetch("/market/api/auth-status", { credentials: "same-origin" });
  const data = await response.json();
  state.authRequired = data.required;
  if (data.required && !data.authenticated) {
    $("loginOverlay").classList.remove("hidden");
    $("logoutButton").classList.add("hidden");
    return false;
  }
  $("loginOverlay").classList.add("hidden");
  $("logoutButton").classList.toggle("hidden", !data.required);
  return true;
}

async function login() {
  const password = $("passwordInput").value;
  $("loginError").textContent = "";
  try {
    const response = await fetch("/market/api/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (!response.ok) throw new Error("Incorrect password");
    $("passwordInput").value = "";
    $("loginOverlay").classList.add("hidden");
    $("logoutButton").classList.remove("hidden");
    connectWebSocket();
  } catch (err) {
    $("loginError").textContent = err.message || "Login failed";
  }
}

async function logout() {
  await fetch("/market/api/logout", { method: "POST", credentials: "same-origin" });
  if (state.ws) state.ws.close();
  await fetchAuthStatus();
}

function connectWebSocket() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) return;
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${proto}//${location.host}/market/ws`);
  state.ws = socket;
  setConnection("connecting");

  socket.addEventListener("open", () => setConnection("live"));
  socket.addEventListener("message", (event) => {
    try { render(JSON.parse(event.data)); } catch (_) {}
  });
  socket.addEventListener("close", async (event) => {
    setConnection("offline");
    if (event.code === 4401) {
      await fetchAuthStatus();
      return;
    }
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(connectWebSocket, 2500);
  });
}

function switchView(name) {
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
  $(`${name}View`).classList.add("active");
  $("viewTitle").textContent = name.charAt(0).toUpperCase() + name.slice(1);
  if (name === "overview" && state.snapshot) requestAnimationFrame(() => drawEquity(state.snapshot.equity_curve || []));
}

function bindEvents() {
  document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  document.querySelectorAll("[data-jump]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.jump)));
  ["tradeSymbolFilter", "tradeResultFilter"].forEach((id) => $(id).addEventListener("change", () => state.snapshot && renderTrades(state.snapshot.trades || [])));
  ["setupSymbolFilter", "setupTriggerFilter"].forEach((id) => $(id).addEventListener("change", () => state.snapshot && renderSetups(state.snapshot.setups || [])));
  $("loginButton").addEventListener("click", login);
  $("passwordInput").addEventListener("keydown", (event) => { if (event.key === "Enter") login(); });
  $("logoutButton").addEventListener("click", logout);
  window.addEventListener("resize", () => state.snapshot && drawEquity(state.snapshot.equity_curve || []));
}

async function boot() {
  bindEvents();
  updateClock();
  setInterval(updateClock, 1000);
  try {
    if (await fetchAuthStatus()) connectWebSocket();
  } catch (_) {
    setConnection("offline");
    setTimeout(boot, 2500);
  }
}

boot();
