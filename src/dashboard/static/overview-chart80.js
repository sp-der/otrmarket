(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const num = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
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
  const state = { timer: null, busy: false, chart: null, decisions: null };

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
            <select id="otr8OverviewBars" aria-label="OTR chart bars">
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
          <div class="otr8-chart-title"><strong id="otr8ChartTitle">GC · 5m</strong><span id="otr8LastPrice">--</span></div>
          <div class="otr8-canvas-wrap">
            <canvas id="otr8OverviewCanvas" aria-label="Gold candlestick chart showing OTR setup and execution levels"></canvas>
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

    $('otr8OverviewTf').addEventListener('change', () => { state.chart = null; refresh(); });
    $('otr8OverviewBars').addEventListener('change', () => { state.chart = null; refresh(); });
    window.addEventListener('resize', () => { if (state.chart) draw(state.chart); });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
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
    ctx.font = '700 9px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textAlign = 'right';
    ctx.fillText(`${label} ${fmt(price)}`, plot.right - 4, Math.max(plot.top + 10, y - 4));
    ctx.restore();
  }

  function draw(data) {
    const canvas = $('otr8OverviewCanvas');
    if (!canvas) return;
    const candles = data?.candles || [];
    $('otr8ChartEmpty').style.display = candles.length ? 'none' : 'block';
    const parent = canvas.parentElement;
    const width = Math.max(1, parent.clientWidth);
    const height = Math.max(1, parent.clientHeight);
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
    if (!candles.length) return;

    const prices = [];
    candles.forEach((c) => { [c.low, c.high].forEach((v) => { const n = num(v); if (n !== null) prices.push(n); }); });
    (data.setups || []).slice(-24).forEach((s) => {
      [s.entry_price, s.stop_price, s.target_price].forEach((v) => { const n = num(v); if (n !== null) prices.push(n); });
      [s.overlay?.fvg, s.overlay?.order_block].forEach((z) => { const b = boundsOf(z); if (b) prices.push(b.low, b.high); });
    });
    const plan = latestPlan(data);
    if (plan) [plan.entry_price, plan.stop_price, plan.target_price].forEach((v) => { const n = num(v); if (n !== null) prices.push(n); });
    let low = Math.min(...prices), high = Math.max(...prices);
    const pad = Math.max((high - low) * .08, Math.abs(high || 1) * .0002); low -= pad; high += pad;
    const plot = { left: 10, right: width - 68, top: 16, bottom: height - 24 };
    const pw = Math.max(1, plot.right - plot.left), ph = Math.max(1, plot.bottom - plot.top);
    const xFor = (i) => plot.left + (candles.length === 1 ? pw / 2 : (i / (candles.length - 1)) * pw);
    const yFor = (p) => plot.bottom - ((p - low) / Math.max(.000001, high - low)) * ph;

    ctx.strokeStyle = '#171717'; ctx.fillStyle = '#666'; ctx.lineWidth = 1; ctx.font = '8px ui-monospace, SFMono-Regular, Menlo, monospace';
    for (let i = 0; i <= 5; i += 1) {
      const y = plot.top + (i / 5) * ph, p = high - (i / 5) * (high - low);
      ctx.beginPath(); ctx.moveTo(plot.left, y); ctx.lineTo(plot.right, y); ctx.stroke(); ctx.fillText(fmt(p), plot.right + 5, y + 3);
    }

    const nearest = (rawTime) => {
      const target = new Date(rawTime || '').getTime();
      if (!Number.isFinite(target)) return Math.max(0, candles.length - 1);
      let best = 0, dist = Infinity;
      candles.forEach((c, i) => { const t = new Date(c.close_time || c.open_time).getTime(); const d = Math.abs(t - target); if (d < dist) { dist = d; best = i; } });
      return best;
    };
    const zone = (z, stroke, fill) => {
      const b = boundsOf(z); if (!b) return;
      const start = nearest(z.formed_at || z.candle_time || z.time); const end = Math.min(candles.length - 1, start + Math.max(8, Math.round(candles.length * .09)));
      const x = xFor(start), right = xFor(end), top = yFor(b.high), bottom = yFor(b.low);
      ctx.fillStyle = fill; ctx.strokeStyle = stroke; ctx.fillRect(x, top, Math.max(3, right - x), Math.max(1, bottom - top)); ctx.strokeRect(x, top, Math.max(3, right - x), Math.max(1, bottom - top));
    };
    (data.setups || []).slice(-24).forEach((s) => { zone(s.overlay?.fvg, '#54879c', 'rgba(84,135,156,.14)'); zone(s.overlay?.order_block, '#a48858', 'rgba(164,136,88,.15)'); });

    const spacing = pw / Math.max(1, candles.length - 1), body = Math.max(1, Math.min(9, spacing * .64));
    candles.forEach((c, i) => {
      const o = num(c.open), h = num(c.high), l = num(c.low), cl = num(c.close); if ([o, h, l, cl].some((v) => v === null)) return;
      const color = cl >= o ? '#58ba84' : '#d76565', x = xFor(i); ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.beginPath(); ctx.moveTo(x, yFor(h)); ctx.lineTo(x, yFor(l)); ctx.stroke();
      const top = Math.min(yFor(o), yFor(cl)); ctx.fillRect(x - body / 2, top, body, Math.max(1, Math.abs(yFor(o) - yFor(cl))));
    });

    if (plan) {
      drawLine(ctx, plot, yFor, plan.entry_price, '#eeeeee', 'ENTRY');
      drawLine(ctx, plot, yFor, plan.stop_price, '#d76565', 'SL');
      drawLine(ctx, plot, yFor, plan.target_price, '#58ba84', 'TP');
    }

    ctx.fillStyle = '#666'; ctx.font = '8px ui-monospace, SFMono-Regular, Menlo, monospace';
    [0, .33, .66, 1].forEach((part, pos) => {
      const i = Math.round((candles.length - 1) * part), d = new Date(candles[i]?.close_time || candles[i]?.open_time || '');
      const label = Number.isNaN(d.getTime()) ? '--' : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      ctx.textAlign = pos === 0 ? 'left' : (pos === 3 ? 'right' : 'center'); ctx.fillText(label, xFor(i), height - 7);
    });
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
    const last = data?.candles?.at(-1); $('otr8ChartTitle').textContent = `GC · ${tf} · ${(data?.candles || []).length} bars`; $('otr8LastPrice').textContent = fmt(last?.close);
  }

  function decisionReason(row) {
    const trace = row?.trace || {};
    const stages = Array.isArray(trace.stages) ? trace.stages : [];
    const last = stages.at(-1) || {};
    return last.reason || trace.reason || trace.final_reason || row.final_status || 'Decision recorded';
  }

  function renderDecisions(payload, tf) {
    const rows = (payload?.recent_decisions || []).filter((row) => String(row.symbol || '').toUpperCase() === 'GC' && String(row.timeframe || '').toLowerCase() === tf).slice(0, 8);
    if (!rows.length) { $('otr8DecisionTape').innerHTML = '<div class="empty-state">No OTR 8.0 decisions on this timeframe yet.</div>'; return; }
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

  async function refresh() {
    if (state.busy || document.hidden || !$('otr8OverviewTf')) return;
    state.busy = true;
    const tf = $('otr8OverviewTf').value, bars = $('otr8OverviewBars').value;
    try {
      const [chart, decisions] = await Promise.all([
        loadJson(`/market/api/chart?symbol=GC&timeframe=${encodeURIComponent(tf)}&limit=${encodeURIComponent(bars)}`),
        loadJson('/market/api/otr8').catch(() => ({ recent_decisions: [] })),
      ]);
      state.chart = chart; state.decisions = decisions; renderStatus(chart, tf); renderPlan(chart); renderDecisions(decisions, tf); draw(chart);
    } catch (error) {
      $('otr8Feed').textContent = 'CHART ERROR'; $('otr8Feed').className = 'otr8-chart-chip warn'; $('otr8Updated').textContent = String(error.message || error);
    } finally {
      state.busy = false; clearTimeout(state.timer); state.timer = setTimeout(refresh, 2000);
    }
  }

  function boot() { buildSurface(); if ($('otr8OverviewChart')) refresh(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true }); else boot();
})();
