(() => {
  const VERIFY_MODES = new Set(['VERIFY', 'VERIFICATION', 'TEST']);

  function money(value, signed = false) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    const body = Math.abs(n).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    if (n < 0) return `-$${body}`;
    return `${signed && n > 0 ? '+' : ''}$${body}`;
  }

  function rValue(value, signed = false) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    return `${signed && n > 0 ? '+' : ''}${n.toFixed(2)}R`;
  }

  function percent(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '--';
    return `${n.toFixed(1)}%`;
  }

  function marketTime(value) {
    if (!value) return 'waiting for replay time';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  }

  function setLabelFor(id, label) {
    const value = document.getElementById(id);
    const labelNode = value?.previousElementSibling;
    if (labelNode) labelNode.textContent = label;
  }

  function strategySummary(run) {
    const entries = Object.entries(run?.strategy_breakdown || {});
    if (!entries.length) return 'No accepted trades yet';
    return entries
      .sort((a, b) => Number(b[1]) - Number(a[1]))
      .map(([name, count]) => `${name.replaceAll('_', ' ')} ${count}`)
      .join(' · ');
  }

  function applyVerificationUi(e) {
    const mode = String(e?.trading_mode || '').toUpperCase();
    if (!VERIFY_MODES.has(mode)) return;
    const run = e?.verify_run || {};

    const panel = document.querySelector('.prop-guard-panel');
    if (panel) {
      const kicker = panel.querySelector('.section-kicker');
      const title = panel.querySelector('h2');
      if (kicker) kicker.textContent = 'BOT VERIFICATION';
      if (title) title.textContent = 'Current VERIFY Run';
    }

    const profile = document.getElementById('evalProfile');
    const status = document.getElementById('evalStatus');
    if (profile) profile.textContent = `${run.build || '7.2Q'} · GC REPLAY · CURRENT RUN`;
    if (status) {
      status.textContent = 'ACTIVE';
      status.className = 'eval-status-chip eval-active';
    }

    setLabelFor('evalBalance', 'Run P/L');
    setLabelFor('evalTarget', 'Wins / Losses');
    setLabelFor('evalCushion', 'Win Rate');
    setLabelFor('evalToday', 'Total R');
    setLabelFor('evalRisk', 'Risk / Trade');
    setLabelFor('evalTrades', 'Closed Trades');

    const balance = document.getElementById('evalBalance');
    const pnl = document.getElementById('evalPnl');
    const target = document.getElementById('evalTarget');
    const progressText = document.getElementById('evalProgressText');
    const cushion = document.getElementById('evalCushion');
    const floor = document.getElementById('evalFloor');
    const today = document.getElementById('evalToday');
    const dailyStop = document.getElementById('evalDailyStop');
    const risk = document.getElementById('evalRisk');
    const committed = document.getElementById('evalCommitted');
    const trades = document.getElementById('evalTrades');
    const lossStreak = document.getElementById('evalLossStreak');
    const reason = document.getElementById('evalReason');

    if (balance) balance.textContent = money(run.total_dollars ?? e.realized_pnl, true);
    if (pnl) pnl.textContent = `Max DD ${rValue(-(Number(run.max_drawdown_r || 0)))}`;
    if (target) target.textContent = `${Number(run.wins || 0)}W / ${Number(run.losses || 0)}L`;
    if (progressText) progressText.textContent = `${run.run_id || 'Current run'} · started ${marketTime(run.started_market_time)}`;
    if (cushion) cushion.textContent = percent(run.win_rate);
    if (floor) floor.textContent = `Dollar drawdown ${money(-(Number(run.max_drawdown_dollars || 0)))}`;
    if (today) today.textContent = rValue(run.total_r, true);
    if (dailyStop) dailyStop.textContent = 'All-time history remains preserved on the Trades page';
    if (risk) risk.textContent = money(e.available_risk ?? e.verify_risk_per_trade);
    if (committed) committed.textContent = `Open ${Number(run.open || 0)} · Pending ${Number(run.pending || 0)}`;
    if (trades) trades.textContent = `${Number(run.closed || 0)}`;
    if (lossStreak) lossStreak.textContent = strategySummary(run);
    if (reason) reason.textContent = 'Overview performance now counts only trades created after this VERIFY run boundary. Historical trades are not deleted and remain visible in Trade History.';

    const progress = document.querySelector('.prop-guard-panel .eval-progress');
    if (progress) progress.style.display = 'none';

    const lucidProgress = document.querySelector('.lucid-progress-card');
    if (lucidProgress) lucidProgress.style.display = 'none';
    const calendarKicker = document.querySelector('.lucid-calendar-panel .section-kicker');
    if (calendarKicker) calendarKicker.textContent = 'REPLAY VERIFICATION';
  }

  const originalRenderEvaluation = window.renderEvaluation;
  if (typeof originalRenderEvaluation === 'function' && !originalRenderEvaluation.__otrVerify72n) {
    const wrapped = function renderEvaluationVerify72n(e) {
      originalRenderEvaluation(e);
      applyVerificationUi(e);
    };
    wrapped.__otrVerify72n = true;
    window.renderEvaluation = wrapped;
  }

  const originalRender = window.render;
  if (typeof originalRender === 'function' && !originalRender.__otrVerifyRun72q) {
    const wrappedRender = function renderVerifyRun72q(snapshot) {
      const mode = String(snapshot?.evaluation?.trading_mode || '').toUpperCase();
      const run = snapshot?.evaluation?.verify_run;
      if (!VERIFY_MODES.has(mode) || !run) {
        return originalRender(snapshot);
      }

      // In VERIFY, the Overview cards are the current experiment scoreboard.
      // Trade History remains the complete historical ledger from snapshot.trades.
      const scopedStats = {
        ...(snapshot.stats || {}),
        ...run,
        today_r: run.total_r,
        today_dollars: run.total_dollars,
      };
      return originalRender({ ...snapshot, stats: scopedStats });
    };
    wrappedRender.__otrVerifyRun72q = true;
    window.render = wrappedRender;
  }
})();
