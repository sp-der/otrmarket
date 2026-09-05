(() => {
  const HEALTHY_FOR_MS = 7000;
  const POLL_EVERY_MS = 1000;
  let lastHealthyAt = 0;
  let polling = false;

  function nodes() {
    return {
      text: document.getElementById('connectionText'),
      dot: document.getElementById('connectionDot'),
    };
  }

  function paintLive() {
    const { text, dot } = nodes();
    if (!text || !dot) return;
    text.textContent = 'Live dashboard';
    dot.classList.remove('offline');
    dot.classList.add('live');
  }

  function paintOffline() {
    const { text, dot } = nodes();
    if (!text || !dot) return;
    text.textContent = 'Reconnecting';
    dot.classList.remove('live');
    dot.classList.add('offline');
  }

  function healthyRecently() {
    return Date.now() - lastHealthyAt <= HEALTHY_FOR_MS;
  }

  // app.js was written for a direct Railway websocket. Behind the Vercel /gold
  // reverse proxy that upgrade path can fail even while every HTTP API is healthy.
  // Keep app.js from overwriting a healthy polling state with a cosmetic WS error.
  const originalSetConnection = typeof window.setConnection === 'function' ? window.setConnection : null;
  window.setConnection = function patchedSetConnection(status) {
    if ((status === 'offline' || status === 'connecting') && healthyRecently()) {
      paintLive();
      return;
    }
    if (originalSetConnection) {
      originalSetConnection(status);
      return;
    }
    if (status === 'live') paintLive();
    else if (status === 'offline') paintOffline();
  };

  async function pollSnapshot() {
    if (polling) return;
    polling = true;
    try {
      const response = await fetch(`/market/api/snapshot?connection_probe=${Date.now()}`, {
        cache: 'no-store',
        credentials: 'same-origin',
      });
      if (!response.ok) {
        if (!healthyRecently()) paintOffline();
        return;
      }

      const snapshot = await response.json();
      lastHealthyAt = Date.now();

      // This is the important part: drive the exact same renderer that the
      // websocket used so markets, P/L, trades, scanner, queue and EVAL cards
      // keep updating on /gold instead of only changing the status pill.
      if (typeof window.render === 'function') {
        window.render(snapshot);
      } else if (typeof render === 'function') {
        render(snapshot);
      }
      paintLive();
    } catch (_) {
      if (!healthyRecently()) paintOffline();
    } finally {
      polling = false;
    }
  }

  function enforceStatus() {
    if (healthyRecently()) paintLive();
  }

  function start() {
    pollSnapshot();
    setInterval(pollSnapshot, POLL_EVERY_MS);
    setInterval(enforceStatus, 250);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, { once: true });
  } else {
    start();
  }
})();
