// Triangle-mesh engine for STL/OBJ: parse, transform, measure, export. Positions are a flat Float32Array,
// 9 numbers per triangle. Pure functions, no DOM.
export function parseSTL(buf) {
  const b = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  if (b.length >= 84 && b.length === 84 + 50 * dv.getUint32(80, true)) {
    const n = dv.getUint32(80, true), out = new Float32Array(n * 9);
    for (let i = 0; i < n; i++) for (let k = 0; k < 9; k++) out[i * 9 + k] = dv.getFloat32(84 + i * 50 + 12 + k * 4, true);
    return out;
  }
  const text = new TextDecoder().decode(b), re = /vertex\s+(\S+)\s+(\S+)\s+(\S+)/gi, v = [];
  let m;
  while ((m = re.exec(text))) v.push(+m[1], +m[2], +m[3]);
  if (v.length === 0 || v.length % 9) throw new Error('Could not read this STL file.');
  return new Float32Array(v);
}

export function parseOBJ(text) {
  const verts = [], out = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line.startsWith('v ')) { const p = line.split(/\s+/); verts.push([+p[1], +p[2], +p[3]]); }
    else if (line.startsWith('f ')) {
      const idx = line.slice(2).trim().split(/\s+/).map((t) => { const i = parseInt(t.split('/')[0], 10); return i < 0 ? verts.length + i : i - 1; });
      for (let k = 1; k + 1 < idx.length; k++) for (const i of [idx[0], idx[k], idx[k + 1]]) {
        const p = verts[i]; if (!p) throw new Error('OBJ face refers to a missing vertex.'); out.push(p[0], p[1], p[2]);
      }
    }
  }
  if (!out.length) throw new Error('No faces found in this OBJ file.');
  return new Float32Array(out);
}

export const identity = () => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];   // row-major 4x4
export function mul(a, b) { const o = new Array(16).fill(0); for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) for (let k = 0; k < 4; k++) o[r * 4 + c] += a[r * 4 + k] * b[k * 4 + c]; return o; }
export const translation = (x, y, z) => { const m = identity(); m[3] = x; m[7] = y; m[11] = z; return m; };
export const scaling = (x, y = x, z = x) => { const m = identity(); m[0] = x; m[5] = y; m[10] = z; return m; };
export function rotation(axis, deg) {
  const t = (deg * Math.PI) / 180, c = Math.cos(t), s = Math.sin(t), m = identity();
  if (axis === 'x') { m[5] = c; m[6] = -s; m[9] = s; m[10] = c; }
  else if (axis === 'y') { m[0] = c; m[2] = s; m[8] = -s; m[10] = c; }
  else { m[0] = c; m[1] = -s; m[4] = s; m[5] = c; }
  return m;
}
export const mirror = (axis) => scaling(axis === 'x' ? -1 : 1, axis === 'y' ? -1 : 1, axis === 'z' ? -1 : 1);

export function bbox(p) {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < p.length; i += 3) for (let k = 0; k < 3; k++) { lo[k] = Math.min(lo[k], p[i + k]); hi[k] = Math.max(hi[k], p[i + k]); }
  return { min: lo, max: hi, size: hi.map((h, k) => h - lo[k]), center: hi.map((h, k) => (h + lo[k]) / 2) };
}

// Flips triangle winding when the transform mirrors space (negative determinant), so normals stay outward.
export function transform(p, m) {
  const det = m[0] * (m[5] * m[10] - m[6] * m[9]) - m[1] * (m[4] * m[10] - m[6] * m[8]) + m[2] * (m[4] * m[9] - m[5] * m[8]);
  const out = new Float32Array(p.length);
  for (let i = 0; i < p.length; i += 3) {
    const x = p[i], y = p[i + 1], z = p[i + 2];
    out[i] = m[0] * x + m[1] * y + m[2] * z + m[3];
    out[i + 1] = m[4] * x + m[5] * y + m[6] * z + m[7];
    out[i + 2] = m[8] * x + m[9] * y + m[10] * z + m[11];
  }
  return det < 0 ? flipWinding(out) : out;
}
export function flipWinding(p) {
  const o = new Float32Array(p);
  for (let t = 0; t < o.length; t += 9) for (let k = 0; k < 3; k++) { const a = o[t + 3 + k]; o[t + 3 + k] = o[t + 6 + k]; o[t + 6 + k] = a; }
  return o;
}
export const centerOnOrigin = (p) => { const c = bbox(p).center; return transform(p, translation(-c[0], -c[1], -c[2])); };
export const dropToZ0 = (p) => transform(p, translation(0, 0, -bbox(p).min[2]));

export function measure(p) {
  let area = 0, vol = 0;
  for (let i = 0; i < p.length; i += 9) {
    const ax = p[i], ay = p[i + 1], az = p[i + 2], bx = p[i + 3], by = p[i + 4], bz = p[i + 5], cx = p[i + 6], cy = p[i + 7], cz = p[i + 8];
    const ux = bx - ax, uy = by - ay, uz = bz - az, vx = cx - ax, vy = cy - ay, vz = cz - az;
    area += 0.5 * Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx);
    vol += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6;
  }
  return { triangles: p.length / 9, area, volume: vol };   // volume is signed: negative means inside-out
}

export function toSTL(p) {
  const n = p.length / 9, buf = new ArrayBuffer(84 + 50 * n), dv = new DataView(buf);
  new TextEncoder().encodeInto('NitoCAD binary STL', new Uint8Array(buf, 0, 80));
  dv.setUint32(80, n, true);
  for (let t = 0; t < n; t++) {
    const o = t * 9, ux = p[o + 3] - p[o], uy = p[o + 4] - p[o + 1], uz = p[o + 5] - p[o + 2], vx = p[o + 6] - p[o], vy = p[o + 7] - p[o + 1], vz = p[o + 8] - p[o + 2];
    let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx; const l = Math.hypot(nx, ny, nz) || 1;
    const base = 84 + t * 50;
    [nx / l, ny / l, nz / l].forEach((v, k) => dv.setFloat32(base + k * 4, v, true));
    for (let k = 0; k < 9; k++) dv.setFloat32(base + 12 + k * 4, p[o + k], true);
  }
  return buf;
}

export function toOBJ(p) {
  const map = new Map(), verts = [], faces = [];
  const key = (i) => `${p[i]},${p[i + 1]},${p[i + 2]}`;
  for (let t = 0; t < p.length; t += 9) {
    const f = [];
    for (let k = 0; k < 3; k++) { const kk = key(t + k * 3); if (!map.has(kk)) { map.set(kk, verts.length + 1); verts.push(`v ${p[t + k * 3]} ${p[t + k * 3 + 1]} ${p[t + k * 3 + 2]}`); } f.push(map.get(kk)); }
    if (f[0] !== f[1] && f[1] !== f[2] && f[0] !== f[2]) faces.push(`f ${f.join(' ')}`);
  }
  return `# NitoCAD export\n${verts.join('\n')}\n${faces.join('\n')}\n`;
}
