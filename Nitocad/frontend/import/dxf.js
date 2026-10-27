// ASCII DXF reader/editor that edits the file's group-code pairs in place, so every entity or table it does
// not understand survives an edit unchanged. Pair = [code:number, value:string, codeLine:string|null].
// Limits (stated in the UI): ASCII DXF only; geometry assumed in world coordinates (OCS extrusion is honoured
// only for arcs/circles/polylines mirrored with extrusion z = -1).
const NUM = (s) => parseFloat(String(s).trim());
const fmt = (n) => { let s = (+n).toFixed(9); if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, ''); return s === '-0' ? '0' : s; };
const rad = (d) => (d * Math.PI) / 180, deg = (r) => (r * 180) / Math.PI;

export function parseDXF(text) {
  if (/^AutoCAD Binary DXF/.test(text)) throw new Error('This is a binary DXF. Save it as an ASCII DXF and open it again.');
  const eol = text.includes('\r\n') ? '\r\n' : '\n';
  const lines = text.split(/\r?\n/);
  const trailing = lines.length && lines[lines.length - 1] === '';
  if (trailing) lines.pop();
  if (lines.length % 2) throw new Error('This DXF file is cut off or damaged (odd number of lines).');
  const pairs = [];
  for (let i = 0; i < lines.length; i += 2) {
    const code = parseInt(lines[i].trim(), 10);
    if (Number.isNaN(code)) throw new Error(`Bad group code on line ${i + 1}.`);
    pairs.push([code, lines[i + 1], lines[i]]);
  }
  return { pairs, eol, trailing };
}

export function serializeDXF(doc) {
  const out = [];
  for (const [c, v, cl] of doc.pairs) { out.push(cl != null ? cl : String(c).padStart(3, ' ')); out.push(v); }
  return out.join(doc.eol) + (doc.trailing ? doc.eol : '');
}

function section(doc, name) {
  const P = doc.pairs;
  for (let i = 0; i + 1 < P.length; i++) {
    if (P[i][0] === 0 && P[i][1].trim() === 'SECTION' && P[i + 1][0] === 2 && P[i + 1][1].trim() === name) {
      let j = i + 2;
      while (j < P.length && !(P[j][0] === 0 && P[j][1].trim() === 'ENDSEC')) j++;
      return [i + 2, j];
    }
  }
  return null;
}

function listEntities(doc, s, e) {
  const P = doc.pairs, out = [];
  let i = s;
  while (i < e) {
    if (P[i][0] !== 0) { i++; continue; }
    const type = P[i][1].trim();
    let j = i + 1;
    if (type === 'POLYLINE') {
      while (j < e && !(P[j][0] === 0 && P[j][1].trim() === 'SEQEND')) j++;
      j++;
      while (j < e && P[j][0] !== 0) j++;
    } else while (j < e && P[j][0] !== 0) j++;
    const get = (c) => { for (let k = i + 1; k < j; k++) if (P[k][0] === c) return P[k][1]; return undefined; };
    out.push({ id: i, start: i, end: j, type, layer: (get(8) || '0').trim(), handle: get(5) && get(5).trim(), colorIdx: get(62) !== undefined ? parseInt(get(62), 10) : 256 });
    i = j;
  }
  return out;
}

export function entities(doc) {
  const r = section(doc, 'ENTITIES');
  return r ? listEntities(doc, r[0], r[1]) : [];
}

export function layers(doc) {
  const t = section(doc, 'TABLES'), res = new Map();
  if (!t) return res;
  const P = doc.pairs;
  for (let i = t[0]; i < t[1]; i++) {
    if (P[i][0] === 0 && P[i][1].trim() === 'LAYER') {
      let name, color = 7, flags = 0;
      for (let k = i + 1; k < t[1] && P[k][0] !== 0; k++) { if (P[k][0] === 2) name = P[k][1].trim(); if (P[k][0] === 62) color = parseInt(P[k][1], 10); if (P[k][0] === 70) flags = parseInt(P[k][1], 10); }
      if (name !== undefined) res.set(name, { color: Math.abs(color), off: color < 0, frozen: !!(flags & 1) });
    }
  }
  return res;
}

const ACI = { 1: '#ff3b30', 2: '#ffd60a', 3: '#30d158', 4: '#64d2ff', 5: '#0a84ff', 6: '#bf5af2', 7: null, 8: '#8e8e93', 9: '#c7c7cc' };
export const aciColor = (n) => (n in ACI ? ACI[n] : n >= 10 ? `hsl(${(n * 37) % 360} 70% 55%)` : null);   // null = use foreground

function pts(P, s, e, cx, cy) { const o = []; let x = null; for (let k = s; k < e; k++) { if (P[k][0] === cx) x = NUM(P[k][1]); else if (P[k][0] === cy && x !== null) { o.push([x, NUM(P[k][1])]); x = null; } } return o; }
const first = (P, s, e, c, d = 0) => { for (let k = s; k < e; k++) if (P[k][0] === c) return NUM(P[k][1]); return d; };
const firstStr = (P, s, e, c, d = '') => { for (let k = s; k < e; k++) if (P[k][0] === c) return P[k][1]; return d; };

// Geometry of one entity in world coordinates. Returns null for unsupported types.
export function geometry(doc, ent) {
  const P = doc.pairs, s = ent.start + 1, e = ent.end, flip = first(P, s, e, 230, 1) < 0;
  const fx = (x) => (flip ? -x : x);
  switch (ent.type) {
    case 'LINE': return { k: 'line', x1: first(P, s, e, 10), y1: first(P, s, e, 20), x2: first(P, s, e, 11), y2: first(P, s, e, 21) };
    case 'POINT': return { k: 'point', x: first(P, s, e, 10), y: first(P, s, e, 20) };
    case 'CIRCLE': return { k: 'circle', cx: fx(first(P, s, e, 10)), cy: first(P, s, e, 20), r: first(P, s, e, 40) };
    case 'ARC': { let a1 = first(P, s, e, 50), a2 = first(P, s, e, 51); if (flip) [a1, a2] = [180 - a2, 180 - a1];
      return { k: 'arc', cx: fx(first(P, s, e, 10)), cy: first(P, s, e, 20), r: first(P, s, e, 40), a1, a2 }; }
    case 'LWPOLYLINE': {
      const v = []; let cur = null;
      for (let k = s; k < e; k++) { const c = P[k][0]; if (c === 10) { cur = [fx(NUM(P[k][1])), 0, 0]; v.push(cur); } else if (c === 20 && cur) cur[1] = NUM(P[k][1]); else if (c === 42 && cur) cur[2] = NUM(P[k][1]); }
      return { k: 'poly', pts: v, closed: !!(first(P, s, e, 70) & 1) };
    }
    case 'POLYLINE': {
      const v = []; let closed = !!(first(P, s, s + 12, 70) & 1);
      for (let k = s; k < e; k++) if (P[k][0] === 0 && P[k][1].trim() === 'VERTEX') {
        let j = k + 1; while (j < e && P[j][0] !== 0) j++;
        if (!(first(P, k + 1, j, 70) & 16)) v.push([first(P, k + 1, j, 10), first(P, k + 1, j, 20), first(P, k + 1, j, 42)]);   // skip spline-frame control vertices
      }
      return { k: 'poly', pts: v, closed };
    }
    case 'TEXT': case 'ATTRIB': case 'ATTDEF': return { k: 'text', x: first(P, s, e, 10), y: first(P, s, e, 20), h: first(P, s, e, 40, 1), rot: first(P, s, e, 50), text: firstStr(P, s, e, 1) };
    case 'MTEXT': { let t = ''; for (let k = s; k < e; k++) if (P[k][0] === 3 || P[k][0] === 1) t += P[k][1];
      return { k: 'text', x: first(P, s, e, 10), y: first(P, s, e, 20), h: first(P, s, e, 40, 1), rot: first(P, s, e, 50), text: t.replace(/\\P/g, ' ').replace(/\\[A-Za-z][^;]*;/g, '').replace(/[{}]/g, '') }; }
    case 'ELLIPSE': return { k: 'ellipse', cx: first(P, s, e, 10), cy: first(P, s, e, 20), mx: first(P, s, e, 11), my: first(P, s, e, 21), ratio: first(P, s, e, 40, 1), t1: first(P, s, e, 41, 0), t2: first(P, s, e, 42, 2 * Math.PI) };
    case 'SPLINE': { const c = pts(P, s, e, 10, 20), f = pts(P, s, e, 11, 21); return { k: 'poly', pts: (f.length ? f : c).map((p) => [p[0], p[1], 0]), closed: false, spline: true }; }
    case 'INSERT': return { k: 'insert', name: firstStr(P, s, e, 2).trim(), x: first(P, s, e, 10), y: first(P, s, e, 20), sx: first(P, s, e, 41, 1), sy: first(P, s, e, 42, 1), rot: first(P, s, e, 50) };
    default: return null;
  }
}

export function blocks(doc) {
  const b = section(doc, 'BLOCKS'), res = new Map();
  if (!b) return res;
  const all = listEntities(doc, b[0], b[1]);
  let cur = null;
  for (const en of all) {
    if (en.type === 'BLOCK') { cur = { name: firstStr(doc.pairs, en.start + 1, en.end, 2).trim(), base: [first(doc.pairs, en.start + 1, en.end, 10), first(doc.pairs, en.start + 1, en.end, 20)], ents: [] }; res.set(cur.name, cur); }
    else if (en.type === 'ENDBLK') cur = null;
    else if (cur) cur.ents.push(en);
  }
  return res;
}

// Everything drawable, INSERTs expanded (depth-limited). Each item keeps the id of its TOP-LEVEL entity.
export function flatten(doc, opts = {}) {
  const lay = layers(doc), blk = blocks(doc), out = [], unsupported = new Set();
  const place = (g, T) => { // T = [a,b,c,d,tx,ty] maps (x,y) -> (a x + c y + tx, b x + d y + ty)
    const ap = (x, y) => [T[0] * x + T[2] * y + T[4], T[1] * x + T[3] * y + T[5]];
    const sc = Math.hypot(T[0], T[1]), ang = deg(Math.atan2(T[1], T[0]));
    switch (g.k) {
      case 'line': { const a = ap(g.x1, g.y1), b = ap(g.x2, g.y2); return { k: 'line', x1: a[0], y1: a[1], x2: b[0], y2: b[1] }; }
      case 'point': { const a = ap(g.x, g.y); return { k: 'point', x: a[0], y: a[1] }; }
      case 'circle': { const a = ap(g.cx, g.cy); return { k: 'circle', cx: a[0], cy: a[1], r: g.r * sc }; }
      case 'arc': { const a = ap(g.cx, g.cy); return { k: 'arc', cx: a[0], cy: a[1], r: g.r * sc, a1: g.a1 + ang, a2: g.a2 + ang }; }
      case 'poly': return { ...g, pts: g.pts.map((p) => { const a = ap(p[0], p[1]); return [a[0], a[1], p[2]]; }) };
      case 'text': { const a = ap(g.x, g.y); return { ...g, x: a[0], y: a[1], h: g.h * sc, rot: g.rot + ang }; }
      case 'ellipse': { const c = ap(g.cx, g.cy), m = [T[0] * g.mx + T[2] * g.my, T[1] * g.mx + T[3] * g.my]; return { ...g, cx: c[0], cy: c[1], mx: m[0], my: m[1] }; }
      default: return g;
    }
  };
  const emit = (ent, T, topId, depth) => {
    const g = geometry(doc, ent);
    if (!g) { unsupported.add(ent.type); return; }
    const L = lay.get(ent.layer);
    if (opts.hideOff !== false && L && (L.off || L.frozen)) return;
    if (g.k === 'insert') {
      const b = blk.get(g.name); if (!b || depth > 5) return;
      const c = Math.cos(rad(g.rot)), s = Math.sin(rad(g.rot));
      const M = [c * g.sx, s * g.sx, -s * g.sy, c * g.sy, g.x, g.y];
      const base = [-b.base[0], -b.base[1]];
      const T2 = [T[0] * M[0] + T[2] * M[1], T[1] * M[0] + T[3] * M[1], T[0] * M[2] + T[2] * M[3], T[1] * M[2] + T[3] * M[3], T[0] * M[4] + T[2] * M[5] + T[4], T[1] * M[4] + T[3] * M[5] + T[5]];
      const T3 = [T2[0], T2[1], T2[2], T2[3], T2[0] * base[0] + T2[2] * base[1] + T2[4], T2[1] * base[0] + T2[3] * base[1] + T2[5]];
      for (const be of b.ents) emit(be, T3, topId, depth + 1);
      return;
    }
    out.push({ id: topId, layer: ent.layer, colorIdx: ent.colorIdx === 256 || ent.colorIdx === 0 ? (L ? L.color : 7) : ent.colorIdx, g: place(g, T) });
  };
  for (const ent of entities(doc)) emit(ent, [1, 0, 0, 1, 0, 0], ent.id, 0);
  return { items: out, unsupported: [...unsupported].sort() };
}

export function extents(items) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const add = (x, y) => { if (Number.isFinite(x) && Number.isFinite(y)) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); } };
  for (const { g } of items) {
    if (g.k === 'line') { add(g.x1, g.y1); add(g.x2, g.y2); }
    else if (g.k === 'circle' || g.k === 'arc') { add(g.cx - g.r, g.cy - g.r); add(g.cx + g.r, g.cy + g.r); }
    else if (g.k === 'poly') g.pts.forEach((p) => add(p[0], p[1]));
    else if (g.k === 'ellipse') { const r = Math.hypot(g.mx, g.my); add(g.cx - r, g.cy - r); add(g.cx + r, g.cy + r); }
    else if (g.k === 'text' || g.k === 'point') add(g.x, g.y);
  }
  return Number.isFinite(x0) ? { x0, y0, x1, y1 } : null;
}

// ---------------- editing -------------------------------------------------------------------------------------
function upsert(P, s, e, code, value) {            // returns how many pairs were inserted
  for (let k = s; k < e; k++) if (P[k][0] === code) { P[k][1] = value; return 0; }
  P.splice(e, 0, [code, value, null]); return 1;
}
const idsOf = (doc, ids) => entities(doc).filter((en) => ids.includes(en.id)).sort((a, b) => b.start - a.start);   // last first: edits never shift unprocessed ranges

// Maps points/vectors/scalars of one entity through p' = c + s R(p - c). Returns false if the type is unsupported.
function xform(doc, en, o) {
  const P = doc.pairs, { dx = 0, dy = 0, rot = 0, scale = 1, cx = 0, cy = 0 } = o;
  const c = Math.cos(rad(rot)), sn = Math.sin(rad(rot));
  const pt = (x, y) => { const rx = x - cx, ry = y - cy; return [cx + scale * (c * rx - sn * ry) + dx, cy + scale * (sn * rx + c * ry) + dy]; };
  const vec = (x, y) => [scale * (c * x - sn * y), scale * (sn * x + c * y)];
  const s = en.start + 1, e = en.end;
  const movePt = (cxc, cyc, from = s, to = e) => { for (let k = from; k < to; k++) if (P[k][0] === cxc) { let m = k + 1; while (m < to && P[m][0] !== cyc) m++; if (m < to) { const q = pt(NUM(P[k][1]), NUM(P[m][1])); P[k][1] = fmt(q[0]); P[m][1] = fmt(q[1]); } } };
  const moveVec = (cxc, cyc) => { for (let k = s; k < e; k++) if (P[k][0] === cxc) { let m = k + 1; while (m < e && P[m][0] !== cyc) m++; if (m < e) { const q = vec(NUM(P[k][1]), NUM(P[m][1])); P[k][1] = fmt(q[0]); P[m][1] = fmt(q[1]); } } };
  const mulCode = (code) => { for (let k = s; k < e; k++) if (P[k][0] === code) P[k][1] = fmt(NUM(P[k][1]) * scale); };
  const addAngle = (code) => { if (rot) { let n = 0; for (let k = s; k < e; k++) if (P[k][0] === code) { P[k][1] = fmt(NUM(P[k][1]) + rot); n++; } return n; } return 0; };
  switch (en.type) {
    case 'LINE': movePt(10, 20); movePt(11, 21); return true;
    case 'POINT': movePt(10, 20); return true;
    case 'CIRCLE': movePt(10, 20); mulCode(40); return true;
    case 'ARC': movePt(10, 20); mulCode(40); addAngle(50); addAngle(51); return true;
    case 'LWPOLYLINE': movePt(10, 20); return true;
    case 'POLYLINE': for (let k = s; k < e; k++) if (P[k][0] === 0 && P[k][1].trim() === 'VERTEX') { let j = k + 1; while (j < e && P[j][0] !== 0) j++; movePt(10, 20, k + 1, j); } return true;
    case 'TEXT': case 'ATTRIB': case 'ATTDEF': movePt(10, 20); movePt(11, 21); mulCode(40); if (rot && !addAngle(50)) upsert(P, s, e, 50, fmt(rot)); return true;
    case 'MTEXT': movePt(10, 20); moveVec(11, 21); mulCode(40); if (rot) { if (!addAngle(50) && !P.slice(s, e).some((p) => p[0] === 11)) upsert(P, s, e, 50, fmt(rot)); } return true;
    case 'ELLIPSE': movePt(10, 20); moveVec(11, 21); return true;
    case 'SPLINE': movePt(10, 20); movePt(11, 21); moveVec(12, 22); moveVec(13, 23); return true;
    case 'INSERT': {
      movePt(10, 20);
      if (scale !== 1) { for (const code of [41, 42, 43]) { const had = P.slice(s, e).some((p) => p[0] === code); if (had) mulCode(code); else upsert(P, s, e, code, fmt(scale)); } }
      if (rot) { if (!addAngle(50)) upsert(P, s, e, 50, fmt(rot)); }
      return true;
    }
    default: return false;
  }
}

export function transformEntities(doc, ids, o) {
  const skipped = new Set(); let changed = 0;
  for (const en of idsOf(doc, ids)) { if (xform(doc, en, o)) changed++; else skipped.add(en.type); }
  return { changed, skipped: [...skipped].sort() };
}
export const moveEntities = (doc, ids, dx, dy) => transformEntities(doc, ids, { dx, dy });
export const rotateEntities = (doc, ids, deg, cx, cy) => transformEntities(doc, ids, { rot: deg, cx, cy });
export const scaleEntities = (doc, ids, factor, cx, cy) => transformEntities(doc, ids, { scale: factor, cx, cy });

export function deleteEntities(doc, ids) {
  let n = 0;
  for (const en of idsOf(doc, ids)) { doc.pairs.splice(en.start, en.end - en.start); n++; }
  return n;
}

export function ensureLayer(doc, name, color = 7) {
  if (layers(doc).has(name)) return;
  const t = section(doc, 'TABLES'); if (!t) return;
  const P = doc.pairs;
  for (let i = t[0]; i < t[1]; i++) if (P[i][0] === 2 && P[i][1].trim() === 'LAYER' && P[i - 1][0] === 0 && P[i - 1][1].trim() === 'TABLE') {
    let j = i; while (j < t[1] && !(P[j][0] === 0 && P[j][1].trim() === 'ENDTAB')) j++;
    const rec = [[0, 'LAYER'], [2, name], [70, '0'], [62, String(color)], [6, 'CONTINUOUS']];
    if (P.slice(i, j).some((p) => p[0] === 100)) rec.splice(2, 0, [100, 'AcDbSymbolTableRecord'], [100, 'AcDbLayerTableRecord']);
    P.splice(j, 0, ...rec.map(([c, v]) => [c, v, null]));
    return;
  }
}
export function setLayer(doc, ids, layer) {
  const before = doc.pairs.length;
  ensureLayer(doc, layer);                       // inserts into TABLES, which precedes ENTITIES: every entity id shifts
  const shift = doc.pairs.length - before;
  ids = ids.map((i) => i + shift);
  let n = 0;
  for (const en of idsOf(doc, ids)) { upsert(doc.pairs, en.start + 1, en.end, 8, layer); n++; }
  return n;
}

function nextHandle(doc) {
  let max = 0;
  for (const p of doc.pairs) if (p[0] === 5 || p[0] === 105) { const v = parseInt(p[1], 16); if (v > max) max = v; }
  const seed = doc.pairs.findIndex((p, i) => p[0] === 9 && p[1].trim() === '$HANDSEED');
  if (seed >= 0) max = Math.max(max, parseInt(doc.pairs[seed + 1][1], 16) - 1);
  const h = max + 1;
  if (seed >= 0) doc.pairs[seed + 1][1] = (h + 1).toString(16).toUpperCase();
  return h.toString(16).toUpperCase();
}

function append(doc, type, body) {
  const r = section(doc, 'ENTITIES'); if (!r) throw new Error('This DXF has no ENTITIES section to add to.');
  const hasHandles = doc.pairs.some((p) => p[0] === 5);
  const rec = [[0, type]];
  if (hasHandles) rec.push([5, nextHandle(doc)]);
  rec.push(...body);
  const at = r[1];
  doc.pairs.splice(at, 0, ...rec.map(([c, v]) => [c, String(v), null]));
  return at;                                                // id of the new entity (its first pair)
}
const ly = (l) => [8, l || '0'];
export const addLine = (d, a) => append(d, 'LINE', [ly(a.layer), [10, fmt(a.x1)], [20, fmt(a.y1)], [30, '0'], [11, fmt(a.x2)], [21, fmt(a.y2)], [31, '0']]);
export const addCircle = (d, a) => append(d, 'CIRCLE', [ly(a.layer), [10, fmt(a.cx)], [20, fmt(a.cy)], [30, '0'], [40, fmt(a.r)]]);
export const addArc = (d, a) => append(d, 'ARC', [ly(a.layer), [10, fmt(a.cx)], [20, fmt(a.cy)], [30, '0'], [40, fmt(a.r)], [50, fmt(a.a1)], [51, fmt(a.a2)]]);
export const addText = (d, a) => append(d, 'TEXT', [ly(a.layer), [10, fmt(a.x)], [20, fmt(a.y)], [30, '0'], [40, fmt(a.h || 2.5)], [1, a.text], [50, fmt(a.rot || 0)]]);
export function addPolyline(d, a) {
  const body = [ly(a.layer), [100, 'AcDbPolyline'], [90, a.pts.length], [70, a.closed ? 1 : 0]];
  for (const p of a.pts) { body.push([10, fmt(p[0])], [20, fmt(p[1])]); if (p[2]) body.push([42, fmt(p[2])]); }
  return append(d, 'LWPOLYLINE', d.pairs.some((p) => p[0] === 5) ? body : body.filter((b) => b[0] !== 100));
}
export const addRect = (d, a) => addPolyline(d, { layer: a.layer, closed: true, pts: [[a.x1, a.y1], [a.x2, a.y1], [a.x2, a.y2], [a.x1, a.y2]] });
