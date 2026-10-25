// KiCad s-expression engine: parse/serialise any KiCad file (.kicad_pcb/.kicad_sch/.kicad_sym/.kicad_mod/...),
// a PCB model for drawing, and structured edits. Items: string {s}, atom {a}, node {name,args}.
export class KiCadError extends Error {}
export const isNode = (x) => x && typeof x === 'object' && 'args' in x;
export const S = (v) => ({ s: String(v) });
export const A = (v) => ({ a: String(v) });

export function parse(text) {
  let i = 0; const n = text.length; let nodes = 0;
  const ws = () => { for (;;) { while (i < n && /\s/.test(text[i])) i++; if (text[i] === '#') { while (i < n && text[i] !== '\n') i++; } else break; } };
  function list(depth) {
    if (depth > 200) throw new KiCadError('File is nested too deeply.');
    if (++nodes > 3_000_000) throw new KiCadError('File is too large to edit here.');
    i++; ws();                                      // consume "("
    let name = '';
    if (text[i] !== ')' && text[i] !== '(' && text[i] !== '"') { const st = i; while (i < n && !/[\s()]/.test(text[i])) i++; name = text.slice(st, i); }
    const node = { name, args: [] };
    for (;;) {
      ws();
      if (i >= n) throw new KiCadError(`Unbalanced parentheses: "(${name}" is never closed.`);
      const c = text[i];
      if (c === ')') { i++; return node; }
      if (c === '(') node.args.push(list(depth + 1));
      else if (c === '"') {
        let out = ''; i++;
        while (i < n && text[i] !== '"') { if (text[i] === '\\' && i + 1 < n) { const e = text[i + 1]; out += e === 'n' ? '\n' : e === 't' ? '\t' : e; i += 2; } else out += text[i++]; }
        if (i >= n) throw new KiCadError('Unterminated quoted string.');
        i++; node.args.push({ s: out });
      } else { const st = i; while (i < n && !/[\s()]/.test(text[i])) i++; node.args.push({ a: text.slice(st, i) }); }
    }
  }
  ws();
  if (text[i] !== '(') throw new KiCadError('This does not look like a KiCad file (it must start with "(").');
  const root = list(0); ws();
  if (i < n) throw new KiCadError('Unexpected content after the end of the file.');
  return root;
}

const q = (s) => '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n').replace(/\t/g, '\\t') + '"';
const item = (x) => ('s' in x ? q(x.s) : x.a);
export function serialize(root, depth = 0) {
  const ind = '\t'.repeat(depth);
  const head = [root.name]; let k = 0;
  while (k < root.args.length && !isNode(root.args[k])) head.push(item(root.args[k++]));
  if (k === root.args.length) return `(${head.join(' ')})`;
  let out = `(${head.join(' ')}`;
  for (; k < root.args.length; k++) { const a = root.args[k]; out += isNode(a) ? `\n${ind}\t${serialize(a, depth + 1)}` : ` ${item(a)}`; }
  return out + `\n${ind})`;
}

export const child = (nd, name) => nd.args.find((a) => isNode(a) && a.name === name);
export const children = (nd, name) => nd.args.filter((a) => isNode(a) && a.name === name);
export const val = (x) => (x === undefined ? undefined : 's' in x ? x.s : x.a);
const nv = (nd, i, d = 0) => { const a = nd && nd.args[i]; const v = a === undefined || isNode(a) ? NaN : parseFloat(val(a)); return Number.isNaN(v) ? d : v; };
const fmtN = (v) => { let s = (+v).toFixed(6); s = s.replace(/0+$/, '').replace(/\.$/, ''); return s === '-0' ? '0' : s; };
const xy = (nd) => (nd ? [nv(nd, 0), nv(nd, 1)] : [0, 0]);

export function parents(root) { const m = new WeakMap(); (function w(nd) { for (const a of nd.args) if (isNode(a)) { m.set(a, nd); w(a); } })(root); return m; }
export function remove(root, node) { const p = parents(root).get(node); if (!p) return false; p.args.splice(p.args.indexOf(node), 1); return true; }

// KiCad has Y pointing down; a positive angle turns counter-clockwise on screen. Bottom-side parts are mirrored in x.
export function fpToWorld(fp, lx, ly) {
  const t = (fp.rot * Math.PI) / 180, c = Math.cos(t), s = Math.sin(t);
  if (fp.layer && fp.layer.startsWith('B.')) lx = -lx;
  return [fp.x + lx * c + ly * s, fp.y + ly * c - lx * s];
}

function readGraphic(nd) {
  const layer = val(child(nd, 'layer') && child(nd, 'layer').args[0]) || '';
  const w = nv(child(nd, 'width') || child(nd, 'stroke') && child(child(nd, 'stroke'), 'width'), 0, 0.15) || 0.15;
  const base = { node: nd, layer, w };
  switch (nd.name) {
    case 'gr_line': case 'fp_line': return { ...base, k: 'line', a: xy(child(nd, 'start')), b: xy(child(nd, 'end')) };
    case 'gr_rect': case 'fp_rect': return { ...base, k: 'rect', a: xy(child(nd, 'start')), b: xy(child(nd, 'end')) };
    case 'gr_circle': case 'fp_circle': { const c = xy(child(nd, 'center')), e = xy(child(nd, 'end')); return { ...base, k: 'circle', c, r: Math.hypot(e[0] - c[0], e[1] - c[1]) }; }
    case 'gr_arc': case 'fp_arc': {
      const mid = child(nd, 'mid');
      if (mid) return { ...base, k: 'arc3', a: xy(child(nd, 'start')), m: xy(mid), b: xy(child(nd, 'end')) };
      const c = xy(child(nd, 'start')), e = xy(child(nd, 'end')), ang = nv(child(nd, 'angle'), 0);   // KiCad 5: start = centre, end = first point
      const a0 = Math.atan2(e[1] - c[1], e[0] - c[0]), a1 = a0 + (ang * Math.PI) / 180, r = Math.hypot(e[0] - c[0], e[1] - c[1]);
      return { ...base, k: 'arc3', a: e, m: [c[0] + r * Math.cos((a0 + a1) / 2), c[1] + r * Math.sin((a0 + a1) / 2)], b: [c[0] + r * Math.cos(a1), c[1] + r * Math.sin(a1)] };
    }
    case 'gr_poly': case 'fp_poly': { const pts = child(nd, 'pts'); return { ...base, k: 'poly', pts: pts ? children(pts, 'xy').map(xy) : [] }; }
    default: return null;
  }
}

export function pcbModel(root) {
  if (root.name !== 'kicad_pcb') throw new KiCadError('This is not a PCB file (expected kicad_pcb).');
  const M = { version: nv(child(root, 'version'), 0), segments: [], arcs: [], vias: [], footprints: [], graphics: [], zones: [], nets: new Map(), layers: new Set() };
  for (const nt of children(root, 'net')) M.nets.set(nv(nt, 0), val(nt.args[1]) || '');
  for (const nd of root.args) {
    if (!isNode(nd)) continue;
    const layer = val(child(nd, 'layer') && child(nd, 'layer').args[0]) || '';
    if (nd.name === 'segment') M.segments.push({ node: nd, a: xy(child(nd, 'start')), b: xy(child(nd, 'end')), w: nv(child(nd, 'width'), 0, 0.25), layer, net: nv(child(nd, 'net'), 0) });
    else if (nd.name === 'arc') M.arcs.push({ node: nd, a: xy(child(nd, 'start')), m: xy(child(nd, 'mid')), b: xy(child(nd, 'end')), w: nv(child(nd, 'width'), 0, 0.25), layer, net: nv(child(nd, 'net'), 0) });
    else if (nd.name === 'via') M.vias.push({ node: nd, p: xy(child(nd, 'at')), size: nv(child(nd, 'size'), 0, 0.8), drill: nv(child(nd, 'drill'), 0, 0.4), net: nv(child(nd, 'net'), 0) });
    else if (/^gr_(line|rect|circle|arc|poly)$/.test(nd.name)) { const g = readGraphic(nd); if (g) { M.graphics.push(g); M.layers.add(g.layer); } }
    else if (nd.name === 'zone') { const poly = child(nd, 'polygon'); const pts = poly && child(poly, 'pts'); M.zones.push({ node: nd, layer: val(child(nd, 'layer') && child(nd, 'layer').args[0]) || (child(nd, 'layers') && val(child(nd, 'layers').args[0])) || '', pts: pts ? children(pts, 'xy').map(xy) : [] }); }
    else if (nd.name === 'footprint' || nd.name === 'module') M.footprints.push(readFootprint(nd));
  }
  for (const s of M.segments) M.layers.add(s.layer);
  return M;
}

function readFootprint(nd) {
  const at = child(nd, 'at');
  const fp = { node: nd, name: val(nd.args[0]) || '', layer: val(child(nd, 'layer') && child(nd, 'layer').args[0]) || 'F.Cu', x: nv(at, 0), y: nv(at, 1), rot: nv(at, 2), ref: '', value: '', pads: [], graphics: [] };
  for (const p of children(nd, 'property')) { const k = val(p.args[0]); if (k === 'Reference') fp.ref = val(p.args[1]) || ''; if (k === 'Value') fp.value = val(p.args[1]) || ''; }
  for (const t of children(nd, 'fp_text')) { const k = val(t.args[0]); if (k === 'reference' && !fp.ref) fp.ref = val(t.args[1]) || ''; if (k === 'value' && !fp.value) fp.value = val(t.args[1]) || ''; }
  for (const pd of children(nd, 'pad')) {
    const pa = child(pd, 'at'), sz = child(pd, 'size'), dr = child(pd, 'drill');
    fp.pads.push({ node: pd, num: val(pd.args[0]) || '', type: val(pd.args[1]), shape: val(pd.args[2]) || 'rect', lx: nv(pa, 0), ly: nv(pa, 1), angle: nv(pa, 2, fp.rot), w: nv(sz, 0, 1), h: nv(sz, 1, 1),
      drill: dr ? (val(dr.args[0]) === 'oval' ? nv(dr, 1) : nv(dr, 0)) : 0, layers: (child(pd, 'layers') ? child(pd, 'layers').args.map(val) : []) });
  }
  for (const g of nd.args) if (isNode(g) && /^fp_(line|rect|circle|arc|poly)$/.test(g.name)) { const r = readGraphic(g); if (r) fp.graphics.push(r); }
  return fp;
}

// ---- structured edits ----------------------------------------------------------------------------------------
function setAtNode(nd, x, y, rot) {
  nd.args[0] = A(fmtN(x)); nd.args[1] = A(fmtN(y));
  if (rot !== undefined) { if (nd.args.length > 2 && !isNode(nd.args[2])) nd.args[2] = A(fmtN(rot)); else nd.args.splice(2, 0, A(fmtN(rot))); }
}
export function moveFootprint(fp, dx, dy) { const at = child(fp.node, 'at'); if (!at) throw new KiCadError('Footprint has no position.'); setAtNode(at, fp.x + dx, fp.y + dy, at.args.length > 2 ? fp.rot : undefined); fp.x += dx; fp.y += dy; }
export function rotateFootprint(fp, deg) {
  const at = child(fp.node, 'at'); if (!at) throw new KiCadError('Footprint has no position.');
  const nr = (((fp.rot + deg) % 360) + 360) % 360;
  setAtNode(at, fp.x, fp.y, nr);
  for (const pd of fp.pads) { const pa = child(pd.node, 'at'); if (pa) { const na = (((pd.angle + deg) % 360) + 360) % 360; setAtNode(pa, pd.lx, pd.ly, na); pd.angle = na; } }   // KiCad stores pad angles as absolute orientation
  fp.rot = nr;
}
export function moveSegment(s, dx, dy) {
  const st = child(s.node, 'start'), en = child(s.node, 'end');
  setAtNode(st, s.a[0] + dx, s.a[1] + dy); setAtNode(en, s.b[0] + dx, s.b[1] + dy);
  s.a = [s.a[0] + dx, s.a[1] + dy]; s.b = [s.b[0] + dx, s.b[1] + dy];
  const mid = child(s.node, 'mid'); if (mid && s.m) { setAtNode(mid, s.m[0] + dx, s.m[1] + dy); s.m = [s.m[0] + dx, s.m[1] + dy]; }
}
export function moveVia(v, dx, dy) { setAtNode(child(v.node, 'at'), v.p[0] + dx, v.p[1] + dy); v.p = [v.p[0] + dx, v.p[1] + dy]; }
export function setFootprintText(fp, which, text) {   // which: 'Reference' | 'Value'
  const prop = children(fp.node, 'property').find((p) => val(p.args[0]) === which);
  if (prop) { prop.args[1] = S(text); }
  else { const t = children(fp.node, 'fp_text').find((x) => val(x.args[0]) === which.toLowerCase()); if (!t) throw new KiCadError(`Footprint has no ${which} field.`); t.args[1] = S(text); }
  if (which === 'Reference') fp.ref = text; else fp.value = text;
}

// Generic editing for the tree view: replace a leaf's text; strings stay strings, atoms stay atoms.
export function setLeaf(parent, index, text) { const it = parent.args[index]; if (isNode(it)) throw new KiCadError('That is not a value.'); parent.args[index] = 's' in it ? S(text) : A(text.trim() || '0'); }
export function summarize(nd) { const parts = nd.args.filter((a) => !isNode(a)).slice(0, 3).map(item); return `${nd.name}${parts.length ? ' ' + parts.join(' ') : ''}`; }
