(() => {
  const $ = (id) => document.getElementById(id);
  const els = {};
  let statusTimer = null;
  let baseTradeRenderer72 = null;

  const REALIZED_RESULTS_72 = new Set(['WIN', 'LOSS']);
  const LIVE_TRADE_STATES_72 = new Set(['OPEN', 'PENDING']);
  const CHART_TIMEFRAMES_72 = ['1m', '5m', '15m', '1h'];
  const chartAuxCache72 = new Map();

  function text(id, value) {
    const el = els[id] || $(id);
    if (el) el.textContent = value == null || value === '' ? '--' : String(value);
  }

  function setState(id, value, tone) {
    const el = els[id] || $(id);
    if (!el) return;
    el.textContent = value == null || value === '' ? '--' : String(value);
    el.classList.remove('execution-state-good', 'execution-state-warn', 'execution-state-bad');
    if (tone) el.classList.add(`execution-state-${tone}`);
  }

  function ageLabel(isoValue) {
    if (!isoValue) return 'No heartbeat';
    const value = Date.parse(isoValue);
    if (!Number.isFinite(value)) return 'Unknown';
    const seconds = Math.max(0, Math.round((Date.now() - value) / 1000));
    if (seconds < 2) return 'Just now';
    if (seconds < 60) return `${seconds}s ago`;
    return `${Math.round(seconds / 60)}m ago`;
  }

  function isVisibleJournalTrade72(trade) {
    const result = String(trade?.result || '').toUpperCase();
    const status = String(trade?.status || '').toUpperCase();
    return REALIZED_RESULTS_72.has(result) || LIVE_TRADE_STATES_72.has(status);
  }

  function ensureTradeFilters72() {
    const resultFilter = $('tradeResultFilter');
    if (!resultFilter || resultFilter.dataset.liveVisibility72 === '1') return;
    resultFilter.innerHTML = `
      <option value="all">All trades</option>
      <option value="OPEN">Open</option>
      <option value="PENDING">Pending</option>
      <option value="WIN">Wins</option>
      <option value="LOSS">Losses</option>`;
    resultFilter.dataset.liveVisibility72 = '1';
    if (!['all', 'OPEN', 'PENDING', 'WIN', 'LOSS'].includes(resultFilter.value)) {
      resultFilter.value = 'all';
    }
  }

  function captureTradeRenderer72() {
    if (typeof window.renderTrades === 'function') {
      baseTradeRenderer72 = window.renderTrades;
    }
  }

  function installTradeVisibility72() {
    if (!baseTradeRenderer72 || window.renderTrades?.__otrLiveVisibility72 === true) return;

    ensureTradeFilters72();
    const liveRenderer72 = function liveRenderer72(trades) {
      ensureTradeFilters72();
      const allTrades = trades || [];
      baseTradeRenderer72(allTrades.filter(isVisibleJournalTrade72));
      if (typeof window.renderAttemptAudit65 === 'function') {
        window.renderAttemptAudit65(allTrades);
      }
    };
    liveRenderer72.__otrLiveVisibility72 = true;
    window.renderTrades = liveRenderer72;

    if (typeof state !== 'undefined' && state?.snapshot) {
      window.renderTrades(state.snapshot.trades || []);
    }
  }

  const nativeFetch72 = window.fetch.bind(window);

  function chartTradeTime72(trade, fallback = 0) {
    const value = Date.parse(trade?.opened_at || trade?.updated_at || trade?.closed_at || '');
    return Number.isFinite(value) ? value : fallback;
  }

  function overlapsWindow72(trade, start, end) {
    const opened = Date.parse(trade?.opened_at || trade?.updated_at || '');
    const closed = Date.parse(trade?.closed_at || trade?.updated_at || '');
    const startsBeforeEnd = !Number.isFinite(opened) || opened <= end;
    const endsAfterStart = !Number.isFinite(closed) || closed >= start;
    return startsBeforeEnd && endsAfterStart;
  }

  async function loadAuxChart72(url, init, timeframe) {
    const aux = new URL(url.toString());
    aux.searchParams.set('timeframe', timeframe);
    aux.searchParams.set('limit', '1000');
    const key = aux.toString();
    const cached = chartAuxCache72.get(key);
    if (cached && Date.now() - cached.at < 4500) return cached.payload;

    const response = await nativeFetch72(aux.toString(), init);
    if (!response.ok) return null;
    const payload = await response.json();
    chartAuxCache72.set(key, { at: Date.now(), payload });
    return payload;
  }

  async function fetchWithCrossTimeframeTrades72(input, init) {
    const primaryResponse = await nativeFetch72(input, init);
    let url;
    try {
      const raw = typeof input === 'string' ? input : input?.url;
      url = new URL(raw, window.location.href);
    } catch (_) {
      return primaryResponse;
    }

    if (url.pathname !== '/market/api/chart' || !primaryResponse.ok) return primaryResponse;

    try {
      const primary = await primaryResponse.clone().json();
      const candles = Array.isArray(primary?.candles) ? primary.candles : [];
      const selectedTimeframe = String(primary?.timeframe || url.searchParams.get('timeframe') || '').toLowerCase();
      if (!candles.length || !CHART_TIMEFRAMES_72.includes(selectedTimeframe)) return primaryResponse;

      const start = Date.parse(candles[0]?.open_time || candles[0]?.close_time || '');
      const end = Date.parse(candles.at(-1)?.close_time || candles.at(-1)?.open_time || '');
      if (!Number.isFinite(start) || !Number.isFinite(end)) return primaryResponse;

      const primaryTrades = (Array.isArray(primary.trades) ? primary.trades : []).map((trade) => ({
        ...trade,
        timeframe: trade.timeframe || selectedTimeframe,
      }));

      const otherTimeframes = CHART_TIMEFRAMES_72.filter((timeframe) => timeframe !== selectedTimeframe);
      const auxiliaryPayloads = await Promise.all(
        otherTimeframes.map(async (timeframe) => {
          try {
            const payload = await loadAuxChart72(url, init, timeframe);
            return { timeframe, payload };
          } catch (_) {
            return { timeframe, payload: null };
          }
        })
      );

      const merged = new Map();
      primaryTrades.forEach((trade) => {
        merged.set(trade.setup_id || `${selectedTimeframe}:${trade.opened_at}:${trade.entry_price}`, trade);
      });

      auxiliaryPayloads.forEach(({ timeframe, payload }) => {
        const trades = Array.isArray(payload?.trades) ? payload.trades : [];
        trades
          .filter((trade) => overlapsWindow72(trade, start, end))
          .forEach((trade) => {
            const item = { ...trade, timeframe };
            const key = item.setup_id || `${timeframe}:${item.opened_at}:${item.entry_price}`;
            if (!merged.has(key)) merged.set(key, item);
          });
      });

      primary.trades = [...merged.values()]
        .sort((a, b) => chartTradeTime72(a) - chartTradeTime72(b))
        .slice(-80);
      primary.trade_timeframes = [...new Set(primary.trades.map((trade) => trade.timeframe).filter(Boolean))];

      return new Response(JSON.stringify(primary), {
        status: primaryResponse.status,
        statusText: primaryResponse.statusText,
        headers: { 'Content-Type': 'application/json' },
      });
    } catch (_) {
      return primaryResponse;
    }
  }

  window.fetch = fetchWithCrossTimeframeTrades72;

  async function loadStatus() {
    try {
      const response = await fetch('/market/api/execution/status', {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (response.status === 401) return;
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const config = data.config || {};
      const reconciliation = data.reconciliation || {};
      const commands = data.commands || {};

      setState('executionModeStatus', config.mode || 'PAPER', config.mode === 'PAPER' ? 'warn' : 'good');
      setState('executionArmStatus', config.armed ? 'ARMED' : 'DISARMED', config.armed ? 'bad' : 'good');
      text('executionAccountStatus', config.account || '--');
      setState(
        'executionReconciliationStatus',
        reconciliation.ok ? 'MATCHED' : 'BLOCKED',
        reconciliation.ok ? 'good' : 'bad'
      );
      text('executionBridgeHeartbeat', ageLabel(data.bridge_heartbeat_at));
      text('executionQueueStatus', data.active_commands || 0);
      setState('executionKillStatus', data.kill_switch ? 'ACTIVE' : 'CLEAR', data.kill_switch ? 'bad' : 'good');
      setState(
        'executionTransmissionStatus',
        data.broker_transmission_possible ? 'POSSIBLE' : 'LOCKED',
        data.broker_transmission_possible ? 'warn' : 'good'
      );

      const note = $('executionSafetyNote');
      if (note) {
        const reason = reconciliation.reason || data.latest_audit?.reason || 'Execution kernel is waiting for broker certification.';
        note.textContent = `${reason} Commands: ${Object.entries(commands).map(([k, v]) => `${k} ${v}`).join(' · ') || 'none'}`;
      }

      const engage = $('executionKillEngage');
      const reset = $('executionKillReset');
      if (engage) engage.disabled = Boolean(data.kill_switch);
      if (reset) reset.disabled = !data.kill_switch;
    } catch (error) {
      setState('executionReconciliationStatus', 'UNAVAILABLE', 'bad');
      const note = $('executionSafetyNote');
      if (note) note.textContent = `Execution status unavailable: ${error.message}`;
    }
  }

  async function setKillSwitch(enabled) {
    const payload = {
      enabled,
      reason: enabled ? 'Dashboard emergency stop' : 'Dashboard supervised reset',
      confirmation: enabled ? '' : 'RESET_EXECUTION_KILL_SWITCH',
    };
    const response = await fetch('/market/api/execution/kill-switch', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const body = await response.json();
        detail = body.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    await loadStatus();
  }

  function bind() {
    captureTradeRenderer72();

    const engage = $('executionKillEngage');
    if (engage) {
      engage.addEventListener('click', async () => {
        if (!window.confirm('Engage OTR execution kill switch and block all new broker commands?')) return;
        try {
          await setKillSwitch(true);
        } catch (error) {
          window.alert(`Could not engage kill switch: ${error.message}`);
        }
      });
    }

    const reset = $('executionKillReset');
    if (reset) {
      reset.addEventListener('click', async () => {
        const typed = window.prompt('Type RESET to clear the execution kill switch. This does not arm trading.');
        if (typed !== 'RESET') return;
        try {
          await setKillSwitch(false);
        } catch (error) {
          window.alert(`Could not reset kill switch: ${error.message}`);
        }
      });
    }

    loadStatus();
    statusTimer = window.setInterval(loadStatus, 5000);
  }

  window.addEventListener('DOMContentLoaded', bind, { once: true });
  window.addEventListener('load', () => window.setTimeout(installTradeVisibility72, 0), { once: true });
  window.addEventListener('beforeunload', () => {
    if (statusTimer) window.clearInterval(statusTimer);
  });
})();
