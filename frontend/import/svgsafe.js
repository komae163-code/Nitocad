// SVG sanitiser: a small XML parser plus an allow-list. Untrusted SVG can carry scripts, event handlers,
// foreignObject HTML, external references and entity bombs, so nothing is trusted unless listed here.
// No DOM needed, so it is also testable in Node.
const ALLOWED_EL = new Set(['svg', 'g', 'defs', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'text', 'tspan', 'title', 'desc',
  'lineargradient', 'radialgradient', 'stop', 'clippath', 'mask', 'pattern', 'symbol', 'use', 'marker', 'image']);
const CANON = {}; for (const n of ['linearGradient', 'radialGradient', 'clipPath']) CANON[n.toLowerCase()] = n;
const PRESENTATION = ['fill', 'fill-opacity', 'fill-rule', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit', 'stroke-dasharray',
  'stroke-dashoffset', 'stroke-opacity', 'opacity', 'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor', 'dominant-baseline', 'letter-spacing',
  'stop-color', 'stop-opacity', 'clip-path', 'mask', 'marker-start', 'marker-mid', 'marker-end', 'display', 'visibility', 'vector-effect'];
const GEOMETRY = ['id', 'class', 'x', 'y', 'width', 'height', 'cx', 'cy', 'r', 'rx', 'ry', 'x1', 'y1', 'x2', 'y2', 'd', 'points', 'transform', 'viewBox', 'preserveAspectRatio',
  'dx', 'dy', 'rotate', 'offset', 'gradientUnits', 'gradientTransform', 'patternUnits', 'patternTransform', 'patternContentUnits', 'clipPathUnits', 'maskUnits', 'markerWidth',
  'markerHeight', 'markerUnits', 'refX', 'refY', 'orient', 'fx', 'fy', 'spreadMethod', 'xmlns', 'xmlns:xlink', 'version', 'xml:space'];
const ATTR_OK = new Set([...PRESENTATION, ...GEOMETRY]);
const URL_ATTR = new Set(['fill', 'stroke', 'clip-path', 'mask', 'marker-start', 'marker-mid', 'marker-end']);

export class SvgError extends Error {}

const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
const decode = (s) => s.replace(/&(#x[0-9a-fA-F]+|#\d+|[A-Za-z][A-Za-z0-9]*);/g, (m, g) => {
  if (g[0] === '#') { const c = g[1] === 'x' ? parseInt(g.slice(2), 16) : parseInt(g.slice(1), 10); return c > 0 && c < 0x110000 ? String.fromCodePoint(c) : ''; }
  return ENT[g] !== undefined ? ENT[g] : '';       // custom entities are dropped, never expanded
});
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export function parseXML(src) {
  if (/<!DOCTYPE|<!ENTITY/i.test(src)) throw new SvgError('This SVG contains a DOCTYPE or entity definition, which is not allowed (it can be used to attack viewers).');
  let i = 0; const n = src.length; const stack = []; let root = null;
  while (i < n) {
    if (src.startsWith('<!--', i)) { const e = src.indexOf('-->', i + 4); if (e < 0) throw new SvgError('Unterminated comment.'); i = e + 3; continue; }
    if (src.startsWith('<![CDATA[', i)) { const e = src.indexOf(']]>', i); if (e < 0) throw new SvgError('Unterminated CDATA.'); if (stack.length) stack[stack.length - 1].children.push({ text: src.slice(i + 9, e) }); i = e + 3; continue; }
    if (src.startsWith('<?', i)) { const e = src.indexOf('?>', i); if (e < 0) throw new SvgError('Unterminated declaration.'); i = e + 2; continue; }
    if (src[i] === '<') {
      if (src[i + 1] === '/') {
        const e = src.indexOf('>', i); if (e < 0) throw new SvgError('Unterminated closing tag.');
        const name = src.slice(i + 2, e).trim(); const top = stack.pop();
        if (!top || top.name !== name) throw new SvgError(`Mismatched closing tag </${name}>.`);
        i = e + 1; continue;
      }
      const m = /^<([A-Za-z_][\w:.-]*)/.exec(src.slice(i, i + 200)); if (!m) throw new SvgError('Malformed tag.');
      let j = i + m[0].length; const attrs = {};
      for (;;) {
        while (/\s/.test(src[j] || '')) j++;
        if (src[j] === '/' && src[j + 1] === '>') { j += 2; var selfClose = true; break; }
        if (src[j] === '>') { j++; selfClose = false; break; }
        const am = /^([A-Za-z_][\w:.-]*)\s*=\s*("([^"]*)"|'([^']*)')/.exec(src.slice(j, j + 20000));
        if (!am) throw new SvgError('Malformed attribute.');
        attrs[am[1]] = decode(am[3] !== undefined ? am[3] : am[4]); j += am[0].length;
      }
      const node = { name: m[1], attrs, children: [] };
      if (stack.length) stack[stack.length - 1].children.push(node); else if (!root) root = node; else throw new SvgError('More than one root element.');
      if (!selfClose) { stack.push(node); if (stack.length > 200) throw new SvgError('SVG is nested too deeply.'); }
      i = j; continue;
    }
    const e = src.indexOf('<', i); const t = src.slice(i, e < 0 ? n : e);
    if (stack.length && t.trim() !== '') stack[stack.length - 1].children.push({ text: decode(t) });
    i = e < 0 ? n : e;
  }
  if (stack.length) throw new SvgError('SVG is not well formed: a tag is never closed.');
  if (!root) throw new SvgError('No SVG content found.');
  return root;
}

const safeUrl = (v) => !/url\(/i.test(v) || /^\s*url\(\s*#[\w:.-]+\s*\)\s*$/.test(v);
const dangerous = (v) => /javascript:|vbscript:|data:text|expression\s*\(|@import|<script/i.test(v);

function cleanStyle(style, removed) {
  const keep = [];
  for (const decl of style.split(';')) {
    const k = decl.indexOf(':'); if (k < 0) continue;
    const prop = decl.slice(0, k).trim().toLowerCase(), val = decl.slice(k + 1).trim();
    if (PRESENTATION.includes(prop) && !dangerous(val) && safeUrl(val)) keep.push(`${prop}:${val}`); else if (prop) removed.push(`style ${prop}`);
  }
  return keep.join(';');
}

function cleanNode(node, removed, depth = 0) {
  const lname = node.name.replace(/^svg:/, '').toLowerCase();
  if (!ALLOWED_EL.has(lname)) { removed.push(`<${node.name}>`); return null; }
  const name = CANON[lname] || lname;
  const attrs = {};
  for (const [k0, v] of Object.entries(node.attrs)) {
    const k = k0.replace(/^svg:/, '');
    if (/^on/i.test(k)) { removed.push(`${k0} handler`); continue; }
    if (k === 'style') { const s = cleanStyle(v, removed); if (s) attrs.style = s; continue; }
    if (k === 'href' || k === 'xlink:href') {
      if (name === 'use' && /^#[\w:.-]+$/.test(v)) attrs.href = v;
      else if ((name === 'lineargradient' || name === 'radialgradient' || name === 'pattern') && /^#[\w:.-]+$/.test(v)) attrs.href = v;
      else if (name === 'image' && /^data:image\/(png|jpeg|jpg|webp|gif);base64,[A-Za-z0-9+/=\s]+$/.test(v)) attrs.href = v;
      else removed.push(`${k0} (external or unsafe link)`);
      continue;
    }
    if (!ATTR_OK.has(k)) { removed.push(`${k0} attribute`); continue; }
    if (dangerous(v) || (URL_ATTR.has(k) && !safeUrl(v))) { removed.push(`${k0} (unsafe value)`); continue; }
    attrs[k] = v;
  }
  if (name === 'svg' && !attrs.xmlns) attrs.xmlns = 'http://www.w3.org/2000/svg';
  const children = [];
  for (const c of node.children) {
    if (c.text !== undefined) { if (name === 'text' || name === 'tspan' || name === 'title' || name === 'desc') children.push(c); continue; }
    const r = cleanNode(c, removed, depth + 1); if (r) children.push(r);
  }
  return { name, attrs, children };
}

export function serializeNode(n) {
  if (n.text !== undefined) return esc(n.text).replace(/"/g, '"');
  const a = Object.entries(n.attrs).map(([k, v]) => ` ${k}="${esc(String(v))}"`).join('');
  return n.children.length ? `<${n.name}${a}>${n.children.map(serializeNode).join('')}</${n.name}>` : `<${n.name}${a}/>`;
}

// -> { svg: string (safe), tree, removed: string[] }
export function sanitizeSVG(src) {
  const root = parseXML(src);
  if (root.name.replace(/^svg:/, '').toLowerCase() !== 'svg') throw new SvgError('This file does not have an <svg> root element.');
  const removed = [];
  const tree = cleanNode(root, removed);
  return { svg: serializeNode(tree), tree, removed: [...new Set(removed)] };
}

export { esc as escapeXML };
