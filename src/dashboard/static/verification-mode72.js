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

  function setLabelFor(id, label) {
    const value = document.getElementById(id);
    const labelNode = value?.previousElementSibling;
    if (labelNode) labelNode.textContent = label;
  }

  function applyVerificationUi(e) {
    const mode = String(e?.trading_mode || '').toUpperCase();
    if (!VERIFY_MODES.has(mode)) return;

    const panel = document.querySelector('.prop-guard-panel');
    if (panel) {
      const kicker = panel.querySelector('.section-kicker');
      const title = panel.querySelector('h2');
      if (kicker) kicker.textContent = 'BOT VERIFICATION';
      if (title) title.textContent = 'Continuous Verification';
    }

    const profile = document.getElementById('evalProfile');
    const status = document.getElementById('evalStatus');
    if (profile) profile.textContent = 'GC REPLAY · VERIFY';
    if (status) {
      status.textContent = 'ACTIVE';
      status.className = 'eval-status-chip eval-active';
    }

    setLabelFor('evalBalance', 'Tracked Balance');
    setLabelFor('evalTarget', 'Run P/L');
    setLabelFor('evalCushion', 'Account Limits');
    setLabelFor('evalToday', 'Today');
    setLabelFor('evalRisk', 'Risk / Trade');
    setLabelFor('evalTrades', 'Session');

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

    if (balance) balance.textContent = money(e.balance);
    if (pnl) pnl.textContent = `P&L ${money(e.realized_pnl, true)}`;
    if (target) target.textContent = money(e.realized_pnl, true);
    if (progressText) progressText.textContent = 'No pass target during verification';
    if (cushion) cushion.textContent = 'OFF';
    if (floor) floor.textContent = 'Eval / funded account locks are bypassed';
    if (today) today.textContent = money(e.today_pnl, true);
    if (dailyStop) dailyStop.textContent = 'No daily loss lock in VERIFY';
    if (risk) risk.textContent = money(e.available_risk ?? e.verify_risk_per_trade);
    if (committed) committed.textContent = `Committed ${money(e.committed_risk || 0)}`;
    if (trades) trades.textContent = 'Unlimited';
    if (lossStreak) lossStreak.textContent = 'No session / loss-streak governor';
    if (reason) reason.textContent = 'Continuous verification is active. Strategy quality, market-session rules, structural risk geometry, and one active position at a time still apply.';

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
})();
