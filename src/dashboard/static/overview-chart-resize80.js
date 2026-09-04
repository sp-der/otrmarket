(() => {
  'use strict';

  const HEIGHT_KEY = 'otr8.overview.chartHeight';
  const SIDE_KEY = 'otr8.overview.sideWidth';
  const DEFAULT_HEIGHT = 520;
  const DEFAULT_SIDE = 310;
  const MIN_HEIGHT = 320;
  const MAX_HEIGHT = 900;
  const MIN_SIDE = 240;
  const MIN_CHART = 520;

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const readStored = (key, fallback) => {
    try {
      const value = Number(localStorage.getItem(key));
      return Number.isFinite(value) ? value : fallback;
    } catch (_) {
      return fallback;
    }
  };
  const saveStored = (key, value) => {
    try { localStorage.setItem(key, String(Math.round(value))); } catch (_) { /* storage is optional */ }
  };

  let redrawFrame = 0;
  function redraw() {
    if (redrawFrame) cancelAnimationFrame(redrawFrame);
    redrawFrame = requestAnimationFrame(() => {
      redrawFrame = 0;
      window.dispatchEvent(new Event('resize'));
    });
  }

  function maxSideWidth(shell) {
    return Math.max(MIN_SIDE, shell.clientWidth - MIN_CHART - 16);
  }

  function setHeight(panel, value, persist = false) {
    const height = clamp(Number(value) || DEFAULT_HEIGHT, MIN_HEIGHT, MAX_HEIGHT);
    panel.style.setProperty('--otr8-chart-height', `${Math.round(height)}px`);
    if (persist) saveStored(HEIGHT_KEY, height);
    redraw();
    return height;
  }

  function setSideWidth(panel, shell, value, persist = false) {
    const width = clamp(Number(value) || DEFAULT_SIDE, MIN_SIDE, maxSideWidth(shell));
    panel.style.setProperty('--otr8-side-width', `${Math.round(width)}px`);
    if (persist) saveStored(SIDE_KEY, width);
    redraw();
    return width;
  }

  function installDrag(handle, bodyClass, onMove, onEnd) {
    handle.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      handle.classList.add('is-dragging');
      document.body.classList.add(bodyClass);
      handle.setPointerCapture?.(event.pointerId);
      const start = { x: event.clientX, y: event.clientY };

      const move = (next) => onMove(next, start);
      const end = (next) => {
        handle.classList.remove('is-dragging');
        document.body.classList.remove(bodyClass);
        handle.removeEventListener('pointermove', move);
        handle.removeEventListener('pointerup', end);
        handle.removeEventListener('pointercancel', end);
        onEnd?.(next, start);
      };
      handle.addEventListener('pointermove', move);
      handle.addEventListener('pointerup', end);
      handle.addEventListener('pointercancel', end);
    });
  }

  function attachResizeWorkspace() {
    const panel = document.getElementById('otr8OverviewChart');
    const shell = panel?.querySelector('.otr8-chart-shell');
    const card = shell?.querySelector('.otr8-chart-card');
    const side = shell?.querySelector('.otr8-chart-side');
    if (!panel || !shell || !card || !side || shell.dataset.otr8ResizeReady === '1') return false;

    shell.dataset.otr8ResizeReady = '1';
    shell.classList.add('otr8-resize-ready');

    const splitter = document.createElement('div');
    splitter.className = 'otr8-chart-splitter';
    splitter.setAttribute('role', 'separator');
    splitter.setAttribute('aria-orientation', 'vertical');
    splitter.setAttribute('aria-label', 'Resize Gold chart width');
    splitter.tabIndex = 0;
    shell.insertBefore(splitter, side);

    const heightHandle = document.createElement('div');
    heightHandle.className = 'otr8-chart-height-handle';
    heightHandle.setAttribute('role', 'separator');
    heightHandle.setAttribute('aria-orientation', 'horizontal');
    heightHandle.setAttribute('aria-label', 'Resize Gold chart height');
    heightHandle.tabIndex = 0;
    card.appendChild(heightHandle);

    let currentHeight = setHeight(panel, readStored(HEIGHT_KEY, DEFAULT_HEIGHT));
    let currentSide = setSideWidth(panel, shell, readStored(SIDE_KEY, DEFAULT_SIDE));

    installDrag(splitter, 'otr8-resizing-x', (event, start) => {
      const delta = event.clientX - start.x;
      setSideWidth(panel, shell, currentSide - delta);
    }, (event, start) => {
      const delta = event.clientX - start.x;
      currentSide = setSideWidth(panel, shell, currentSide - delta, true);
    });

    installDrag(heightHandle, 'otr8-resizing-y', (event, start) => {
      const delta = event.clientY - start.y;
      setHeight(panel, currentHeight + delta);
    }, (event, start) => {
      const delta = event.clientY - start.y;
      currentHeight = setHeight(panel, currentHeight + delta, true);
    });

    splitter.addEventListener('dblclick', () => { currentSide = setSideWidth(panel, shell, DEFAULT_SIDE, true); });
    heightHandle.addEventListener('dblclick', () => { currentHeight = setHeight(panel, DEFAULT_HEIGHT, true); });

    splitter.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      event.preventDefault();
      const step = event.shiftKey ? 50 : 20;
      currentSide = setSideWidth(panel, shell, currentSide + (event.key === 'ArrowLeft' ? step : -step), true);
    });
    heightHandle.addEventListener('keydown', (event) => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const step = event.shiftKey ? 60 : 30;
      currentHeight = setHeight(panel, currentHeight + (event.key === 'ArrowDown' ? step : -step), true);
    });

    const observer = new ResizeObserver(() => {
      currentSide = setSideWidth(panel, shell, currentSide);
      redraw();
    });
    observer.observe(shell);
    return true;
  }

  function boot() {
    if (attachResizeWorkspace()) return;
    const observer = new MutationObserver(() => {
      if (attachResizeWorkspace()) observer.disconnect();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
