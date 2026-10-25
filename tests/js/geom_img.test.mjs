import test from 'node:test';
import assert from 'node:assert/strict';
import * as G from '../../frontend/import/geom2d.js';
import * as I from '../../frontend/import/imgops.js';

const near = (a, b, e = 1e-6) => assert.ok(Math.abs(a - b) <= e, `${a} !~ ${b}`);

test('segment distance covers interior, end caps and degenerate segments', () => {
  near(G.segDist(5, 3, 0, 0, 10, 0), 3); near(G.segDist(-4, 3, 0, 0, 10, 0), 5); near(G.segDist(3, 4, 0, 0, 0, 0), 5);
});
test('arc picking respects the sweep, including arcs that cross 0 degrees', () => {
  near(G.arcDist(0, 5, 0, 0, 5, 0, 90), 0);
  assert.ok(G.arcDist(0, -5, 0, 0, 5, 0, 90) > 5, 'point on the missing side of the circle is far from the arc');
  near(G.arcDist(5, 0, 0, 0, 5, 350, 10), 0);
  assert.ok(G.onArc(0, 350, 10) && !G.onArc(180, 350, 10));
});
test('bulge 1 is a counter-clockwise half circle', () => {
  const p = G.bulgePoints(0, 0, 10, 0, 1);
  assert.deepEqual(p[p.length - 1], [10, 0]);
  const mid = p[Math.floor(p.length / 2) - 1]; near(Math.hypot(mid[0] - 5, mid[1]), 5, 0.05); assert.ok(mid[1] < 0, 'CCW from (0,0) to (10,0) bows below the chord');
  const cw = G.bulgePoints(0, 0, 10, 0, -1); assert.ok(cw[Math.floor(cw.length / 2) - 1][1] > 0);
  assert.deepEqual(G.bulgePoints(0, 0, 3, 4, 0), [[3, 4]]);
});
test('quarter-circle bulge ends on the circle', () => {
  const b = Math.tan(Math.PI / 8), p = G.bulgePoints(10, 0, 0, 10, b);
  for (const q of p) near(Math.hypot(q[0], q[1]), 10, 0.02);
});
test('closed polylines add the closing segment', () => {
  assert.equal(G.polyPoints([[0, 0, 0], [1, 0, 0], [1, 1, 0]], true).length, 4);
  assert.equal(G.polyPoints([[0, 0, 0], [1, 0, 0], [1, 1, 0]], false).length, 3);
});
test('circle through three points, direction and radius', () => {
  const c = G.circleFrom3([1, 0], [0, 1], [-1, 0]);
  near(c.cx, 0); near(c.cy, 0); near(c.r, 1); assert.equal(c.ccw, true);
  assert.equal(G.circleFrom3([1, 0], [0, -1], [-1, 0]).ccw, false);
  assert.equal(G.circleFrom3([0, 0], [1, 1], [2, 2]), null, 'collinear points have no circle');
});
test('point in polygon', () => {
  const sq = [[0, 0], [10, 0], [10, 10], [0, 10]];
  assert.ok(G.pointInPoly(5, 5, sq) && !G.pointInPoly(15, 5, sq));
});
test('hit distances per DXF geometry kind', () => {
  near(G.hitDistance({ k: 'line', x1: 0, y1: 0, x2: 10, y2: 0 }, 5, 2), 2);
  near(G.hitDistance({ k: 'circle', cx: 0, cy: 0, r: 5 }, 8, 0), 3);
  near(G.hitDistance({ k: 'poly', pts: [[0, 0, 0], [10, 0, 0], [10, 10, 0]], closed: true }, 5, 5), 0, 0.01);
  near(G.hitDistance({ k: 'ellipse', cx: 0, cy: 0, mx: 10, my: 0, ratio: 0.5, t1: 0, t2: 2 * Math.PI }, 0, 5), 0, 0.05);
  assert.equal(G.hitDistance({ k: 'text', x: 0, y: 0, h: 2, rot: 0, text: 'abc' }, 1, 1), 0);
  assert.equal(G.hitDistance({ k: 'unknown' }, 0, 0), Infinity);
});
test('item boxes', () => {
  assert.deepEqual(G.itemBox({ k: 'circle', cx: 5, cy: 5, r: 2 }), { x0: 3, y0: 3, x1: 7, y1: 7 });
  assert.equal(G.itemBox({ k: 'weird' }), null);
  assert.ok(G.boxHit(G.boxOf([5, 5], [0, 0]), [2, 2]));
});

test('crop is clamped to the image and rejects empty rectangles', () => {
  assert.deepEqual(I.clampCrop({ x: -5, y: -5, w: 30, h: 30 }, 20, 20), { x: 0, y: 0, w: 20, h: 20 });
  assert.deepEqual(I.clampCrop({ x: 10, y: 10, w: -5, h: -5 }, 20, 20), { x: 5, y: 5, w: 5, h: 5 }, 'drag up/left works');
  assert.equal(I.clampCrop({ x: 30, y: 30, w: 5, h: 5 }, 20, 20), null);
});
test('resize keeps aspect ratio and caps the pixel count', () => {
  assert.deepEqual(I.fitSize(200, 100, 100, 0, true), { w: 100, h: 50 });
  assert.deepEqual(I.fitSize(200, 100, 0, 25, true), { w: 50, h: 25 });
  assert.equal(I.fitSize(200, 100, 20000, 20000, false), null);
  assert.equal(I.fitSize(200, 100, 0, 0, true), null);
});
const px = (r, g, b, a = 255) => new Uint8ClampedArray([r, g, b, a]);
test('image adjustments', () => {
  assert.deepEqual(Array.from(I.adjust(px(100, 100, 100), { brightness: 10 })), [126, 126, 126, 255]);
  assert.deepEqual(Array.from(I.adjust(px(10, 20, 30), { invert: true })), [245, 235, 225, 255]);
  const g = Array.from(I.adjust(px(255, 0, 0), { gray: true })); assert.equal(g[0], g[1]); assert.equal(g[1], g[2]); assert.equal(g[0], 76);
  assert.deepEqual(Array.from(I.adjust(px(1, 2, 3, 77), {})).slice(0, 4), [1, 2, 3, 77], 'no-op keeps alpha and values');
  const des = Array.from(I.adjust(px(255, 0, 0), { saturation: -100 })); assert.equal(des[0], des[1]);
});
test('rotate 90 twice equals rotate 180; four times is the identity; flips undo themselves', () => {
  const w = 3, h = 2, d = new Uint8ClampedArray(w * h * 4).map((_, i) => i);
  let r = { data: d, w, h }; for (let k = 0; k < 4; k++) r = I.rotate90(r.data, r.w, r.h, true);
  assert.deepEqual(Array.from(r.data), Array.from(d)); assert.equal(r.w, w);
  const once = I.rotate90(d, w, h, true); assert.deepEqual([once.w, once.h], [2, 3]);
  assert.deepEqual(Array.from(I.rotate90(once.data, 2, 3, false).data), Array.from(d), 'ccw undoes cw');
  assert.deepEqual(Array.from(I.flip(I.flip(d, w, h, true), w, h, true)), Array.from(d));
  assert.deepEqual(Array.from(I.flip(d, w, h, false)).slice(0, 4), Array.from(d.slice(w * 4, w * 4 + 4)));
});
