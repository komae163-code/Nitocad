// Pan/zoom 2D canvas used by the DXF, KiCad and Gerber editors. Mouse: wheel zooms, middle/right/space-drag or the
// Pan tool pans. Touch: two fingers pan + pinch, one finger drives the active tool. World -> screen is
//   sx = ox + x*scale,  sy = oy + y*scale*(flipY ? -1 : 1)
export class View2D {
  constructor(canvas, { flipY = false, onDraw = () => {}, onTool = () => {} } = {}) {
    Object.assign(this, { canvas, flipY, onDraw, onTool, scale: 1, ox: 0, oy: 0, panMode: false, pointers: new Map(), dpr: 1, w: 0, h: 0 });
    this._bind();
  }
  toScreen(x, y) { return [this.ox + x * this.scale, this.oy + (this.flipY ? -y : y) * this.scale]; }
  toWorld(px, py) { return [(px - this.ox) / this.scale, ((py - this.oy) / this.scale) * (this.flipY ? -1 : 1)]; }
  resize() {
    const r = this.canvas.getBoundingClientRect(); this.dpr = window.devicePixelRatio || 1;
    this.w = Math.max(1, Math.round(r.width)); this.h = Math.max(1, Math.round(r.height));
    this.canvas.width = Math.round(this.w * this.dpr); this.canvas.height = Math.round(this.h * this.dpr); this.draw();
  }
  fit(b, pad = 24) {
    if (!b || !(b.x1 > b.x0 || b.y1 > b.y0)) { this.scale = 1; this.ox = this.w / 2; this.oy = this.h / 2; return this.draw(); }
    const sw = (this.w - 2 * pad) / Math.max(1e-9, b.x1 - b.x0), sh = (this.h - 2 * pad) / Math.max(1e-9, b.y1 - b.y0);
    this.scale = Math.min(sw, sh); const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
    this.ox = this.w / 2 - cx * this.scale; this.oy = this.h / 2 - (this.flipY ? -cy : cy) * this.scale; this.draw();
  }
  zoomAt(px, py, k) {
    const [wx, wy] = this.toWorld(px, py); this.scale = Math.min(1e9, Math.max(1e-6, this.scale * k));
    this.ox = px - wx * this.scale; this.oy = py - (this.flipY ? -wy : wy) * this.scale; this.draw();
  }
  pan(dx, dy) { this.ox += dx; this.oy += dy; this.draw(); }
  draw() {
    const c = this.canvas.getContext('2d'); c.setTransform(this.dpr, 0, 0, this.dpr, 0, 0); c.clearRect(0, 0, this.w, this.h);
    this.onDraw(c, this);
  }
  _pos(e) { const r = this.canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  _bind() {
    const cv = this.canvas; cv.style.touchAction = 'none';
    cv.addEventListener('wheel', (e) => { e.preventDefault(); const [px, py] = this._pos(e); this.zoomAt(px, py, Math.exp(-e.deltaY * 0.0015)); }, { passive: false });
    cv.addEventListener('contextmenu', (e) => e.preventDefault());
    cv.addEventListener('pointerdown', (e) => {
      cv.setPointerCapture(e.pointerId); const p = this._pos(e);
      this.pointers.set(e.pointerId, { x: p[0], y: p[1], px: p[0], py: p[1], button: e.button });
      this.mode = this.pointers.size >= 2 ? 'gesture' : (e.button === 1 || e.button === 2 || this.panMode || e.shiftKey && e.altKey ? 'pan' : 'tool');
      if (this.pointers.size >= 2) { this.onTool('cancel', null, e); }
      else if (this.mode === 'tool') this.onTool('down', p, e);
    });
    cv.addEventListener('pointermove', (e) => {
      const q = this.pointers.get(e.pointerId); const p = this._pos(e);
      if (!q) return this.onTool('hover', p, e);
      const dx = p[0] - q.x, dy = p[1] - q.y; q.x = p[0]; q.y = p[1];
      if (this.pointers.size >= 2) {
        const [a, b] = [...this.pointers.values()]; const d1 = Math.hypot(a.x - b.x, a.y - b.y), d0 = Math.hypot(a.px - b.px, a.py - b.py);
        if (e.pointerId === [...this.pointers.keys()][0]) { this.pan(dx / 2, dy / 2); } else { this.pan(dx / 2, dy / 2); }
        if (d0 > 0 && d1 > 0) this.zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, 1 + (d1 - d0) / d0 * 0.5);
        a.px = a.x; a.py = a.y; b.px = b.x; b.py = b.y;
      } else if (this.mode === 'pan') this.pan(dx, dy);
      else this.onTool('move', p, e);
    });
    const up = (e) => { const q = this.pointers.get(e.pointerId); if (!q) return; const p = this._pos(e); const was = this.mode; this.pointers.delete(e.pointerId); if (was === 'tool' && this.pointers.size === 0) this.onTool('up', p, e); if (this.pointers.size === 0) this.mode = null; };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => this.resize()).observe(cv);
  }
}
