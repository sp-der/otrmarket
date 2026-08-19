(() => {
  const API_PATH = "/market/api/snapshot";
  const NY_TZ = "America/New_York";
  const weekdays = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
  const months = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];

  let cursorYear = null;
  let cursorMonth = null;
  let manualMonth = false;
  let activeTab = "pnl";
  let latestSnapshot = null;
  let pollTimer = null;

  function money(value, signed = false, digits = 0) {
    const n = Number(value);
    if (!Number.isFinite(n)) return "--";
    const abs = Math.abs(n).toLocaleString(undefined, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
    if (n < 0) return `-$${abs}`;
    return `${signed && n > 0 ? "+" : ""}$${abs}`;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function dateParts(value) {
    const date = value ? new Date(value) : new Date();
    if (Number.isNaN(date.getTime())) return null;
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: NY_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(date);
    const get = (type) => Number(parts.find((part) => part.type === type)?.value);
    return { year: get("year"), month: get("month") - 1, day: get("day") };
  }

  function dateKey(value) {
    const parts = dateParts(value);
    if (!parts) return null;
    return `${parts.year}-${String(parts.month + 1).padStart(2, "0")}-${String(parts.day).padStart(2, "0")}`;
  }

  function ensureUi() {
    if (document.getElementById("lucidEvalWrap")) return true;
    const guard = document.querySelector("#overviewView .prop-guard-panel");
    if (!guard) return false;

    guard.insertAdjacentHTML("afterend", `
      <div id="lucidEvalWrap" class="lucid-eval-wrap">
        <section class="lucid-progress-card" aria-label="Evaluation progress">
          <div class="lucid-progress-track">
            <span id="lucidProgressFill" class="lucid-progress-fill"></span>
            <span id="lucidStartMarker" class="lucid-start-marker"></span>
          </div>
          <div class="lucid-progress-labels">
            <div class="lucid-progress-left"><span id="lucidMll" class="lucid-value-line loss">-- <small>MLL</small></span></div>
            <div></div>
            <div class="lucid-progress-right"><span id="lucidTarget" class="lucid-value-line win">-- <small>TARGET</small></span></div>
          </div>
          <div class="lucid-progress-bottom">
            <div class="lucid-progress-left"><span id="lucidCushion" class="lucid-value-line">-- <small>cushion</small></span></div>
            <div class="lucid-progress-center"><span id="lucidBalance" class="lucid-balance-main">-- <small>balance</small></span></div>
            <div class="lucid-progress-right"><span id="lucidRemaining" class="lucid-value-line">-- <small>remaining</small></span></div>
          </div>
        </section>

        <section class="lucid-calendar-panel">
          <div class="lucid-calendar-head">
            <div class="lucid-calendar-title-row">
              <div>
                <div class="section-kicker">REPLAY EVALUATION</div>
                <h2>Trading Calendar</h2>
              </div>
              <div class="lucid-tabs" role="tablist" aria-label="Trading calendar mode">
                <button id="lucidPnlTab" class="lucid-tab active" type="button" data-lucid-tab="pnl">PNL</button>
                <button id="lucidEventsTab" class="lucid-tab" type="button" data-lucid-tab="events">Events</button>
              </div>
            </div>
            <div class="lucid-month-nav">
              <button id="lucidPrevMonth" class="lucid-nav-button" type="button" aria-label="Previous month">‹</button>
              <div id="lucidMonthLabel" class="lucid-month-label">--</div>
              <button id="lucidNextMonth" class="lucid-nav-button" type="button" aria-label="Next month">›</button>
            </div>
          </div>
          <div id="lucidCalendarGrid" class="lucid-calendar-grid"></div>
          <div id="lucidEventsPlaceholder" class="lucid-events-placeholder lucid-hidden">
            Event overlays are reserved for the economic/news filter. PNL remains the source of truth for this replay test.
          </div>
        </section>
      </div>
    `);

    document.getElementById("lucidPrevMonth")?.addEventListener("click", () => shiftMonth(-1));
    document.getElementById("lucidNextMonth")?.addEventListener("click", () => shiftMonth(1));
    document.querySelectorAll("[data-lucid-tab]").forEach((button) => {
      button.addEventListener("click", () => setTab(button.dataset.lucidTab));
    });
    return true;
  }

  function shiftMonth(delta) {
    if (cursorYear === null || cursorMonth === null) return;
    const next = new Date(Date.UTC(cursorYear, cursorMonth + delta, 1));
    cursorYear = next.getUTCFullYear();
    cursorMonth = next.getUTCMonth();
    manualMonth = true;
    if (latestSnapshot) renderCalendar(latestSnapshot);
  }

  function setTab(tab) {
    activeTab = tab === "events" ? "events" : "pnl";
    document.getElementById("lucidPnlTab")?.classList.toggle("active", activeTab === "pnl");
    document.getElementById("lucidEventsTab")?.classList.toggle("active", activeTab === "events");
    document.getElementById("lucidCalendarGrid")?.classList.toggle("lucid-hidden", activeTab !== "pnl");
    document.getElementById("lucidEventsPlaceholder")?.classList.toggle("lucid-hidden", activeTab !== "events");
  }

  function renderProgress(snapshot) {
    const e = snapshot?.evaluation || {};
    const start = Number(e.starting_balance || 50000);
    const balance = Number(e.balance ?? start);
    const floor = Number(e.mll_floor ?? (start - 2000));
    const targetBalance = start + Number(e.profit_target || 3000);
    const cushion = Number(e.mll_cushion ?? (balance - floor));
    const remaining = targetBalance - balance;
    const range = Math.max(1, targetBalance - floor);
    const startPct = clamp(((start - floor) / range) * 100, 0, 100);
    const balancePct = clamp(((balance - floor) / range) * 100, 0, 100);

    const set = (id, html) => {
      const node = document.getElementById(id);
      if (node) node.innerHTML = html;
    };

    set("lucidMll", `${money(floor)} <small>MLL</small>`);
    set("lucidTarget", `${money(targetBalance)} <small>TARGET</small>`);
    set("lucidCushion", `${money(cushion)} <small>cushion</small>`);
    set("lucidBalance", `${money(balance)} <small>balance</small>`);

    if (remaining > 0) {
      set("lucidRemaining", `${money(remaining)} <small>remaining</small>`);
    } else {
      set("lucidRemaining", `${money(Math.abs(remaining), true)} <small>beyond target</small>`);
      document.getElementById("lucidRemaining")?.classList.add("win");
    }

    const fill = document.getElementById("lucidProgressFill");
    const marker = document.getElementById("lucidStartMarker");
    if (fill) fill.style.width = `${balancePct}%`;
    if (marker) marker.style.left = `${startPct}%`;
  }

  function groupTrades(trades) {
    const days = new Map();
    (trades || []).forEach((trade) => {
      if (trade.status !== "CLOSED" || !trade.closed_at) return;
      const key = dateKey(trade.closed_at);
      if (!key) return;
      if (!days.has(key)) days.set(key, { pnl: 0, closed: 0, wins: 0, losses: 0 });
      const day = days.get(key);
      day.pnl += Number(trade.display_result_dollars || 0);
      day.closed += 1;
      if (trade.result === "WIN") day.wins += 1;
      if (trade.result === "LOSS") day.losses += 1;
    });
    return days;
  }

  function groupDailyRealized(rows) {
    const days = new Map();
    (rows || []).forEach((row) => {
      const key = String(row?.date || "");
      if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return;
      days.set(key, {
        pnl: Number(row.pnl || 0),
        closed: Number(row.closed || 0),
        wins: Number(row.wins || 0),
        losses: Number(row.losses || 0),
      });
    });
    return days;
  }

  function renderCalendar(snapshot) {
    const reference = snapshot?.runtime?.market_time || snapshot?.evaluation?.reference_time || snapshot?.generated_at;
    const refParts = dateParts(reference);
    if (!refParts) return;

    if (cursorYear === null || cursorMonth === null || !manualMonth) {
      cursorYear = refParts.year;
      cursorMonth = refParts.month;
    }

    const label = document.getElementById("lucidMonthLabel");
    if (label) label.textContent = `${months[cursorMonth]} ${cursorYear}`;

    const grid = document.getElementById("lucidCalendarGrid");
    if (!grid) return;

    const fullLedger = snapshot?.daily_realized_pnl;
    const grouped = Array.isArray(fullLedger)
      ? groupDailyRealized(fullLedger)
      : groupTrades(snapshot?.trades || []);
    const firstDay = new Date(Date.UTC(cursorYear, cursorMonth, 1)).getUTCDay();
    const daysInMonth = new Date(Date.UTC(cursorYear, cursorMonth + 1, 0)).getUTCDate();
    const todayKey = `${refParts.year}-${String(refParts.month + 1).padStart(2, "0")}-${String(refParts.day).padStart(2, "0")}`;

    const cells = weekdays.map((day) => `<div class="lucid-weekday">${day}</div>`);
    for (let i = 0; i < firstDay; i += 1) cells.push('<div class="lucid-day empty" aria-hidden="true"></div>');

    for (let day = 1; day <= daysInMonth; day += 1) {
      const key = `${cursorYear}-${String(cursorMonth + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      const stats = grouped.get(key);
      const winRate = stats?.closed ? Math.round((stats.wins / stats.closed) * 100) : null;
      const signClass = stats ? (stats.pnl > 0 ? "positive" : stats.pnl < 0 ? "negative" : "") : "";
      const hasPnl = stats ? "has-pnl" : "";
      const today = key === todayKey ? "today" : "";
      cells.push(`
        <div class="lucid-day ${hasPnl} ${signClass} ${today}">
          <div class="lucid-day-number">${day}</div>
          ${stats ? `<div class="lucid-day-pnl">${money(stats.pnl, true, 2)}</div>` : ""}
          ${stats ? `<div class="lucid-day-meta">${winRate}% wins · ${stats.closed} trade${stats.closed === 1 ? "" : "s"}</div>` : ""}
        </div>
      `);
    }

    const totalCells = firstDay + daysInMonth;
    const trailing = (7 - (totalCells % 7)) % 7;
    for (let i = 0; i < trailing; i += 1) cells.push('<div class="lucid-day empty" aria-hidden="true"></div>');
    grid.innerHTML = cells.join("");
  }

  function tradeTimestamp(value) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function tradeDuration(openedAt, closedAt) {
    if (!openedAt || !closedAt) return "--";
    const opened = new Date(openedAt);
    const closed = new Date(closedAt);
    if (Number.isNaN(opened.getTime()) || Number.isNaN(closed.getTime())) return "--";
    const totalSeconds = Math.max(0, Math.round((closed.getTime() - opened.getTime()) / 1000));
    if (totalSeconds < 60) return `${totalSeconds}s`;
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    if (hours > 0) return `${hours}h ${minutes}m ${seconds}s`;
    return `${minutes}m ${seconds}s`;
  }

  function installTradeTimingUi() {
    const header = document.querySelector("#tradesView table thead tr");
    if (header && !header.querySelector("[data-trade-duration]")) {
      header.insertAdjacentHTML("beforeend", '<th data-trade-duration="true">Duration</th>');
    }

    tradeRow = function timedTradeRow(t, compact = false) {
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
      cols.push(`<td class="${pnlClass(t.result_r)}">${fmtR(t.result_r)}</td>`);
      cols.push(`<td class="pnl-cell ${pnlClass(t.display_result_dollars)}">${fmtMoney(t.display_result_dollars, true)}</td>`);
      if (compact) {
        cols.push(`<td>${fmtTime(t.updated_at)}</td>`);
      } else {
        cols.push(`<td>${tradeTimestamp(t.opened_at)}</td>`);
        cols.push(`<td>${tradeTimestamp(t.closed_at)}</td>`);
        cols.push(`<td>${tradeDuration(t.opened_at, t.closed_at)}</td>`);
      }
      return `<tr>${cols.join("")}</tr>`;
    };

    renderTrades = function timedRenderTrades(trades) {
      const recent = (trades || []).slice(0, 8);
      $("overviewTradesBody").innerHTML = recent.length
        ? recent.map((t) => tradeRow(t, true)).join("")
        : '<tr><td colspan="10" class="empty-state">No paper trades recorded yet.</td></tr>';

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
        : '<tr><td colspan="14" class="empty-state">No trades match these filters.</td></tr>';
    };

    if (state?.snapshot) renderTrades(state.snapshot.trades || []);
  }

  function render(snapshot) {
    latestSnapshot = snapshot;
    if (!ensureUi()) return;
    renderProgress(snapshot);
    renderCalendar(snapshot);
    setTab(activeTab);
  }

  async function poll() {
    try {
      const response = await fetch(API_PATH, { credentials: "same-origin", cache: "no-store" });
      if (response.ok) render(await response.json());
    } catch (_) {
      // The primary dashboard websocket already owns connection-status UI.
    } finally {
      pollTimer = window.setTimeout(poll, 2000);
    }
  }

  function start() {
    installTradeTimingUi();
    ensureUi();
    if (pollTimer) window.clearTimeout(pollTimer);
    poll();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();