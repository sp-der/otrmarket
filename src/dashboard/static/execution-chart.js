(() => {
  "use strict";

  const chartState = {
    active: false,
    loading: false,
    timer: null,
    primary: null,
    pair: null,
  };

  const byId = (id) => document.getElementById(id);
  const numeric = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const timeValue = (value) => {
    if (!value) return null;
    const parsed = new Date(value).getTime();
    return Number.isFinite(parsed) ? parsed : null;
  };
  const priceText = (value) => {
    const parsed = numeric(value);
    if (parsed === null) return "--";
    return parsed.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  };
  const shortTime = (value) => {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value || "--";
    return parsed.toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  };
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

  function nearestIndex(candles, rawTime) {
    const target = timeValue(rawTime);
    if (target === null || !candles.length) return candles.length - 1;
    let bestIndex = 0;
    let bestDistance = Infinity;
    candles.forEach((candle, index) => {
      const current = timeValue(candle.close_time || candle.open_time);
      if (current === null) return;
      const distance = Math.abs(current - target);
      if (distance < bestDistance) {
        bestDistance = distance;
        bestIndex = index;
      }
    });
    return bestIndex;
  }

  function zoneBounds(zone) {
    if (!zone || typeof zone !== "object") return null;
    const low = numeric(zone.lower ?? zone.low ?? zone.zone_overlap_low ?? zone.body_low);
    const high = numeric(zone.upper ?? zone.high ?? zone.zone_overlap_high ?? zone.body_high);
    if (low === null || high === null) return null;
    return { low: Math.min(low, high), high: Math.max(low, high) };
  }

  function chartRange(data) {
    const prices = [];
    (data.candles || []).forEach((candle) => {
      [candle.low, candle.high].forEach((value) => {
        const parsed = numeric(value);
        if (parsed !== null) prices.push(parsed);
      });
    });
    (data.setups || []).forEach((setup) => {
      [setup.entry_price, setup.stop_price, setup.target_price].forEach((value) => {
        const parsed = numeric(value);
        if (parsed !== null) prices.push(parsed);
      });
      [setup.overlay?.fvg, setup.overlay?.order_block].forEach((zone) => {
        const bounds = zoneBounds(zone);
        if (bounds) prices.push(bounds.low, bounds.high);
      });
    });
    (data.trades || []).forEach((trade) => {
      [trade.entry_price, trade.stop_price, trade.target_price, trade.exit_price].forEach((value) => {
        const parsed = numeric(value);
        if (parsed !== null) prices.push(parsed);
      });
    });
    if (!prices.length) return null;
    let low = Math.min(...prices);
    let high = Math.max(...prices);
    const padding = Math.max((high - low) * 0.08, Math.abs(high || 1) * 0.00025);
    low -= padding;
    high += padding;
    if (low === high) high = low + 1;
    return { low, high };
  }

  function drawZone(ctx, candles, geometry, xFor, yFor, color, fill) {
    const bounds = zoneBounds(geometry);
    if (!bounds) return;
    const rawTime = geometry.formed_at || geometry.candle_time || geometry.time;
    const startIndex = Math.max(0, nearestIndex(candles, rawTime));
    const endIndex = Math.min(candles.length - 1, startIndex + Math.max(7, Math.round(candles.length * 0.1)));
    const x = xFor(startIndex);
    const right = xFor(endIndex);
    const yTop = yFor(bounds.high);
    const yBottom = yFor(bounds.low);
    ctx.fillStyle = fill;
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.fillRect(x, yTop, Math.max(3, right - x), Math.max(1, yBottom - yTop));
    ctx.strokeRect(x, yTop, Math.max(3, right - x), Math.max(1, yBottom - yTop));
  }

  function drawPriceLine(ctx, plot, yFor, value, color, label, dash = [4, 4]) {
    const parsed = numeric(value);
    if (parsed === null) return;
    const y = yFor(parsed);
    ctx.save();
    ctx.setLineDash(dash);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(plot.left, y);
    ctx.lineTo(plot.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.font = "8px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textAlign = "right";
    ctx.fillText(`${label} ${priceText(parsed)}`, plot.right - 3, Math.max(plot.top + 9, y - 3));
    ctx.restore();
  }

  function drawMarker(ctx, x, y, color, label, direction, filled = true) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.fillStyle = filled ? color : "#050505";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (direction === "bullish") {
      ctx.moveTo(x, y - 7);
      ctx.lineTo(x - 5, y + 3);
      ctx.lineTo(x + 5, y + 3);
    } else if (direction === "bearish") {
      ctx.moveTo(x, y + 7);
      ctx.lineTo(x - 5, y - 3);
      ctx.lineTo(x + 5, y - 3);
    } else {
      ctx.arc(x, y, 4, 0, Math.PI * 2);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.font = "bold 8px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textAlign = "center";
    ctx.fillText(label, x, direction === "bullish" ? y + 14 : y - 9);
    ctx.restore();
  }

  function drawChart(canvas, data, compact = false) {
    const candles = data?.candles || [];
    const bounds = chartRange(data || {});
    const parent = canvas.parentElement;
    const cssWidth = Math.max(1, parent.clientWidth);
    const cssHeight = Math.max(1, parent.clientHeight);
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(cssWidth * ratio);
    canvas.height = Math.round(cssHeight * ratio);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);
    canvas._executionData = null;
    if (!candles.length || !bounds) return;

    const plot = { left: 12, top: 18, right: cssWidth - 62, bottom: cssHeight - 26 };
    const plotWidth = Math.max(1, plot.right - plot.left);
    const plotHeight = Math.max(1, plot.bottom - plot.top);
    const xFor = (index) => plot.left + (candles.length === 1 ? plotWidth / 2 : (index / (candles.length - 1)) * plotWidth);
    const yFor = (price) => plot.bottom - ((price - bounds.low) / (bounds.high - bounds.low)) * plotHeight;

    ctx.strokeStyle = "#191919";
    ctx.fillStyle = "#747474";
    ctx.lineWidth = 1;
    ctx.font = "8px ui-monospace, SFMono-Regular, Menlo, monospace";
    for (let tick = 0; tick <= 5; tick += 1) {
      const y = plot.top + (tick / 5) * plotHeight;
      const price = bounds.high - (tick / 5) * (bounds.high - bounds.low);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
      ctx.textAlign = "left";
      ctx.fillText(priceText(price), plot.right + 6, y + 3);
    }

    (data.setups || []).slice(-40).forEach((setup) => {
      drawZone(ctx, candles, setup.overlay?.fvg, xFor, yFor, "#54879c", "rgba(84, 135, 156, .13)");
      drawZone(ctx, candles, setup.overlay?.order_block, xFor, yFor, "#a48858", "rgba(164, 136, 88, .14)");
    });

    const spacing = plotWidth / Math.max(1, candles.length - 1);
    const bodyWidth = Math.max(1, Math.min(compact ? 7 : 10, spacing * 0.64));
    candles.forEach((candle, index) => {
      const open = numeric(candle.open);
      const high = numeric(candle.high);
      const low = numeric(candle.low);
      const close = numeric(candle.close);
      if ([open, high, low, close].some((value) => value === null)) return;
      const rising = close >= open;
      const color = rising ? "#58ba84" : "#d76565";
      const x = xFor(index);
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, yFor(high));
      ctx.lineTo(x, yFor(low));
      ctx.stroke();
      const top = Math.min(yFor(open), yFor(close));
      const height = Math.max(1, Math.abs(yFor(open) - yFor(close)));
      ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, height);
    });

    const activeTrades = (data.trades || []).slice(-8);
    activeTrades.forEach((trade) => {
      drawPriceLine(ctx, plot, yFor, trade.entry_price, "#e7e7e7", "E");
      drawPriceLine(ctx, plot, yFor, trade.stop_price, "#d76565", "S");
      drawPriceLine(ctx, plot, yFor, trade.target_price, "#58ba84", "T");
      const entryIndex = nearestIndex(candles, trade.opened_at || trade.updated_at);
      const entryPrice = numeric(trade.entry_price);
      if (entryPrice !== null) drawMarker(ctx, xFor(entryIndex), yFor(entryPrice), "#fff", "IN", trade.direction);
      const exitPrice = numeric(trade.exit_price);
      if (exitPrice !== null && trade.closed_at) {
        const exitIndex = nearestIndex(candles, trade.closed_at);
        const won = String(trade.result || "").toUpperCase() === "WIN";
        drawMarker(ctx, xFor(exitIndex), yFor(exitPrice), won ? "#58ba84" : "#d76565", "OUT", null, false);
      }
    });

    (data.setups || []).slice(-24).forEach((setup) => {
      const index = nearestIndex(candles, setup.created_at);
      const entry = numeric(setup.entry_price);
      if (entry === null) return;
      const isSmt = String(setup.trigger_type || "").toLowerCase() === "smt";
      const isOb = String(setup.overlay?.entry_type || "").toUpperCase() === "ORDER_BLOCK";
      drawMarker(ctx, xFor(index), yFor(entry), isSmt ? "#d9a8ff" : "#b9b9b9", isSmt ? "SMT" : (isOb ? "OB" : "SET"), setup.direction, false);
    });

    const labelIndexes = [0, .33, .66, 1].map((part) => Math.round((candles.length - 1) * part));
    ctx.fillStyle = "#6e6e6e";
    ctx.font = "8px ui-monospace, SFMono-Regular, Menlo, monospace";
    labelIndexes.forEach((index, position) => {
      const candle = candles[index];
      const date = new Date(candle.close_time || candle.open_time);
      const label = Number.isNaN(date.getTime()) ? "--" : date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      ctx.textAlign = position === 0 ? "left" : (position === labelIndexes.length - 1 ? "right" : "center");
      ctx.fillText(label, xFor(index), cssHeight - 8);
    });

    canvas._executionData = { candles, xFor, plot, spacing };
  }

  function bindTooltip(canvas, tooltip) {
    const hide = () => tooltip.classList.add("hidden");
    canvas.addEventListener("mouseleave", hide);
    canvas.addEventListener("mousemove", (event) => {
      const drawing = canvas._executionData;
      if (!drawing) return hide();
      const rect = canvas.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const ratio = (x - drawing.plot.left) / Math.max(1, drawing.plot.right - drawing.plot.left);
      const index = Math.max(0, Math.min(drawing.candles.length - 1, Math.round(ratio * (drawing.candles.length - 1))));
      const candle = drawing.candles[index];
      tooltip.textContent = `${shortTime(candle.close_time || candle.open_time)}  O ${priceText(candle.open)}  H ${priceText(candle.high)}  L ${priceText(candle.low)}  C ${priceText(candle.close)}  ticks ${candle.ticks ?? 0}`;
      tooltip.classList.remove("hidden");
    });
  }

  function renderCanvasMeta(prefix, data, comparison = false) {
    const candles = data?.candles || [];
    const last = candles.at(-1);
    byId(`${prefix}ChartName`).textContent = data?.symbol || "--";
    byId(`${prefix}ChartMeta`).textContent = `${comparison ? "SMT comparison · " : ""}${data?.timeframe || "--"} · ${candles.length} bars`;
    byId(`${prefix}ChartPrice`).textContent = priceText(last?.close);
    byId(`${prefix}ChartEmpty`).classList.toggle("hidden", candles.length > 0);
  }

  function statusClass(element, state, positiveStates = []) {
    element.className = "chart-status-chip";
    const normalized = String(state || "").toUpperCase();
    if (positiveStates.includes(normalized)) element.classList.add(normalized === "REPLAY" ? "is-replay" : (normalized === "SYNCED" ? "is-synced" : "is-live"));
    if (normalized === "STALE" || normalized === "WAITING") element.classList.add("is-stale");
    if (normalized === "PAIR LAG") element.classList.add("is-lagging");
  }

  function renderStatus(data) {
    const runtimeMode = String(data.runtime?.mode || "IDLE").toUpperCase();
    const feedState = String(data.feed?.state || "WAITING").toUpperCase();
    const runtime = byId("chartRuntime");
    const feed = byId("chartFeed");
    const sync = byId("chartSync");
    runtime.textContent = runtimeMode;
    feed.textContent = `FEED ${feedState}`;
    statusClass(runtime, runtimeMode, ["LIVE", "REPLAY"]);
    statusClass(feed, feedState, ["LIVE", "REPLAY"]);
    if (!data.pair) {
      sync.textContent = "PAIR N/A";
      statusClass(sync, "N/A", []);
    } else if (data.pair.synchronized) {
      sync.textContent = "PAIR SYNCED";
      statusClass(sync, "SYNCED", ["SYNCED"]);
    } else {
      const delta = numeric(data.pair.delta_seconds);
      sync.textContent = delta === null ? "PAIR WAITING" : `PAIR LAG ${delta.toFixed(1)}s`;
      statusClass(sync, delta === null ? "WAITING" : "PAIR LAG", []);
    }
    byId("chartUpdated").textContent = `Updated ${shortTime(data.generated_at)}`;
  }

  function activityLabel(setup) {
    const entryType = setup.overlay?.entry_type;
    const trigger = String(setup.trigger_type || "setup").replaceAll("_", " ");
    return entryType ? `${trigger} · ${entryType.replaceAll("_", " ")}` : trigger;
  }

  function renderActivity(data) {
    const setups = data.setups || [];
    const trades = data.trades || [];
    byId("chartWindowCounts").textContent = `${setups.length} SETUPS · ${trades.length} TRADES`;
    const items = [
      ...setups.map((setup) => ({ type: "SETUP", time: setup.created_at, item: setup })),
      ...trades.map((trade) => ({ type: "TRADE", time: trade.opened_at || trade.updated_at, item: trade })),
    ].sort((a, b) => (timeValue(b.time) || 0) - (timeValue(a.time) || 0)).slice(0, 12);

    if (!items.length) {
      byId("chartActivityList").innerHTML = '<div class="empty-state">No setups or paper executions fall inside this chart window yet.</div>';
      return;
    }
    byId("chartActivityList").innerHTML = items.map(({ type, time, item }) => {
      const isTrade = type === "TRADE";
      const title = isTrade
        ? `${item.direction || "--"} ${item.status || "trade"}`
        : `${item.direction || "--"} ${item.trigger_type || "setup"}`;
      const detail = isTrade ? `${item.result || "OPEN"} · ${item.result_r ?? "--"}R` : activityLabel(item);
      const value = isTrade ? `IN ${priceText(item.entry_price)}` : `E ${priceText(item.entry_price)}`;
      const sub = isTrade && item.exit_price !== null ? `OUT ${priceText(item.exit_price)}` : shortTime(time);
      return `<article class="chart-activity-item">
        <div><strong>${escapeHtml(title.toUpperCase())}</strong><small>${escapeHtml(detail)}</small></div>
        <div class="chart-activity-value">${escapeHtml(value)}<span>${escapeHtml(sub)}</span></div>
      </article>`;
    }).join("");
  }

  async function fetchChart(symbol, timeframe, limit) {
    const params = new URLSearchParams({ symbol, timeframe, limit: String(limit) });
    const response = await fetch(`/market/api/chart?${params}`, { credentials: "same-origin" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Chart request failed (${response.status})`);
    }
    return response.json();
  }

  function renderError(message) {
    byId("chartFeed").textContent = "CHART ERROR";
    statusClass(byId("chartFeed"), "STALE", []);
    byId("chartUpdated").textContent = message;
  }

  function scheduleRefresh() {
    clearTimeout(chartState.timer);
    if (!chartState.active) return;
    chartState.timer = setTimeout(refresh, 2000);
  }

  async function refresh() {
    if (!chartState.active || chartState.loading || document.hidden) {
      scheduleRefresh();
      return;
    }
    chartState.loading = true;
    const symbol = byId("chartSymbol").value;
    const timeframe = byId("chartTimeframe").value;
    const limit = Number(byId("chartLimit").value);
    const pairSymbol = symbol === "NQ" ? "ES" : (symbol === "ES" ? "NQ" : null);
    try {
      const [primary, pair] = await Promise.all([
        fetchChart(symbol, timeframe, limit),
        pairSymbol ? fetchChart(pairSymbol, timeframe, limit) : Promise.resolve(null),
      ]);
      chartState.primary = primary;
      chartState.pair = pair;
      renderStatus(primary);
      renderCanvasMeta("primary", primary);
      drawChart(byId("primaryExecutionCanvas"), primary, false);
      renderActivity(primary);
      byId("pairChartCard").classList.toggle("hidden", !pair);
      if (pair) {
        renderCanvasMeta("pair", pair, true);
        drawChart(byId("pairExecutionCanvas"), pair, true);
      }
    } catch (error) {
      renderError(error.message || "Chart could not load");
    } finally {
      chartState.loading = false;
      scheduleRefresh();
    }
  }

  function redraw() {
    if (!chartState.active) return;
    if (chartState.primary) drawChart(byId("primaryExecutionCanvas"), chartState.primary, false);
    if (chartState.pair) drawChart(byId("pairExecutionCanvas"), chartState.pair, true);
  }

  function bootChart() {
    const chartButton = document.querySelector('[data-view="chart"]');
    if (!chartButton || !byId("primaryExecutionCanvas")) return;
    document.querySelectorAll(".nav-item[data-view]").forEach((button) => {
      button.addEventListener("click", () => {
        chartState.active = button.dataset.view === "chart";
        if (chartState.active) requestAnimationFrame(refresh);
        else clearTimeout(chartState.timer);
      });
    });
    ["chartSymbol", "chartTimeframe", "chartLimit"].forEach((id) => {
      byId(id).addEventListener("change", refresh);
    });
    bindTooltip(byId("primaryExecutionCanvas"), byId("primaryChartTooltip"));
    bindTooltip(byId("pairExecutionCanvas"), byId("pairChartTooltip"));
    window.addEventListener("resize", redraw);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && chartState.active) refresh();
    });
  }

  bootChart();
})();
