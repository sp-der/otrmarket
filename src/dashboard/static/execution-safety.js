(() => {
  const $ = (id) => document.getElementById(id);
  const els = {};
  let statusTimer = null;

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
  window.addEventListener('beforeunload', () => {
    if (statusTimer) window.clearInterval(statusTimer);
  });
})();
