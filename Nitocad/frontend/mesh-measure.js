/* Face picking and measuring on a triangle mesh (the STL the viewer shows).
   No three.js and no DOM in here, so it can be tested on its own.

   The mesh has no real faces, only triangles. This groups them back into faces:
     plane      triangles that lie in one plane
     cylinder   strips of flat facets around one axis: holes, bores, bosses, rounds, fillets
     curved     anything else that bends (spheres, cones), kept as one region
   Sizes come from fitting the mesh vertices, so they are exact to the mesh tolerance, not to the CAD model.
   Units are whatever the mesh is in (millimetres for NitoCAD). */

const PLANE_COS = Math.cos((0.8 * Math.PI) / 180);       // triangles closer than this in direction are one plane
const MIN_SIN = Math.sin((1.0 * Math.PI) / 180);         // adjacent facets need at least this bend to count as curved
const MAX_BEND = (40 * Math.PI) / 180;                   // and at most this much per facet
const AXIS_COS = 0.9995;                                 // axes of neighbouring facets must agree to ~1.8 degrees
const SMOOTH_COS = Math.cos((25 * Math.PI) / 180);       // gentle bends joined into one curved region

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a) => Math.hypot(a[0], a[1], a[2]);
const unit = (a) => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
const scale = (a, k) => [a[0] * k, a[1] * k, a[2] * k];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

/* Smallest-eigenvalue eigenvector of a symmetric 3x3 matrix (Jacobi rotations). For a set of surface normals that all
   lie around one axis, that vector is the axis. */
function weakestDirection(M) {
  const A = M.map((r) => r.slice());
  const V = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  for (let sweep = 0; sweep < 30; sweep++) {
    let off = Math.abs(A[0][1]) + Math.abs(A[0][2]) + Math.abs(A[1][2]);
    if (off < 1e-14) break;
    for (const [p, q] of [[0, 1], [0, 2], [1, 2]]) {
      if (Math.abs(A[p][q]) < 1e-18) continue;
      const theta = (A[q][q] - A[p][p]) / (2 * A[p][q]);
      const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1), s = t * c;
      for (let k = 0; k < 3; k++) { const akp = A[k][p], akq = A[k][q]; A[k][p] = c * akp - s * akq; A[k][q] = s * akp + c * akq; }
      for (let k = 0; k < 3; k++) { const apk = A[p][k], aqk = A[q][k]; A[p][k] = c * apk - s * aqk; A[q][k] = s * apk + c * aqk; }
      for (let k = 0; k < 3; k++) { const vkp = V[k][p], vkq = V[k][q]; V[k][p] = c * vkp - s * vkq; V[k][q] = s * vkp + c * vkq; }
    }
  }
  let best = 0;
  for (let i = 1; i < 3; i++) if (A[i][i] < A[best][best]) best = i;
  return unit([V[0][best], V[1][best], V[2][best]]);
}

/* Least-squares circle through 2D points (Kasa fit). Returns { cx, cy, r } or null. */
function fitCircle(pts) {
  let sx = 0, sy = 0, sxx = 0, syy = 0, sxy = 0, sz = 0, sxz = 0, syz = 0;
  const n = pts.length;
  for (const [x, y] of pts) { const z = x * x + y * y; sx += x; sy += y; sxx += x * x; syy += y * y; sxy += x * y; sz += z; sxz += x * z; syz += y * z; }
  // solve [sxx sxy sx; sxy syy sy; sx sy n] [a b c] = [sxz syz sz]
  const m = [[sxx, sxy, sx, sxz], [sxy, syy, sy, syz], [sx, sy, n, sz]];
  for (let i = 0; i < 3; i++) {
    let piv = i;
    for (let r = i + 1; r < 3; r++) if (Math.abs(m[r][i]) > Math.abs(m[piv][i])) piv = r;
    if (Math.abs(m[piv][i]) < 1e-12) return null;
    [m[i], m[piv]] = [m[piv], m[i]];
    for (let r = 0; r < 3; r++) {
      if (r === i) continue;
      const f = m[r][i] / m[i][i];
      for (let c = i; c < 4; c++) m[r][c] -= f * m[i][c];
    }
  }
  const a = m[0][3] / m[0][0], b = m[1][3] / m[1][1], c = m[2][3] / m[2][2];
  const cx = a / 2, cy = b / 2, r2 = c + cx * cx + cy * cy;
  return r2 > 0 ? { cx, cy, r: Math.sqrt(r2) } : null;
}

/** positions: flat xyz array (a triangle soup or an indexed mesh); index: optional triangle index array. */
export function analyzeMesh(positions, index) {
  const vertCount = positions.length / 3;
  const triCount = index ? index.length / 3 : Math.floor(vertCount / 3);
  const at = (i) => [positions[3 * i], positions[3 * i + 1], positions[3 * i + 2]];

  // weld vertices that sit at the same place, so triangles know their neighbours
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < vertCount; i++) for (let k = 0; k < 3; k++) { const v = positions[3 * i + k]; if (v < lo[k]) lo[k] = v; if (v > hi[k]) hi[k] = v; }
  const diag = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) || 1;
  const eps = Math.max(1e-6, diag * 1e-6);
  const weld = new Map(), pts = [];
  const welded = (i) => {
    const p = at(i), key = Math.round(p[0] / eps) + "," + Math.round(p[1] / eps) + "," + Math.round(p[2] / eps);
    let id = weld.get(key);
    if (id === undefined) { id = pts.length; weld.set(key, id); pts.push(p); }
    return id;
  };

  const tv = new Int32Array(triCount * 3);
  const nrm = [], area = new Float64Array(triCount), cen = [];
  for (let t = 0; t < triCount; t++) {
    const ia = index ? index[3 * t] : 3 * t, ib = index ? index[3 * t + 1] : 3 * t + 1, ic = index ? index[3 * t + 2] : 3 * t + 2;
    const a = welded(ia), b = welded(ib), c = welded(ic);
    tv[3 * t] = a; tv[3 * t + 1] = b; tv[3 * t + 2] = c;
    const n = cross(sub(pts[b], pts[a]), sub(pts[c], pts[a]));
    const l = len(n);
    area[t] = l / 2;
    nrm.push(l > 0 ? scale(n, 1 / l) : [0, 0, 0]);
    cen.push(scale(add(add(pts[a], pts[b]), pts[c]), 1 / 3));
  }

  // triangles that share an edge
  const edgeMap = new Map();
  const nbrs = Array.from({ length: triCount }, () => []);
  for (let t = 0; t < triCount; t++) {
    if (area[t] < 1e-12) continue;
    for (let e = 0; e < 3; e++) {
      const a = tv[3 * t + e], b = tv[3 * t + (e + 1) % 3], key = a < b ? a + "_" + b : b + "_" + a;
      const list = edgeMap.get(key);
      if (list) { for (const o of list) { nbrs[t].push(o); nbrs[o].push(t); } list.push(t); } else edgeMap.set(key, [t]);
    }
  }

  // 1. flat patches
  const patchOf = new Int32Array(triCount).fill(-1);
  const patches = [];
  for (let s = 0; s < triCount; s++) {
    if (patchOf[s] !== -1 || area[s] < 1e-12) continue;
    const id = patches.length, tris = [s]; patchOf[s] = id;
    for (let q = 0; q < tris.length; q++) for (const o of nbrs[tris[q]]) {
      if (patchOf[o] === -1 && dot(nrm[o], nrm[s]) >= PLANE_COS && dot(nrm[o], nrm[tris[q]]) >= PLANE_COS) { patchOf[o] = id; tris.push(o); }
    }
    patches.push({ tris });
  }
  for (const p of patches) {
    let a = 0, n = [0, 0, 0], c = [0, 0, 0];
    const verts = new Set();
    for (const t of p.tris) { a += area[t]; n = add(n, scale(nrm[t], area[t])); c = add(c, scale(cen[t], area[t])); for (let k = 0; k < 3; k++) verts.add(tv[3 * t + k]); }
    p.area = a; p.normal = unit(n); p.centroid = scale(c, 1 / (a || 1)); p.verts = [...verts];
  }
  const widthAlong = (p, dir) => { let mn = Infinity, mx = -Infinity; for (const v of p.verts) { const d = dot(pts[v], dir); if (d < mn) mn = d; if (d > mx) mx = d; } return mx - mn; };

  // 2. neighbouring patches that bend about one axis with similar widths are strips of one cylinder
  const pairSeen = new Set(), cand = [];
  for (let t = 0; t < triCount; t++) for (const o of nbrs[t]) {
    const a = patchOf[t], b = patchOf[o];
    if (a === b || a < 0 || b < 0) continue;
    const key = a < b ? a + "_" + b : b + "_" + a;
    if (pairSeen.has(key)) continue; pairSeen.add(key);
    const A = patches[a], B = patches[b];
    const cs = dot(A.normal, B.normal), sin = len(cross(A.normal, B.normal));
    if (sin < MIN_SIN || Math.acos(Math.min(1, Math.max(-1, cs))) > MAX_BEND) continue;
    const axis = unit(cross(A.normal, B.normal));
    const wa = widthAlong(A, unit(cross(axis, A.normal))), wb = widthAlong(B, unit(cross(axis, B.normal)));
    const ratio = wa / (wb || 1e-9);
    if (ratio < 0.6 || ratio > 1 / 0.6) continue;
    cand.push({ a, b, axis });
  }
  const parent = patches.map((_, i) => i), gAxis = new Array(patches.length).fill(null);
  const find = (x) => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  for (const { a, b, axis } of cand) {
    const ra = find(a), rb = find(b);
    const ax = gAxis[ra] || gAxis[rb] || axis;
    if (Math.abs(dot(ax, axis)) < AXIS_COS) continue;
    if (ra !== rb) { parent[rb] = ra; }
    gAxis[ra] = ax;
  }

  // 3. collect faces
  const groups = new Map();
  patches.forEach((p, i) => { const r = find(i); if (!groups.has(r)) groups.set(r, []); groups.get(r).push(i); });
  const faces = [];
  const faceOfTri = new Int32Array(triCount).fill(-1);
  const smallLimit = patches.reduce((sum, p) => sum + p.area, 0) * 0.02;

  for (const members of groups.values()) {
    if (members.length === 1) {
      const p = patches[members[0]];
      faces.push({ kind: "plane", tris: p.tris, area: p.area, normal: p.normal, centroid: p.centroid, _patches: [members[0]] });
      continue;
    }
    faces.push(describeStrip(members));
  }

  function describeStrip(members) {
    const tris = [], vset = new Set(); let areaSum = 0, cSum = [0, 0, 0], M = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
    for (const m of members) {
      const p = patches[m]; areaSum += p.area; cSum = add(cSum, scale(p.centroid, p.area));
      for (const t of p.tris) tris.push(t);
      for (const v of p.verts) vset.add(v);
      for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) M[i][j] += p.normal[i] * p.normal[j] * p.area;
    }
    const face = { kind: "curved", tris, area: areaSum, centroid: scale(cSum, 1 / (areaSum || 1)), _patches: members };
    const axis = weakestDirection(M);
    const flat = members.every((m) => Math.abs(dot(patches[m].normal, axis)) < 0.03);
    if (!flat || members.length < 3) return face;      // two facets always fit a circle, so three is the least that proves a cylinder
    // circle in the plane across the axis
    const u = unit(Math.abs(axis[0]) < 0.9 ? cross(axis, [1, 0, 0]) : cross(axis, [0, 1, 0])), w = cross(axis, u);
    const verts = [...vset], flat2 = verts.map((v) => [dot(pts[v], u), dot(pts[v], w)]);
    const fit = fitCircle(flat2);
    if (!fit) return face;
    let worst = 0;
    for (const [x, y] of flat2) worst = Math.max(worst, Math.abs(Math.hypot(x - fit.cx, y - fit.cy) - fit.r));
    if (worst > Math.max(0.02 * fit.r, diag * 2e-5)) return face;
    const along = verts.map((v) => dot(pts[v], axis));
    const lo2 = Math.min(...along), hi2 = Math.max(...along);
    const centre = add(add(scale(u, fit.cx), scale(w, fit.cy)), scale(axis, (lo2 + hi2) / 2));
    // which way the surface faces decides hole or boss: an inward-facing wall is a hole
    let facing = 0;
    for (const m of members) {
      const p = patches[m], radial = sub(p.centroid, add(centre, scale(axis, dot(sub(p.centroid, centre), axis))));
      facing += dot(p.normal, unit(radial)) * p.area;
    }
    // arc covered: the circle minus its largest gap
    const ang = flat2.map(([x, y]) => Math.atan2(y - fit.cy, x - fit.cx)).sort((a, b) => a - b);
    let gap = ang[0] + 2 * Math.PI - ang[ang.length - 1];
    for (let i = 1; i < ang.length; i++) gap = Math.max(gap, ang[i] - ang[i - 1]);
    const span = ((2 * Math.PI - gap) * 180) / Math.PI;
    return Object.assign(face, { kind: "cylinder", axis, centre, radius: fit.r, length: hi2 - lo2, concave: facing < 0, spanDeg: span, full: span >= 330 });
  }

  // curved pieces that are not cylinders (spheres, cones) and small flat bits that bend gently into each other are one curved region
  const triFace = new Int32Array(triCount).fill(-1);
  faces.forEach((f, i) => { for (const t of f.tris) triFace[t] = i; });
  const loose = (f) => f.kind === "curved" || (f.kind === "plane" && f.area < smallLimit);
  const par = faces.map((_, i) => i), fd = (x) => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  for (let t = 0; t < triCount; t++) for (const o of nbrs[t]) {
    const a = triFace[t], b = triFace[o];
    if (a < 0 || b < 0 || a === b || !loose(faces[a]) || !loose(faces[b])) continue;
    if (dot(nrm[t], nrm[o]) >= SMOOTH_COS) par[fd(a)] = fd(b);
  }
  const regions = new Map();
  faces.forEach((f, i) => { if (!loose(f)) return; const r = fd(i); if (!regions.has(r)) regions.set(r, []); regions.get(r).push(f); });
  for (const list of regions.values()) {
    const bends = list.some((f) => f.kind === "curved");
    if (list.length < (bends ? 2 : 3)) continue;
    const tris = list.flatMap((f) => f.tris), a = list.reduce((s2, f) => s2 + f.area, 0);
    const c = list.reduce((s2, f) => add(s2, scale(f.centroid, f.area)), [0, 0, 0]);
    faces.push({ kind: "curved", tris, area: a, centroid: scale(c, 1 / a), _patches: list.flatMap((f) => f._patches) });
    for (const f of list) f.drop = true;
  }
  const finalFaces = faces.filter((f) => !f.drop);
  finalFaces.forEach((f, i) => { f.id = i; for (const t of f.tris) faceOfTri[t] = i; });

  return {
    faces: finalFaces, faceOfTri,
    faceAt: (tri) => (tri >= 0 && tri < triCount && faceOfTri[tri] >= 0 ? finalFaces[faceOfTri[tri]] : null),
    /** the corners of a face's triangles as a flat xyz array, for drawing a highlight */
    trianglePositions: (face) => {
      const out = new Float32Array(face.tris.length * 9);
      face.tris.forEach((t, i) => { for (let k = 0; k < 3; k++) { const p = pts[tv[3 * t + k]]; out[9 * i + 3 * k] = p[0]; out[9 * i + 3 * k + 1] = p[1]; out[9 * i + 3 * k + 2] = p[2]; } });
      return out;
    },
    bounds: { lo, hi },
  };
}

export function fmtLen(v) {
  const a = Math.abs(v);
  if (a >= 10000) return (v / 1000).toFixed(2).replace(/\.?0+$/, "") + " m";
  const s = a >= 100 ? v.toFixed(1) : v.toFixed(2);
  return s.replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") + " mm";
}
const fmtArea = (v) => (v >= 1e6 ? (v / 1e6).toFixed(2) + " m²" : Math.round(v).toLocaleString("en-US") + " mm²");
const fmtAng = (v) => v.toFixed(1).replace(/\.0$/, "") + "°";

/** Plain description of one picked face. */
export function describeFace(f) {
  if (!f) return null;
  if (f.kind === "cylinder") {
    const d = f.radius * 2;
    if (f.full) {
      const title = f.concave ? "Hole" : "Round boss";
      return { title, lines: [`Diameter ${fmtLen(d)}`, `${f.concave ? "Depth" : "Height"} ${fmtLen(f.length)}`], diameter: d, isHole: f.concave };
    }
    return { title: f.concave ? "Rounded inside corner" : "Rounded edge", lines: [`Radius ${fmtLen(f.radius)}`, `Covers ${fmtAng(f.spanDeg)} of a circle`, `Along ${fmtLen(f.length)}`] };
  }
  if (f.kind === "plane") return { title: "Flat face", lines: [`Area ${fmtArea(f.area)}`] };
  return { title: "Curved surface", lines: [`Area ${fmtArea(f.area)}`] };
}

const perp = (v, axis) => sub(v, scale(axis, dot(v, axis)));

/** What lies between two picked faces. */
export function describePair(a, b) {
  if (!a || !b) return null;
  if (a.id === b.id) return { title: "Same face", lines: ["Tap a different face to measure to it."] };
  if (a.kind === "plane" && b.kind === "plane") {
    const c = Math.abs(dot(a.normal, b.normal));
    if (c > 0.9998) return { title: "Flat faces, parallel", lines: [`Distance ${fmtLen(Math.abs(dot(a.normal, sub(b.centroid, a.centroid))))}`] };
    return { title: "Flat faces", lines: [`Angle between them ${fmtAng((Math.acos(Math.min(1, c)) * 180) / Math.PI)}`] };
  }
  if (a.kind === "cylinder" && b.kind === "cylinder" && Math.abs(dot(a.axis, b.axis)) > 0.9995) {
    const d = len(perp(sub(b.centre, a.centre), a.axis));
    const lines = [`Centres ${fmtLen(d)} apart`];
    if (a.full && b.full) {
      if (a.concave && b.concave) lines.push(`Material between the holes ${fmtLen(d - a.radius - b.radius)}`);
      else if (a.concave !== b.concave) {
        const [hole, boss] = a.concave ? [a, b] : [b, a];
        if (d + hole.radius <= boss.radius + 1e-6) lines.push(`Thinnest wall ${fmtLen(boss.radius - hole.radius - d)}`);   // the hole sits inside the boss
        else lines.push(`Gap between them ${fmtLen(d - hole.radius - boss.radius)}`);
      }
      else lines.push(`Gap between ${fmtLen(d - a.radius - b.radius)}`);
    }
    const t = (x) => (x.full ? (x.concave ? "hole" : "boss") : "round");
    return { title: `${t(a)[0].toUpperCase() + t(a).slice(1)} to ${t(b)}`, lines };
  }
  const [cyl, other] = a.kind === "cylinder" ? [a, b] : [b, a];
  if (cyl.kind === "cylinder" && other.kind === "plane" && Math.abs(dot(cyl.axis, other.normal)) < 0.02) {
    const d = Math.abs(dot(other.normal, sub(cyl.centre, other.centroid)));
    const lines = [`Centre to the face ${fmtLen(d)}`];
    if (cyl.full) lines.push(`${cyl.concave ? "Hole" : "Boss"} edge to the face ${fmtLen(Math.abs(d - cyl.radius))}`);
    return { title: `${cyl.full ? (cyl.concave ? "Hole" : "Boss") : "Round"} to flat face`, lines };
  }
  return { title: "Between the two faces", lines: [`Centre to centre ${fmtLen(len(sub(a.centroid, b.centroid)))}`] };
}

/** Separate solids in one mesh. Triangles that share a corner belong to the same solid, so a compound of
    parts that do not share vertices (a gear on a shaft) comes apart into its parts. Used to pull an assembly apart. */
export function splitIslands(positions, index) {
  const vertCount = positions.length / 3;
  const triCount = index ? index.length / 3 : Math.floor(vertCount / 3);
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < vertCount; i++) for (let k = 0; k < 3; k++) { const v = positions[3 * i + k]; if (v < lo[k]) lo[k] = v; if (v > hi[k]) hi[k] = v; }
  const diag = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) || 1;
  const eps = Math.max(1e-6, diag * 1e-6);
  const weld = new Map(); let next = 0;
  const idOf = (i) => {
    const key = Math.round(positions[3 * i] / eps) + "," + Math.round(positions[3 * i + 1] / eps) + "," + Math.round(positions[3 * i + 2] / eps);
    let id = weld.get(key); if (id === undefined) { id = next++; weld.set(key, id); } return id;
  };
  const tv = new Int32Array(triCount * 3);
  for (let t = 0; t < triCount; t++) for (let k = 0; k < 3; k++) tv[3 * t + k] = idOf(index ? index[3 * t + k] : 3 * t + k);
  const parent = new Int32Array(next); for (let i = 0; i < next; i++) parent[i] = i;
  const find = (x) => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  for (let t = 0; t < triCount; t++) { const a = find(tv[3 * t]), b = find(tv[3 * t + 1]), c = find(tv[3 * t + 2]); parent[b] = a; parent[find(c)] = a; }
  const byRoot = new Map();
  for (let t = 0; t < triCount; t++) { const r = find(tv[3 * t]); if (!byRoot.has(r)) byRoot.set(r, []); byRoot.get(r).push(t); }
  const islands = [];
  for (const tris of byRoot.values()) {
    const c = [0, 0, 0], l = [Infinity, Infinity, Infinity], h = [-Infinity, -Infinity, -Infinity];
    for (const t of tris) for (let k = 0; k < 3; k++) {
      const i = index ? index[3 * t + k] : 3 * t + k;
      for (let a = 0; a < 3; a++) { const v = positions[3 * i + a]; c[a] += v; if (v < l[a]) l[a] = v; if (v > h[a]) h[a] = v; }
    }
    const n = tris.length * 3;
    islands.push({ tris, centroid: [c[0] / n, (c[1] / n), c[2] / n], lo: l, hi: h, mid: [(l[0] + h[0]) / 2, (l[1] + h[1]) / 2, (l[2] + h[2]) / 2] });
  }
  islands.sort((a, b) => b.tris.length - a.tris.length);
  return { islands, bounds: { lo, hi }, diag };
}
