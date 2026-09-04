(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const num = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  };
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const timeMs = (value) => {
    const parsed = new Date(value || '').getTime();
    return Number.isFinite(parsed) ? parsed : null;
  };
  const fmt = (value) => {
    const n = num(value);
    return n === null ? '--' : n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const esc = (value) => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
  const shortTime = (value) => {
    const date = new Date(value || '');
    return Number.isNaN(date.getTime()) ? '--' : date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  };

  const MIN_VISIBLE_BARS = 24;
  const HISTORY_BARS = 600;
  const state = {
    timer: null,
    busy: false,
    chart: null,
    decisions: null,
    view: {
      visibleBars: null,
      offsetBars: 0,
      drag: null,
    },
  };

  function selectedBars() {
    return Math.max(MIN_VISIBLE_BARS, Number($('otr8OverviewBars')?.value || 320));
  }

  function resetView() {
    state.view.visibleBars = null;
    state.view.offsetBars = 0;
    state.view.drag = null;
  }

  function chartWindow(total) {
    if (!total) return { start: 0, end: 0, visible: 0, offset: 0 };
    const fallback = Math.min(total, selectedBars());
    const visible = clamp(
      Math.round(state.view.visibleBars == null ? fallback : state.view.visibleBars),
      Math.min(MIN_VISIBLE_BARS, total),
      total,
    );
    const maxOffset = Math.max(0, total - visible);
    const offset = clamp(Math.round(state.view.offsetBars || 0), 0, maxOffset);
    const end = total - offset;
    const start = Math.max(0, end - visible);
    state.view.visibleBars = visible === fallback && state.view.visibleBars == null ? null : visible;
    state.view.offsetBars = offset;
    return { start, end, visible: end - start, offset };
  }

  function viewLabel(total, windowState) {
    if (!total || !windowState.visible) return 'Wheel zoom · drag pan · double-click reset';
    if (windowState.offset === 0) {
      return `${windowState.visible}/${total} bars · latest · wheel zoom · drag pan`;
    }
    return `${windowState.visible}/${total} bars · ${windowState.offset} bars back · double-click reset`;
  }

  function buildSurface() {
    if ($('otr8OverviewChart')) return;
    const guard = document.querySelector('.prop-guard-panel');
    if (!guard) return;
    guard.style.display = 'none';

    const section = document.createElement('section');
    section.id = 'otr8OverviewChart';
    section.className = 'panel otr8-live-chart-panel';
    section.innerHTML = `
      <div class="panel-head otr8-live-chart-head">
        <div>
          <div class="section-kicker">NINJATRADER BRIDGE · OTR VIEW</div>
          <h2>Gold Decision Chart</h2>
        </div>
        <div class="otr8-chart-controls">
          <label>Timeframe
            <select id="otr8OverviewTf" aria-label="OTR chart timeframe">
              <option value="1m">1 minute</option>
              <option value="5m" selected>5 minutes</option>
              <option value="15m">15 minutes</option>
              <option value="1h">1 hour</option>
              <option value="4h">4 hour</option>
            </select>
          </label>
          <label>Bars
            <select id="otr8OverviewBars" aria-label="OTR chart visible bar count">
              <option value="120">120</option>
              <option value="320" selected>320</option>
              <option value="600">600</option>
            </select>
          </label>
        </div>
      </div>
      <div class="otr8-chart-status">
        <span id="otr8Mode" class="otr8-chart-chip">IDLE</span>
        <span id="otr8Feed" class="otr8-chart-chip">FEED WAITING</span>
        <span id="otr8TfState" class="otr8-chart-chip">5M</span>
        <span id="otr8Updated" class="otr8-chart-chip">WAITING</span>
      </div>
      <div class="otr8-chart-shell">
        <article class="otr8-chart-card">
          <div class="otr8-chart-title">
            <div>
              <strong id="otr8ChartTitle">GC · 5m</strong>
              <small id="otr8ChartNavState" style="display:block;margin-top:4px;color:#83909d;font:650 10px/1.25 ui-monospace,SFMono-Regular,Menlo,monospace">Wheel zoom · drag pan · double-click reset</small>
            </div>
            <span id="otr8LastPrice">--</span>
          </div>
          <div class="otr8-canvas-wrap">
            <canvas id="otr8OverviewCanvas" aria-label="Interactive Gold candlestick chart showing OTR setup and execution levels" style="cursor:grab;touch-action:none;user-select:none"></canvas>
            <div id="otr8ChartEmpty" class="otr8-chart-empty">Waiting for NinjaTrader bridge candles.</div>
          </div>
          <div class="otr8-chart-legend">
            <span><i class="otr8-swatch fvg"></i>FVG</span><span><i class="otr8-swatch ob"></i>Order block</span>
            <span><i class="otr8-swatch"></i>Entry</span><span><i class="otr8-swatch stop"></i>Stop</span><span><i class="otr8-swatch target"></i>Target</span>
          </div>
        </article>
        <div class="otr8-chart-side">
          <article class="otr8-plan-card">
            <div class="otr8-side-kicker">WHAT OTR IS PLANNING</div>
            <div class="otr8-plan-title"><strong id="otr8PlanName">NO ACTIVE PLAN</strong><span id="otr8PlanStatus">WAITING</span></div>
            <div class="otr8-plan-grid">
              <div><span>Entry</span><strong id="otr8PlanEntry">--</strong></div>
              <div><span>Stop</span><strong id="otr8PlanStop">--</strong></div>
              <div><span>Target</span><strong id="otr8PlanTarget">--</strong></div>
              <div><span>R:R</span><strong id="otr8PlanRr">--</strong></div>
            </div>
          </article>
          <article class="otr8-tape-card">
            <div class="otr8-side-kicker">OTR 8.0 DECISION TAPE</div>
            <div id="otr8DecisionTape" class="otr8-decision-tape"><div class="empty-state">Waiting for OTR decisions.</div></div>
          </article>
        </div>
      </div>`;
    guard.before(section);

    $('otr8OverviewTf').addEventListener('change', () => { resetView(); state.chart = null; refresh(); });
    $('otr8OverviewBars').addEventListener('change', () => { resetView(); state.chart = null; refresh(); });
    window.addEventListener('resize', () => { if (state.chart) draw(state.chart); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    bindChartNavigation();
  }

  function bindChartNavigation() {
    const canvas = $('otr8OverviewCanvas');
    if (!canvas || canvas.dataset.otr8NavigationReady === '1') return;
    canvas.dataset.otr8NavigationReady = '1';

    canvas.addEventListener('wheel', (event) => {
      const total = state.chart?.candles?.length || 0;
      if (!total) return;
      event.preventDefault();
      const current = chartWindow(total);

      if (Math.abs(event.deltaX) > Math.abs(event.deltaY) * 0.75 || event.shiftKey) {
        const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) * 0.75 ? event.deltaX : event.deltaY;
        const bars = Math.round((delta / 120) * Math.max(2, current.visible * 0.10));
        state.view.visibleBars = current.visible;
        state.view.offsetBars = clamp(current.offset + bars, 0, Math.max(0, total - current.visible));
        draw(state.chart);
        return;
      }

      const rect = canvas.getBoundingClientRect();
      const plotLeft = 10;
      const plotRight = Math.max(plotLeft + 1, rect.width - 68);
      const cursorRatio = clamp((event.clientX - rect.left - plotLeft) / Math.max(1, plotRight - plotLeft), 0, 1);
      const factor = event.deltaY < 0 ? 0.82 : 1.22;
      const newVisible = clamp(
        Math.round(current.visible * factor),
        Math.min(MIN_VISIBLE_BARS, total),
        total,
      );
      const anchor = current.start + cursorRatio * Math.max(0, current.visible - 1);
      let newStart = Math.round(anchor - cursorRatio * Math.max(0, newVisible - 1));
      newStart = clamp(newStart, 0, Math.max(0, total - newVisible));
      state.view.visibleBars = newVisible;
      state.view.offsetBars = total - newVisible - newStart;
      draw(state.chart);
    }, { passive: false });

    canvas.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || !state.chart?.candles?.length) return;
      const total = state.chart.candles.length;
      const current = chartWindow(total);
      state.view.visibleBars = current.visible;
      state.view.drag = {
        pointerId: event.pointerId,
        x: event.clientX,
        offset: current.offset,
        visible: current.visible,
      };
      canvas.setPointerCapture?.(event.pointerId);
      canvas.style.cursor = 'grabbing';
      event.preventDefault();
    });

    canvas.addEventListener('pointermove', (event) => {
      const drag = state.view.drag;
      const total = state.chart?.candles?.length || 0;
      if (!drag || drag.pointerId !== event.pointerId || !total) return;
      const plotWidth = Math.max(1, canvas.clientWidth - 78);
      const dx = event.clientX - drag.x;
      const barShift = Math.round((dx / plotWidth) * drag.visible);
      state.view.offsetBars = clamp(drag.offset + barShift, 0, Math.max(0, total - drag.visible));
      draw(state.chart);
      event.preventDefault();
    });

    const endDrag = (event) => {
      if (!state.view.drag) return;
      if (event?.pointerId != null && state.view.drag.pointerId !== event.pointerId) return;
      try { canvas.releasePointerCapture?.(state.view.drag.pointerId); } catch (_) {}
      state.view.drag = null;
      canvas.style.cursor = 'grab';
    };
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);
    canvas.addEventListener('lostpointercapture', () => endDrag());

    canvas.addEventListener('dblclick', (event) => {
      event.preventDefault();
      resetView();
      if (state.chart) draw(state.chart);
    });
  }

  function boundsOf(zone) {
    if (!zone || typeof zone !== 'object') return null;
    const low = num(zone.lower ?? zone.low ?? zone.zone_overlap_low ?? zone.body_low);
    const high = num(zone.upper ?? zone.high ?? zone.zone_overlap_high ?? zone.body_high);
    return low === null || high === null ? null : { low: Math.min(low, high), high: Math.max(low, high) };
  }

  function latestPlan(data) {
    const trades = (data?.trades || []).filter((row) => ['PENDING', 'OPEN'].includes(String(row.status || '').toUpperCase()));
    if (trades.length) return { ...trades.at(-1), kind: 'TRADE' };
    const setups = data?.setups || [];
    return setups.length ? { ...setups.at(-1), kind: 'SETUP' } : null;
  }

  function drawLine(ctx, plot, yFor, value, color, label) {
    const price = num(value);
    if (price === null) return;
    const y = yFor(price);
    ctx.save();
    ctx.setLineDash([6, 5]);
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(plot.left, y); ctx.lineTo(plot.right, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color;
    ctx.font = '700 10px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(`${label} ${fmt(price)}`, plot.right - 4, Math.max(plot.top + 12, y - 4));
    ctx.restore();
  }

  function draw(data) {
    const canvas = $('otr8OverviewCanvas');
    if (!canvas) return;
    const allCandles = data?.candles || [];
    $('otr8ChartEmpty').style.display = allCandles.length ? 'none' : 'block';
    const parent = canvas.parentElement;
    const width = Math.max(1, parent.clientWidth);
    const height = Math.max(1, parent.clientHeight);
    const ratio = Math.min(window.devicePixelRatio || 1, 2.5);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    if (!allCandles.length) return;

    const windowState = chartWindow(allCandles.length);
    const candles = allCandles.slice(windowState.start, windowState.end);
    const firstTime = timeMs(candles[0]?.close_time || candles[0]?.open_time);
    const lastTime = timeMs(candles.at(-1)?.close_time || candles.at(-1)?.open_time);
    const inWindow = (rawTime) => {
      const t = timeMs(rawTime);
      if (t === null || firstTime === null || lastTime === null) return true;
      return t >= firstTime && t <= lastTime;
    };
    const visibleSetups = (data.setups || []).filter((s) => inWindow(s.created_at)).slice(-40);

    const prices = [];
    candles.forEach((c) => { [c.low, c.high].forEach((v) => { const n = num(v); if (n !== null) prices.push(n); }); });
    visibleSetups.forEach((s) => {
      [s.entry_price, s.stop_price, s.target_price].forEach((v) => { const n = num(v); if (n !== null) prices.push(n); });
      [s.overlay?.fvg, s.overlay?.order_block].forEach((z) => { const b = boundsOf(z); if (b) prices.push(b.low, b.high); });
    });
    const plan = latestPlan(data);
    if (plan) [plan.entry_price, plan.stop_price, plan.target_price].forEach((v) => { const n = num(v); if (n !== null) prices.push(n); });
    let low = Math.min(...prices), high = Math.max(...prices);
    const pad = Math.max((high - low) * .08, Math.abs(high || 1) * .0002);
    low -= pad;
    high += pad;
    const plot = { left: 10, right: width - 72, top: 16, bottom: height - 28 };
    const pw = Math.max(1, plot.right - plot.left), ph = Math.max(1, plot.bottom - plot.top);
    const xFor = (i) => plot.left + (candles.length === 1 ? pw / 2 : (i / (candles.length - 1)) * pw);
    const yFor = (p) => plot.bottom - ((p - low) / Math.max(.000001, high - low)) * ph;

    ctx.strokeStyle = '#1b232c';
    ctx.fillStyle = '#8c99a7';
    ctx.lineWidth = 1;
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
    for (let i = 0; i <= 5; i += 1) {
      const y = plot.top + (i / 5) * ph, p = high - (i / 5) * (high - low);
      ctx.beginPath(); ctx.moveTo(plot.left, y); ctx.lineTo(plot.right, y); ctx.stroke();
      ctx.textAlign = 'left';
      ctx.fillText(fmt(p), plot.right + 6, y + 3);
    }

    const nearest = (rawTime) => {
      const target = timeMs(rawTime);
      if (target === null) return Math.max(0, candles.length - 1);
      let best = 0, dist = Infinity;
      candles.forEach((c, i) => {
        const t = timeMs(c.close_time || c.open_time);
        if (t === null) return;
        const d = Math.abs(t - target);
        if (d < dist) { dist = d; best = i; }
      });
      return best;
    };
    const zone = (z, stroke, fill) => {
      const b = boundsOf(z); if (!b) return;
      const start = nearest(z.formed_at || z.candle_time || z.time);
      const end = Math.min(candles.length - 1, start + Math.max(8, Math.round(candles.length * .09)));
      const x = xFor(start), right = xFor(end), top = yFor(b.high), bottom = yFor(b.low);
      ctx.fillStyle = fill; ctx.strokeStyle = stroke;
      ctx.fillRect(x, top, Math.max(3, right - x), Math.max(1, bottom - top));
      ctx.strokeRect(x, top, Math.max(3, right - x), Math.max(1, bottom - top));
    };
    visibleSetups.forEach((s) => {
      zone(s.overlay?.fvg, '#54879c', 'rgba(84,135,156,.14)');
      zone(s.overlay?.order_block, '#a48858', 'rgba(164,136,88,.15)');
    });

    const spacing = pw / Math.max(1, candles.length - 1);
    const body = Math.max(1.5, Math.min(14, spacing * .68));
    candles.forEach((c, i) => {
      const o = num(c.open), h = num(c.high), l = num(c.low), cl = num(c.close);
      if ([o, h, l, cl].some((v) => v === null)) return;
      const color = cl >= o ? '#58ba84' : '#d76565', x = xFor(i);
      ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, yFor(h)); ctx.lineTo(x, yFor(l)); ctx.stroke();
      const top = Math.min(yFor(o), yFor(cl));
      ctx.fillRect(x - body / 2, top, body, Math.max(1.5, Math.abs(yFor(o) - yFor(cl))));
    });

    if (plan) {
      drawLine(ctx, plot, yFor, plan.entry_price, '#eeeeee', 'ENTRY');
      drawLine(ctx, plot, yFor, plan.stop_price, '#d76565', 'SL');
      drawLine(ctx, plot, yFor, plan.target_price, '#58ba84', 'TP');
    }

    ctx.fillStyle = '#8c99a7';
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
    [0, .33, .66, 1].forEach((part, pos) => {
      const i = Math.round((candles.length - 1) * part);
      const d = new Date(candles[i]?.close_time || candles[i]?.open_time || '');
      const label = Number.isNaN(d.getTime()) ? '--' : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      ctx.textAlign = pos === 0 ? 'left' : (pos === 3 ? 'right' : 'center');
      ctx.fillText(label, xFor(i), height - 8);
    });

    $('otr8ChartNavState').textContent = viewLabel(allCandles.length, windowState);
    const tf = $('otr8OverviewTf')?.value || data?.timeframe || '--';
    $('otr8ChartTitle').textContent = `GC · ${tf} · ${windowState.visible}/${allCandles.length} bars`;
  }

  function renderPlan(data) {
    const plan = latestPlan(data);
    if (!plan) {
      $('otr8PlanName').textContent = 'NO ACTIVE PLAN'; $('otr8PlanStatus').textContent = 'WAITING';
      ['otr8PlanEntry','otr8PlanStop','otr8PlanTarget','otr8PlanRr'].forEach((id) => { $(id).textContent = '--'; });
      return;
    }
    const strategy = plan.strategy || plan.overlay?.strategy || plan.trigger_type || plan.kind;
    $('otr8PlanName').textContent = `${String(plan.direction || '').toUpperCase()} ${String(strategy || '').replaceAll('_', ' ')}`.trim();
    $('otr8PlanStatus').textContent = String(plan.status || plan.kind || 'SETUP').toUpperCase();
    $('otr8PlanEntry').textContent = fmt(plan.entry_price); $('otr8PlanStop').textContent = fmt(plan.stop_price); $('otr8PlanTarget').textContent = fmt(plan.target_price);
    const rr = num(plan.risk_reward); $('otr8PlanRr').textContent = rr === null ? '--' : `${rr.toFixed(2)}R`;
  }

  function renderStatus(data, tf) {
    const mode = String(data?.runtime?.mode || 'IDLE').toUpperCase();
    const feed = String(data?.feed?.state || 'WAITING').toUpperCase();
    $('otr8Mode').textContent = mode; $('otr8Mode').className = `otr8-chart-chip ${mode === 'LIVE' ? 'live' : (mode === 'REPLAY' ? 'replay' : '')}`;
    $('otr8Feed').textContent = `FEED ${feed}`; $('otr8Feed').className = `otr8-chart-chip ${['LIVE','REPLAY'].includes(feed) ? 'good' : (feed === 'STALE' ? 'warn' : '')}`;
    $('otr8TfState').textContent = tf.toUpperCase(); $('otr8Updated').textContent = `UPDATED ${shortTime(data?.generated_at)}`;
    const last = data?.candles?.at(-1); $('otr8LastPrice').textContent = fmt(last?.close);
  }

  function decisionReason(row) {
    const trace = row?.trace || {};
    const stages = Array.isArray(trace.stages) ? trace.stages : [];
    const last = stages.at(-1) || {};
    return last.reason || trace.reason || trace.final_reason || row.final_status || 'Decision recorded';
  }

  function renderDecisions(payload, tf) {
    const rows = (payload?.recent_decisions || [])
      .filter((row) => String(row.symbol || '').toUpperCase() === 'GC' && String(row.timeframe || '').toLowerCase() === tf)
      .slice(0, 8);
    if (!rows.length) {
      $('otr8DecisionTape').innerHTML = '<div class="empty-state">No OTR 8.0 decisions on this timeframe yet.</div>';
      return;
    }
    $('otr8DecisionTape').innerHTML = rows.map((row) => `
      <div class="otr8-decision-row">
        <time>${esc(shortTime(row.created_at))}</time>
        <div><strong>${esc(`${row.direction || ''} ${String(row.strategy || '').replaceAll('_',' ')}`)}</strong><small>${esc(decisionReason(row))}</small></div>
        <b>${esc(row.final_status || 'RECORDED')}</b>
      </div>`).join('');
  }

  async function loadJson(url) {
    const response = await fetch(url, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function preserveHistoricalAnchor(previous, next) {
    if (!previous?.candles?.length || !next?.candles?.length || state.view.offsetBars <= 0) return;
    const priorNewest = timeMs(previous.candles.at(-1)?.close_time || previous.candles.at(-1)?.open_time);
    if (priorNewest === null) return;
    const index = next.candles.findIndex((c) => timeMs(c.close_time || c.open_time) === priorNewest);
    if (index < 0) return;
    const addedAfterAnchor = next.candles.length - 1 - index;
    if (addedAfterAnchor > 0) state.view.offsetBars += addedAfterAnchor;
  }

  async function refresh() {
    if (state.busy || document.hidden || !$('otr8OverviewTf')) return;
    state.busy = true;
    const tf = $('otr8OverviewTf').value;
    const requestedBars = selectedBars();
    const historyLimit = Math.max(HISTORY_BARS, requestedBars);
    try {
      const [chart, decisions] = await Promise.all([
        loadJson(`/market/api/chart?symbol=GC&timeframe=${encodeURIComponent(tf)}&limit=${encodeURIComponent(historyLimit)}`),
        loadJson('/market/api/otr8').catch(() => ({ recent_decisions: [] })),
      ]);
      preserveHistoricalAnchor(state.chart, chart);
      state.chart = chart;
      state.decisions = decisions;
      renderStatus(chart, tf);
      renderPlan(chart);
      renderDecisions(decisions, tf);
      draw(chart);
    } catch (error) {
      $('otr8Feed').textContent = 'CHART ERROR';
      $('otr8Feed').className = 'otr8-chart-chip warn';
      $('otr8Updated').textContent = String(error.message || error);
    } finally {
      state.busy = false;
      clearTimeout(state.timer);
      state.timer = setTimeout(refresh, 2000);
    }
  }

  function boot() {
    buildSurface();
    if ($('otr8OverviewChart')) refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true }); else boot();
})();