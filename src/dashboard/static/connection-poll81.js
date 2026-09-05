(() => {
  const HEALTHY_FOR_MS = 8000;
  const PROBE_EVERY_MS = 2500;
  let lastHealthyAt = 0;
  let probing = false;

  function connectionNodes() {
    return {
      text: document.getElementById('connectionText'),
      dot: document.getElementById('connectionDot'),
    };
  }

  function renderHealthy() {
    const { text, dot } = connectionNodes();
    if (!text || !dot) return;
    text.textContent = 'Live dashboard';
    dot.classList.remove('offline');
    dot.classList.add('live');
  }

  function renderOffline() {
    const { text, dot } = connectionNodes();
    if (!text || !dot) return;
    text.textContent = 'Reconnecting';
    dot.classList.remove('live');
    dot.classList.add('offline');
  }

  function enforceHealthDisplay() {
    if (Date.now() - lastHealthyAt <= HEALTHY_FOR_MS) renderHealthy();
  }

  async function probe() {
    if (probing) return;
    probing = true;
    try {
      const separator = '/market/api/snapshot'.includes('?') ? '&' : '?';
      const response = await fetch(`/market/api/snapshot${separator}connection_probe=${Date.now()}`, {
        cache: 'no-store',
        credentials: 'same-origin',
      });
      if (response.ok) {
        lastHealthyAt = Date.now();
        renderHealthy();
      } else if (Date.now() - lastHealthyAt > HEALTHY_FOR_MS) {
        renderOffline();
      }
    } catch (_) {
      if (Date.now() - lastHealthyAt > HEALTHY_FOR_MS) renderOffline();
    } finally {
      probing = false;
    }
  }

  function start() {
    probe();
    setInterval(probe, PROBE_EVERY_MS);
    setInterval(enforceHealthDisplay, 400);

    const target = document.getElementById('connectionText');
    if (target) {
      const observer = new MutationObserver(enforceHealthDisplay);
      observer.observe(target, { childList: true, characterData: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
