// Gerber (RS-274X) and Excellon drill: parse for drawing, and rewrite coordinates for move / mirror / rotate.
// Not supported (reported, never silently wrong): step-and-repeat (SR), load mirror/rotation (LM/LR), incremental
// coordinates, macro primitives 6/7 (moire/thermal), exposure-off macro primitives (drawn as exposure-on).
export class GerberError extends Error {}

export function isExcellonText(t) { return /^\s*M48/m.test(t) || (/^\s*(METRIC|INCH)\b/m.test(t) && /^\s*T\d+C[\d.]+/m.test(t)); }

// ---------- safe arithmetic for aperture macros (no eval) ----------
function evalExpr(src, vars) {
  const s = src.replace(/\s+/g, '').replace(/(?<=[\d.)\w$])x(?=[\d.($])/gi, '*'); let i = 0;
  const num = () => {
    if (s[i] === '(') { i++; const v = add(); if (s[i] !== ')') throw new GerberError('Bad macro expression.'); i++; return v; }
    if (s[i] === '-') { i++; return -num(); } if (s[i] === '+') { i++; return num(); }
    if (s[i] === '$') { const m = /^\$(\d+)/.exec(s.slice(i)); if (!m) throw new GerberError('Bad macro variable.'); i += m[0].length; const v = vars[+m[1]]; return v === undefined ? 0 : v; }
    const m = /^\d*\.?\d+/.exec(s.slice(i)); if (!m) throw new GerberError('Bad macro number.'); i += m[0].length; return parseFloat(m[0]);
  };
  const mul = () => { let v = num(); while (s[i] === '*' || s[i] === '/') { const o = s[i++]; const r = num(); v = o === '*' ? v * r : v / r; } return v; };
  const add = () => { let v = mul(); while (s[i] === '+' || s[i] === '-') { const o = s[i++]; const r = mul(); v = o === '+' ? v + r : v - r; } return v; };
  const v = add(); if (i !== s.length) throw new GerberError('Bad macro expression.'); return v;
}

function macroPrims(lines, params, warnings) {
  const vars = {}; params.forEach((p, k) => { vars[k + 1] = p; }); const out = [];
  for (const raw of lines) {
    const line = raw.trim(); if (!line || line.startsWith('0 ')) continue;
    const asg = /^\$(\d+)\s*=(.*)$/.exec(line); if (asg) { vars[+asg[1]] = evalExpr(asg[2], vars); continue; }
    const p = line.split(',').map((x, k) => (k === 0 ? parseInt(x, 10) : evalExpr(x, vars)));
    const [code, ex = 1] = p;
    if (code === 1) out.push({ k: 'circle', ex, d: p[2], x: p[3] || 0, y: p[4] || 0, rot: p[5] || 0 });
    else if (code === 20) out.push({ k: 'vline', ex, w: p[2], x1: p[3], y1: p[4], x2: p[5], y2: p[6], rot: p[7] || 0 });
    else if (code === 21) out.push({ k: 'cline', ex, w: p[2], h: p[3], x: p[4] || 0, y: p[5] || 0, rot: p[6] || 0 });
    else if (code === 4) { const n = p[2], pts = []; for (let k = 0; k <= n; k++) pts.push([p[3 + 2 * k], p[4 + 2 * k]]); out.push({ k: 'outline', ex, pts, rot: p[3 + 2 * (n + 1)] || 0 }); }
    else if (code === 5) out.push({ k: 'poly', ex, n: p[2], x: p[3] || 0, y: p[4] || 0, d: p[5], rot: p[6] || 0 });
    else warnings.add(`macro primitive ${code} is not drawn`);
    if (ex === 0 && [1, 20, 21, 4, 5].includes(code)) warnings.add('macro with exposure off is drawn as exposure on');
  }
  return out;
}

function coord(str, f, axis) {
  if (str.includes('.')) return parseFloat(str);
  const neg = str[0] === '-'; let d = str.replace(/^[+-]/, '');
  const total = (axis === 'x' ? f.xi + f.xd : f.yi + f.yd), dec = axis === 'x' ? f.xd : f.yd;
  if (f.zeros === 'T') d = d.padEnd(total, '0');
  return (neg ? -1 : 1) * parseInt(d || '0', 10) / 10 ** dec;
}

function blocks(text) {            // -> [{ext:boolean, cmds:[string]}]
  const out = []; let i = 0; const n = text.length;
  while (i < n) {
    const c = text[i];
    if (c === '%') { const e = text.indexOf('%', i + 1); if (e < 0) throw new GerberError('Unterminated % command.'); out.push({ ext: true, cmds: text.slice(i + 1, e).split('*').map((x) => x.replace(/[\r\n]/g, '')).filter((x) => x.length) }); i = e + 1; }
    else if (/\s/.test(c)) i++;
    else { const e = text.indexOf('*', i); if (e < 0) { if (text.slice(i).trim()) throw new GerberError('A block is missing its terminating "*".'); break; } out.push({ ext: false, cmds: [text.slice(i, e).replace(/[\r\n\s]/g, '')] }); i = e + 1; }
  }
  return out;
}

export function parseGerber(text) {
  const G = { kind: 'gerber', units: 'mm', fmt: null, apertures: new Map(), ops: [], warnings: new Set(), bbox: null };
  const macros = new Map();
  let x = 0, y = 0, ap = null, interp = 1, multi = true, dark = true, region = false, lastD = 2, contour = [], contours = [];
  const bb = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
  const ext = (px, py, r = 0) => { if (!Number.isFinite(px) || !Number.isFinite(py)) return; bb.x0 = Math.min(bb.x0, px - r); bb.y0 = Math.min(bb.y0, py - r); bb.x1 = Math.max(bb.x1, px + r); bb.y1 = Math.max(bb.y1, py + r); };
  const radius = (a) => (!a ? 0 : a.type === 'C' ? a.d / 2 : a.type === 'R' || a.type === 'O' ? Math.hypot(a.w, a.h) / 2 : a.type === 'P' ? a.d / 2 : Math.max(0, ...a.prims.map((p) => Math.hypot(p.x || 0, p.y || 0) + (p.d || p.w || 1) / 2)));
  const bl = blocks(text);
  for (const b of bl) {
    if (b.ext) {
      if (b.cmds[0] && b.cmds[0].startsWith('AM')) { macros.set(b.cmds[0].slice(2), b.cmds.slice(1)); continue; }
      for (const c of b.cmds) {
        let m;
        if ((m = /^FS([LT])([AI])X(\d)(\d)Y(\d)(\d)/.exec(c))) { G.fmt = { zeros: m[1], xi: +m[3], xd: +m[4], yi: +m[5], yd: +m[6] }; if (m[2] === 'I') G.warnings.add('incremental coordinates are not supported'); }
        else if ((m = /^MO(MM|IN)/.exec(c))) G.units = m[1] === 'MM' ? 'mm' : 'in';
        else if ((m = /^LP([DC])/.exec(c))) dark = m[1] === 'D';
        else if ((m = /^AD[D]?(\d+)([A-Za-z_.$][\w.$]*)(?:,(.*))?$/.exec(c))) {
          const code = +m[1], shape = m[2], prm = (m[3] || '').split('X').filter((s) => s !== '').map(Number);
          if (shape === 'C') G.apertures.set(code, { type: 'C', d: prm[0] || 0 });
          else if (shape === 'R' || shape === 'O') G.apertures.set(code, { type: shape, w: prm[0] || 0, h: prm[1] === undefined ? prm[0] : prm[1] });
          else if (shape === 'P') G.apertures.set(code, { type: 'P', d: prm[0] || 0, n: prm[1] || 3, rot: prm[2] || 0 });
          else if (macros.has(shape)) G.apertures.set(code, { type: 'M', name: shape, prims: macroPrims(macros.get(shape), prm, G.warnings) });
          else G.warnings.add(`aperture ${code} uses an unknown macro "${shape}"`);
        } else if (/^SR/.test(c) && /^SR[XY]/.test(c)) G.warnings.add('step-and-repeat (SR) is not supported');
        else if (/^L[MR]/.test(c) && !/^LN/.test(c)) G.warnings.add('load mirror/rotation (LM/LR) is not supported');
      }
      continue;
    }
    const c = b.cmds[0];
    if (!c || c.startsWith('G04')) continue;
    if (/^M0?2/.test(c)) break;
    const toks = [...c.matchAll(/([GDMXYIJ])([+-]?[\d.]+)/g)].map((t) => [t[1], t[2]]);
    if (!G.fmt && toks.some((t) => 'XYIJ'.includes(t[0]))) throw new GerberError('Coordinates found before the %FS format statement.');
    let nx = x, ny = y, i = 0, j = 0, d = null, hasXY = false;
    for (const [k, v] of toks) {
      if (k === 'G') { const g = +v; if (g === 1 || g === 2 || g === 3) interp = g; else if (g === 74) multi = false; else if (g === 75) multi = true;
        else if (g === 36) { region = true; contour = []; contours = []; } else if (g === 37) { if (contour.length) contours.push(contour); if (contours.length) G.ops.push({ t: 'region', contours, dark }); region = false; contour = []; contours = []; }
        else if (g === 91) G.warnings.add('incremental coordinates are not supported'); }
      else if (k === 'X') { nx = coord(v, G.fmt, 'x'); hasXY = true; } else if (k === 'Y') { ny = coord(v, G.fmt, 'y'); hasXY = true; }
      else if (k === 'I') i = coord(v, G.fmt, 'x'); else if (k === 'J') j = coord(v, G.fmt, 'y');
      else if (k === 'D') { const dn = +v; if (dn >= 10) ap = G.apertures.get(dn) || null; else d = dn; }
    }
    if (d === null && hasXY) d = lastD;
    if (d === null) continue;
    lastD = d;
    if (d === 2) { if (region && contour.length) { contours.push(contour); contour = []; } x = nx; y = ny; continue; }
    if (d === 3) { if (!region) { if (ap) { G.ops.push({ t: 'flash', ap, x: nx, y: ny, dark }); ext(nx, ny, radius(ap)); } } x = nx; y = ny; continue; }
    // d === 1: draw
    const seg = interp === 1 ? { x1: x, y1: y, x2: nx, y2: ny } : arcSeg(x, y, nx, ny, i, j, interp === 3, multi);
    if (region) { contour.push(seg); ext(x, y); ext(nx, ny); }
    else if (ap) {
      const r = radius(ap); if (seg.arc) { G.ops.push({ t: 'arc', ap, ...seg, dark }); ext(seg.cx, seg.cy, seg.r + r); } else { G.ops.push({ t: 'draw', ap, ...seg, dark }); ext(x, y, r); ext(nx, ny, r); }
    }
    x = nx; y = ny;
  }
  for (const op of G.ops) if (op.t === 'region') for (const c of op.contours) for (const s of c) if (s.arc) ext(s.cx, s.cy, s.r);
  G.bbox = Number.isFinite(bb.x0) ? bb : null; G.warnings = [...G.warnings];
  return G;
}

function arcSeg(x1, y1, x2, y2, i, j, ccw, multi) {
  let cx = x1 + i, cy = y1 + j;
  if (!multi) {                      // G74 single quadrant: I/J are unsigned, pick the centre that gives a <=90 degree arc
    let best = null;
    for (const [sx, sy] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
      const px = x1 + sx * Math.abs(i), py = y1 + sy * Math.abs(j);
      const e = Math.abs(Math.hypot(x1 - px, y1 - py) - Math.hypot(x2 - px, y2 - py));
      let sweep = Math.atan2(y2 - py, x2 - px) - Math.atan2(y1 - py, x1 - px); if (ccw && sweep < 0) sweep += 2 * Math.PI; if (!ccw && sweep > 0) sweep -= 2 * Math.PI;
      const score = e + (Math.abs(sweep) > Math.PI / 2 + 1e-6 ? 1 : 0);
      if (!best || score < best.score) best = { px, py, score };
    }
    cx = best.px; cy = best.py;
  }
  const r = Math.hypot(x1 - cx, y1 - cy);
  let a1 = Math.atan2(y1 - cy, x1 - cx), a2 = Math.atan2(y2 - cy, x2 - cx);
  if (ccw && a2 <= a1) a2 += 2 * Math.PI; if (!ccw && a2 >= a1) a2 -= 2 * Math.PI;
  if (multi && Math.abs(x1 - x2) < 1e-9 && Math.abs(y1 - y2) < 1e-9) a2 = a1 + (ccw ? 2 : -2) * Math.PI;   // full circle
  return { arc: true, cx, cy, r, a1, a2, ccw, x1, y1, x2, y2 };
}

// ---------- Excellon ----------
export function parseExcellon(text) {
  const D = { kind: 'drill', units: 'mm', tools: new Map(), hits: [], slots: [], warnings: new Set(), bbox: null };
  let tool = null, header = true, intFmt = null;
  const bb = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
  const ext = (x, y, r) => { bb.x0 = Math.min(bb.x0, x - r); bb.y0 = Math.min(bb.y0, y - r); bb.x1 = Math.max(bb.x1, x + r); bb.y1 = Math.max(bb.y1, y + r); };
  const num = (s) => { if (s.includes('.')) return parseFloat(s); intFmt = true; const dec = D.units === 'mm' ? 3 : 4; const neg = s[0] === '-'; const dg = s.replace(/^[+-]/, '').padStart(dec + 1, '0'); return (neg ? -1 : 1) * parseInt(dg, 10) / 10 ** dec; };
  for (const raw of text.split(/\r?\n/)) {
    const l = raw.trim(); if (!l || l.startsWith(';')) continue;
    let m;
    if (/^M48/.test(l)) { header = true; continue; } if (/^%$|^M95/.test(l)) { header = false; continue; } if (/^M30|^M00/.test(l)) break;
    if ((m = /^(METRIC|INCH)/.exec(l))) { D.units = m[1] === 'METRIC' ? 'mm' : 'in'; continue; }
    if ((m = /^T(\d+)C([\d.]+)/.exec(l))) { D.tools.set(+m[1], parseFloat(m[2])); continue; }
    if ((m = /^T(\d+)$/.exec(l))) { tool = +m[1]; if (!D.tools.has(tool) && tool !== 0) D.warnings.add(`tool T${tool} has no diameter`); header = false; continue; }
    if (header) continue;
    if ((m = /^X([+-]?[\d.]+)Y([+-]?[\d.]+)G85X([+-]?[\d.]+)Y([+-]?[\d.]+)/.exec(l))) { const d = D.tools.get(tool) || 0; const s = { x1: num(m[1]), y1: num(m[2]), x2: num(m[3]), y2: num(m[4]), d }; D.slots.push(s); ext(s.x1, s.y1, d / 2); ext(s.x2, s.y2, d / 2); continue; }
    if ((m = /^X([+-]?[\d.]+)Y([+-]?[\d.]+)$/.exec(l)) || (m = /^X([+-]?[\d.]+)Y([+-]?[\d.]+)/.exec(l))) { const d = D.tools.get(tool) || 0; const h = { x: num(m[1]), y: num(m[2]), d }; D.hits.push(h); ext(h.x, h.y, d / 2); }
  }
  if (intFmt) D.warnings.add('coordinates have no decimal point: assuming 3 decimals (metric) / 4 decimals (inch)');
  D.bbox = Number.isFinite(bb.x0) ? bb : null; D.warnings = [...D.warnings];
  return D;
}

// ---------- rewriting ----------
const R90 = (x, y, k) => { for (let n = 0; n < k; n++) [x, y] = [-y, x]; return [x, y]; };
function tpoint(x, y, t) { if (t.mirrorX) x = -x; if (t.mirrorY) y = -y; [x, y] = R90(x, y, t.rot / 90); return [x + (t.dx || 0), y + (t.dy || 0)]; }
function tvec(x, y, t) { if (t.mirrorX) x = -x; if (t.mirrorY) y = -y; return R90(x, y, t.rot / 90); }
function checkT(t) {
  if (t.rot === undefined) t.rot = 0;
  if (![0, 90, 180, 270].includes(((t.rot % 360) + 360) % 360)) throw new GerberError('Rotation must be a multiple of 90 degrees.');
  t.rot = ((t.rot % 360) + 360) % 360; return t;
}

export function transformGerber(text, t0) {
  const t = checkT({ ...t0 }), flips = (t.mirrorX ? 1 : 0) + (t.mirrorY ? 1 : 0), pure = !flips && t.rot === 0;
  const info = parseGerber(text); const f = info.fmt; if (!f) throw new GerberError('No %FS format statement found.');
  if (info.warnings.some((w) => /incremental|step-and-repeat|load mirror/.test(w))) throw new GerberError('This file uses features that cannot be moved safely: ' + info.warnings.join('; '));
  if (/\.\d/.test(text.replace(/%[^%]*%/g, '')) && /[XY][+-]?\d*\.\d/.test(text.replace(/%[^%]*%/g, ''))) throw new GerberError('Coordinates with a decimal point are not supported for editing.');
  if (!pure) {
    if ([...info.apertures.values()].some((a) => a.type === 'M' || a.type === 'P')) throw new GerberError('This file has macro or polygon apertures, so it can only be moved, not mirrored or rotated.');
    if (/G74\*|G74[A-Z]/.test(text)) throw new GerberError('This file uses single-quadrant arcs (G74), so it can only be moved.');
  }
  const enc = (v, axis) => {
    const dec = axis === 'x' ? f.xd : f.yd, total = axis === 'x' ? f.xi + f.xd : f.yi + f.yd;
    const n = Math.round(Math.abs(v) * 10 ** dec); let s = String(n);
    if (f.zeros === 'T') { s = s.padStart(total, '0').replace(/0+$/, ''); if (!s) s = '0'; }
    return (v < 0 && n !== 0 ? '-' : '') + s;
  };
  let x = 0, y = 0, lastD = 2, out = '', pos = 0, drawn = false;
  const apRot = t.rot === 90 || t.rot === 270;
  const raw = text; let i = 0; const n = raw.length;
  while (i < n) {
    const c = raw[i];
    if (c === '%') {
      const e = raw.indexOf('%', i + 1); let body = raw.slice(i, e + 1);
      if (apRot) body = body.replace(/(AD[D]?\d+)([RO]),([\d.]+)X([\d.]+)/, (m, a, s, w, h) => `${a}${s},${h}X${w}`);
      out += body; i = e + 1; continue;
    }
    if (/\s/.test(c)) { out += c; i++; continue; }
    const e = raw.indexOf('*', i); const blk = raw.slice(i, e).replace(/[\r\n\s]/g, ''); i = e + 1;
    if (/^G04/.test(blk) || /^M0?2/.test(blk)) { out += blk + '*'; continue; }
    const toks = [...blk.matchAll(/([GDMXYIJ])([+-]?[\d.]+)/g)];
    if (!toks.length || !toks.some((tk) => 'XYIJD'.includes(tk[1]) && (tk[1] !== 'D' || +tk[2] < 10 || true))) { out += blk + '*'; continue; }
    let nx = x, ny = y, iv = null, jv = null, d = null, gs = [], hasXY = false, ds = [];
    for (const [, k, v] of toks) {
      if (k === 'G') gs.push(+v); else if (k === 'X') { nx = coord(v, f, 'x'); hasXY = true; } else if (k === 'Y') { ny = coord(v, f, 'y'); hasXY = true; }
      else if (k === 'I') iv = coord(v, f, 'x'); else if (k === 'J') jv = coord(v, f, 'y'); else if (k === 'D') { if (+v >= 10) ds.push(+v); else d = +v; }
    }
    let piece = '';
    gs = gs.map((g) => (flips % 2 === 1 && (g === 2 || g === 3) ? 5 - g : g));
    for (const g of gs) piece += 'G' + String(g).padStart(2, '0');
    if (d === null && hasXY) d = lastD;
    if (hasXY || iv !== null || jv !== null || d !== null) {
      if (d !== null || hasXY) { const [px, py] = tpoint(nx, ny, t); if (hasXY || d === 1 || d === 3 || d === 2) piece += 'X' + enc(px, 'x') + 'Y' + enc(py, 'y'); }
      if (iv !== null || jv !== null) { const [vx, vy] = tvec(iv || 0, jv || 0, t); piece += 'I' + enc(vx, 'x') + 'J' + enc(vy, 'y'); }
      for (const a of ds) piece += 'D' + a;
      if (d !== null) { piece += 'D' + String(d).padStart(2, '0'); lastD = d; }
      x = nx; y = ny;
    } else for (const a of ds) piece += 'D' + a;
    out += piece + '*';
  }
  return out;
}

export function transformExcellon(text, t0) {
  const t = checkT({ ...t0 }); const flips = (t.mirrorX ? 1 : 0) + (t.mirrorY ? 1 : 0);
  if (flips % 2 === 1 && /^\s*G0[23]/m.test(text)) throw new GerberError('This drill file contains arc routing, which cannot be mirrored safely.');
  let body = false; const lines = text.split(/(\r?\n)/);
  return lines.map((ln) => {
    if (/^\s*(%|M95)/.test(ln)) { body = true; return ln; }
    if (/^\s*M48/.test(ln)) { body = false; return ln; }
    if (!body && !/^\s*T\d+\s*$/.test(ln) && !/^\s*[XYG]/.test(ln)) return ln;
    if (!/[XY][+-]?[\d.]+/.test(ln) || /^\s*;/.test(ln)) return ln;
    // transform every X..Y.. pair on the line (slots carry two pairs)
    return ln.replace(/X([+-]?\d*\.?\d+)Y([+-]?\d*\.?\d+)/g, (m, xs, ys) => {
      if (!xs.includes('.') || !ys.includes('.')) throw new GerberError('This drill file uses integer coordinates, which cannot be edited safely. Export it with decimal coordinates.');
      const [px, py] = tpoint(parseFloat(xs), parseFloat(ys), t); const fx = (v) => { let s = v.toFixed(6).replace(/0+$/, ''); if (s.endsWith('.')) s += '0'; return s === '-0.0' ? '0.0' : s; };   // always keep the decimal point: "15" would be read as 0.015
      return 'X' + fx(px) + 'Y' + fx(py);
    });
  }).join('');
}

export function layerStyle(name) {
  const n = name.toLowerCase();
  if (/\.(gtl)$|f[._-]?cu|top.*copper/.test(n)) return { color: '#d9534f', label: 'Top copper' };
  if (/\.(gbl)$|b[._-]?cu|bottom.*copper/.test(n)) return { color: '#4a90d9', label: 'Bottom copper' };
  if (/\.(gts)$|f[._-]?mask/.test(n)) return { color: '#2e9e5b', label: 'Top mask' };
  if (/\.(gbs)$|b[._-]?mask/.test(n)) return { color: '#1f7a45', label: 'Bottom mask' };
  if (/\.(gto)$|f[._-]?silk/.test(n)) return { color: '#f5f5f5', label: 'Top silk' };
  if (/\.(gbo)$|b[._-]?silk/.test(n)) return { color: '#c9c9c9', label: 'Bottom silk' };
  if (/\.(gtp)$|f[._-]?paste/.test(n)) return { color: '#b0b0b0', label: 'Top paste' };
  if (/\.(gko|gm1|gml)$|edge|outline|profile/.test(n)) return { color: '#ffd60a', label: 'Outline' };
  if (/\.(drl|xln|exc)$|drill/.test(n)) return { color: '#bf5af2', label: 'Drill' };
  return { color: '#8e8e93', label: 'Layer' };
}
