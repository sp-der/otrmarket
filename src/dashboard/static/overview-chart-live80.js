(() => {
  'use strict';

  const nativeFetch = window.fetch.bind(window);

  function isOtrLiveRead(input) {
    return typeof input === 'string' && (
      input.startsWith('/market/api/chart') ||
      input.startsWith('/market/api/otr8') ||
      input.startsWith('/market/api/snapshot')
    );
  }

  function freshUrl(url) {
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}otr8_live=${Date.now()}`;
  }

  window.fetch = (input, init = {}) => {
    if (!isOtrLiveRead(input)) return nativeFetch(input, init);
    const headers = new Headers(init.headers || {});
    headers.set('Cache-Control', 'no-cache, no-store, max-age=0');
    headers.set('Pragma', 'no-cache');
    return nativeFetch(freshUrl(input), {
      ...init,
      cache: 'no-store',
      headers,
    });
  };

  const redraw = () => window.dispatchEvent(new Event('resize'));

  window.addEventListener('focus', redraw);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) redraw();
  });

  // The main chart polls every two seconds. This observer only fixes canvas
  // dimensions when the TradingView-style splitter/height handle moves.
  const attach = () => {
    const panel = document.getElementById('otr8OverviewChart');
    if (!panel || panel.dataset.otr8LiveRefreshReady === '1') return false;
    panel.dataset.otr8LiveRefreshReady = '1';
    const observer = new ResizeObserver(redraw);
    observer.observe(panel);
    const canvasWrap = panel.querySelector('.otr8-canvas-wrap');
    if (canvasWrap) observer.observe(canvasWrap);
    redraw();
    return true;
  };

  if (!attach()) {
    const observer = new MutationObserver(() => {
      if (attach()) observer.disconnect();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }
})();
