// Pure 2D geometry helpers shared by the DXF and KiCad editors: hit-testing, bulge/three-point arcs, boxes.
export const dist = (ax, ay, bx, by) => Math.hypot(ax - bx, ay - by);
export function segDist(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1, l2 = dx * dx + dy * dy;
  const t = l2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / l2));
  return dist(px, py, x1 + t * dx, y1 + t * dy);
}
const norm360 = (a) => ((a % 360) + 360) % 360;
export function onArc(angDeg, a1, a2) { const a = norm360(angDeg), s = norm360(a1), e = norm360(a2); return s <= e ? a >= s && a <= e : a >= s || a <= e; }   // CCW from a1 to a2
export function arcDist(px, py, cx, cy, r, a1, a2) {
  const ang = (Math.atan2(py - cy, px - cx) * 180) / Math.PI;
  if (onArc(ang, a1, a2)) return Math.abs(dist(px, py, cx, cy) - r);
  const p = (a) => [cx + r * Math.cos((a * Math.PI) / 180), cy + r * Math.sin((a * Math.PI) / 180)];
  const [sx, sy] = p(a1), [ex, ey] = p(a2); return Math.min(dist(px, py, sx, sy), dist(px, py, ex, ey));
}
// DXF bulge arc between two vertices -> points along it (bulge = tan(sweep/4), positive = counter-clockwise).
export function bulgePoints(x1, y1, x2, y2, bulge, step = 0.12) {
  if (!bulge) return [[x2, y2]];
  const theta = 4 * Math.atan(bulge), c = Math.hypot(x2 - x1, y2 - y1), r = c / (2 * Math.sin(Math.abs(theta) / 2));
  const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, h = Math.sqrt(Math.max(0, r * r - (c / 2) ** 2)) * (Math.abs(theta) > Math.PI ? -1 : 1) * Math.sign(bulge);
  const cx = mx - (h * (y2 - y1)) / c, cy = my + (h * (x2 - x1)) / c;
  const a0 = Math.atan2(y1 - cy, x1 - cx), n = Math.max(2, Math.ceil(Math.abs(theta) / step)), out = [];
  for (let i = 1; i <= n; i++) { const a = a0 + (theta * i) / n; out.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); }
  out[out.length - 1] = [x2, y2]; return out;
}
export function polyPoints(pts, closed) {
  const out = pts.length ? [[pts[0][0], pts[0][1]]] : [];
  const n = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < n; i++) { const a = pts[i], b = pts[(i + 1) % pts.length]; out.push(...bulgePoints(a[0], a[1], b[0], b[1], a[2] || 0)); }
  return out;
}
// Circle through three points (KiCad arcs): centre, radius, start/end angle (radians) and whether the arc runs counter-clockwise.
export function circleFrom3(a, m, b) {
  const d = 2 * (a[0] * (m[1] - b[1]) + m[0] * (b[1] - a[1]) + b[0] * (a[1] - m[1]));
  if (Math.abs(d) < 1e-12) return null;
  const ux = ((a[0] ** 2 + a[1] ** 2) * (m[1] - b[1]) + (m[0] ** 2 + m[1] ** 2) * (b[1] - a[1]) + (b[0] ** 2 + b[1] ** 2) * (a[1] - m[1])) / d;
  const uy = ((a[0] ** 2 + a[1] ** 2) * (b[0] - m[0]) + (m[0] ** 2 + m[1] ** 2) * (a[0] - b[0]) + (b[0] ** 2 + b[1] ** 2) * (m[0] - a[0])) / d;
  const a0 = Math.atan2(a[1] - uy, a[0] - ux), am = Math.atan2(m[1] - uy, m[0] - ux), a1 = Math.atan2(b[1] - uy, b[0] - ux);
  const tw = (x) => ((x % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  const ccw = tw(am - a0) < tw(a1 - a0);
  return { cx: ux, cy: uy, r: dist(a[0], a[1], ux, uy), a0, a1, ccw };
}
export function pointInPoly(x, y, pts) {
  let c = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) if ((pts[i][1] > y) !== (pts[j][1] > y) && x < ((pts[j][0] - pts[i][0]) * (y - pts[i][1])) / (pts[j][1] - pts[i][1]) + pts[i][0]) c = !c;
  return c;
}
export const boxOf = (a, b) => ({ x0: Math.min(a[0], b[0]), y0: Math.min(a[1], b[1]), x1: Math.max(a[0], b[0]), y1: Math.max(a[1], b[1]) });
export const boxHit = (b, p) => p[0] >= b.x0 && p[0] <= b.x1 && p[1] >= b.y0 && p[1] <= b.y1;

// Distance from a world point to a DXF geometry record (see dxf.geometry), in world units. Infinity if not pickable.
export function hitDistance(g, x, y) {
  switch (g.k) {
    case 'line': return segDist(x, y, g.x1, g.y1, g.x2, g.y2);
    case 'point': return dist(x, y, g.x, g.y);
    case 'circle': return Math.abs(dist(x, y, g.cx, g.cy) - g.r);
    case 'arc': return arcDist(x, y, g.cx, g.cy, g.r, g.a1, g.a2);
    case 'poly': { const p = polyPoints(g.pts, g.closed); let m = Infinity; for (let i = 0; i + 1 < p.length; i++) m = Math.min(m, segDist(x, y, p[i][0], p[i][1], p[i + 1][0], p[i + 1][1])); return m; }
    case 'ellipse': { let m = Infinity; const pts = ellipsePoints(g, 96); for (let i = 0; i + 1 < pts.length; i++) m = Math.min(m, segDist(x, y, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])); return m; }
    case 'text': { const w = (g.text || '').length * g.h * 0.6; const c = Math.cos((-g.rot * Math.PI) / 180), s = Math.sin((-g.rot * Math.PI) / 180), dx = x - g.x, dy = y - g.y, lx = dx * c - dy * s, ly = dx * s + dy * c; return lx >= 0 && lx <= w && ly >= 0 && ly <= g.h ? 0 : Math.min(dist(x, y, g.x, g.y), 1e9); }
    default: return Infinity;
  }
}
export function ellipsePoints(g, n = 96) {
  const maj = Math.hypot(g.mx, g.my), rot = Math.atan2(g.my, g.mx), min = maj * g.ratio, out = [];
  let t1 = g.t1, t2 = g.t2; if (t2 <= t1) t2 += 2 * Math.PI;
  for (let i = 0; i <= n; i++) { const t = t1 + ((t2 - t1) * i) / n, ex = maj * Math.cos(t), ey = min * Math.sin(t); out.push([g.cx + ex * Math.cos(rot) - ey * Math.sin(rot), g.cy + ex * Math.sin(rot) + ey * Math.cos(rot)]); }
  return out;
}
export function itemBox(g) {
  const pts = [];
  if (g.k === 'line') pts.push([g.x1, g.y1], [g.x2, g.y2]); else if (g.k === 'point' || g.k === 'text') pts.push([g.x, g.y]);
  else if (g.k === 'circle' || g.k === 'arc') pts.push([g.cx - g.r, g.cy - g.r], [g.cx + g.r, g.cy + g.r]);
  else if (g.k === 'poly') pts.push(...polyPoints(g.pts, g.closed)); else if (g.k === 'ellipse') pts.push(...ellipsePoints(g, 48));
  if (!pts.length) return null;
  return { x0: Math.min(...pts.map((p) => p[0])), y0: Math.min(...pts.map((p) => p[1])), x1: Math.max(...pts.map((p) => p[0])), y1: Math.max(...pts.map((p) => p[1])) };
}
