(() => {
  'use strict';

  const targetCanvases = new Set([
    'otr8OverviewCanvas',
    'primaryExecutionCanvas',
    'pairExecutionCanvas',
    'equityCanvas',
  ]);

  const brighten = new Map([
    ['#666', '#9da8b5'],
    ['#747474', '#9da8b5'],
    ['#6e6e6e', '#9da8b5'],
    ['#686868', '#96a2ae'],
  ]);

  function readableFont(font) {
    const raw = String(font || '');
    const match = raw.match(/(\d+(?:\.\d+)?)px/);
    if (!match) return raw;
    const size = Number(match[1]);
    if (!Number.isFinite(size) || size >= 10.5) return raw;
    return raw.replace(match[0], '10.5px');
  }

  function patchTextMethod(name) {
    const original = CanvasRenderingContext2D.prototype[name];
    if (typeof original !== 'function' || original.__otr80Readable) return;

    function readableText(...args) {
      const canvasId = this?.canvas?.id;
      if (!targetCanvases.has(canvasId)) return original.apply(this, args);

      const oldFont = this.font;
      const oldFill = this.fillStyle;
      this.font = readableFont(oldFont);
      if (typeof oldFill === 'string' && brighten.has(oldFill.toLowerCase())) {
        this.fillStyle = brighten.get(oldFill.toLowerCase());
      }
      try {
        return original.apply(this, args);
      } finally {
        this.font = oldFont;
        this.fillStyle = oldFill;
      }
    }
    readableText.__otr80Readable = true;
    CanvasRenderingContext2D.prototype[name] = readableText;
  }

  patchTextMethod('fillText');
  patchTextMethod('strokeText');

  function tuneCanvas(canvas) {
    if (!canvas || !targetCanvases.has(canvas.id)) return;
    canvas.style.imageRendering = 'auto';
    canvas.style.backfaceVisibility = 'hidden';
    canvas.style.transform = 'translateZ(0)';
  }

  function tuneAll() {
    targetCanvases.forEach((id) => tuneCanvas(document.getElementById(id)));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tuneAll, { once: true });
  } else {
    tuneAll();
  }

  const observer = new MutationObserver(tuneAll);
  observer.observe(document.documentElement, { childList: true, subtree: true });

  // Redraw when browser zoom / monitor DPR changes. Existing chart modules listen to resize.
  let lastDpr = window.devicePixelRatio || 1;
  window.addEventListener('resize', () => {
    const current = window.devicePixelRatio || 1;
    if (Math.abs(current - lastDpr) > 0.01) {
      lastDpr = current;
      requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    }
  });
})();
