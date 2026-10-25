import test from 'node:test';
import assert from 'node:assert/strict';
import * as D from '../../frontend/import/dxf.js';

const L = (...a) => a.join('\r\n') + '\r\n';
const SAMPLE = L(
  '  0', 'SECTION', '  2', 'HEADER', '  9', '$HANDSEED', '  5', '100', '  0', 'ENDSEC',
  '  0', 'SECTION', '  2', 'TABLES', '  0', 'TABLE', '  2', 'LAYER', ' 70', '2',
  '  0', 'LAYER', '  2', '0', ' 70', '0', ' 62', '7',
  '  0', 'LAYER', '  2', 'WALLS', ' 70', '0', ' 62', '1',
  '  0', 'LAYER', '  2', 'HIDDEN', ' 70', '0', ' 62', '-3',
  '  0', 'ENDTAB', '  0', 'ENDSEC',
  '  0', 'SECTION', '  2', 'BLOCKS',
  '  0', 'BLOCK', '  2', 'BOX', ' 10', '0', ' 20', '0',
  '  0', 'LINE', '  8', '0', ' 10', '0', ' 20', '0', ' 11', '2', ' 21', '0',
  '  0', 'ENDBLK', '  0', 'ENDSEC',
  '  0', 'SECTION', '  2', 'ENTITIES',
  '  0', 'LINE', '  5', '1A', '  8', 'WALLS', ' 10', '0.0', ' 20', '0.0', ' 30', '0.0', ' 11', '10.0', ' 21', '0.0', ' 31', '0.0',
  '  0', 'CIRCLE', '  5', '1B', '  8', 'WALLS', ' 10', '5', ' 20', '5', ' 40', '2',
  '  0', 'ARC', '  5', '1C', '  8', '0', ' 10', '0', ' 20', '0', ' 40', '4', ' 50', '0', ' 51', '90',
  '  0', 'LWPOLYLINE', '  5', '1D', '  8', '0', ' 90', '3', ' 70', '1', ' 10', '0', ' 20', '0', ' 42', '0.5', ' 10', '4', ' 20', '0', ' 10', '4', ' 20', '3',
  '  0', 'TEXT', '  5', '1E', '  8', '0', ' 10', '1', ' 20', '1', ' 40', '2', '  1', 'Hello', ' 50', '0',
  '  0', 'INSERT', '  5', '1F', '  8', '0', '  2', 'BOX', ' 10', '20', ' 20', '10', ' 41', '2', ' 42', '2', ' 50', '90',
  '  0', 'HATCH', '  5', '20', '  8', '0',
  '  0', 'LINE', '  5', '21', '  8', 'HIDDEN', ' 10', '0', ' 20', '0', ' 11', '1', ' 21', '1',
  '  0', 'ENDSEC', '  0', 'EOF');

const fresh = () => D.parseDXF(SAMPLE);
const byHandle = (doc, h) => D.entities(doc).find((e) => e.handle === h);

test('a no-op round trip is byte-identical', () => assert.equal(D.serializeDXF(fresh()), SAMPLE));
test('LF files round trip too', () => { const t = SAMPLE.replace(/\r\n/g, '\n'); assert.equal(D.serializeDXF(D.parseDXF(t)), t); });
test('entities and layers are read', () => {
  const doc = fresh();
  assert.deepEqual(D.entities(doc).map((e) => e.type), ['LINE', 'CIRCLE', 'ARC', 'LWPOLYLINE', 'TEXT', 'INSERT', 'HATCH', 'LINE']);
  const lay = D.layers(doc);
  assert.equal(lay.get('WALLS').color, 1);
  assert.equal(lay.get('HIDDEN').off, true);
});
test('geometry for each supported type', () => {
  const doc = fresh(), g = (h) => D.geometry(doc, byHandle(doc, h));
  assert.deepEqual(g('1A'), { k: 'line', x1: 0, y1: 0, x2: 10, y2: 0 });
  assert.deepEqual(g('1B'), { k: 'circle', cx: 5, cy: 5, r: 2 });
  assert.deepEqual(g('1C'), { k: 'arc', cx: 0, cy: 0, r: 4, a1: 0, a2: 90 });
  const p = g('1D'); assert.equal(p.closed, true); assert.deepEqual(p.pts, [[0, 0, 0.5], [4, 0, 0], [4, 3, 0]]);
  assert.equal(g('1E').text, 'Hello');
  assert.equal(D.geometry(doc, byHandle(doc, '20')), null);
});
test('flatten expands INSERT with scale, rotation and translation; hides off layers; lists unsupported', () => {
  const { items, unsupported } = D.flatten(fresh());
  assert.deepEqual(unsupported, ['HATCH']);
  assert.ok(!items.some((i) => i.layer === 'HIDDEN'));
  const ins = items.find((i) => i.id === D.entities(fresh()).find((e) => e.handle === '1F').id);
  // BOX line (0,0)-(2,0) scaled x2 -> (0,0)-(4,0), rotated 90deg -> (0,0)-(0,4), moved to (20,10)
  assert.ok(Math.abs(ins.g.x1 - 20) < 1e-9 && Math.abs(ins.g.y1 - 10) < 1e-9 && Math.abs(ins.g.x2 - 20) < 1e-9 && Math.abs(ins.g.y2 - 14) < 1e-9);
});
test('extents cover every item', () => {
  const x = D.extents(D.flatten(fresh()).items);
  assert.ok(x.x0 <= 0 && x.x1 >= 20 && x.y1 >= 14);
});
test('move translates the right coordinates only', () => {
  const doc = fresh(); const id = byHandle(doc, '1A').id;
  D.moveEntities(doc, [id], 5, -2);
  assert.deepEqual(D.geometry(doc, byHandle(doc, '1A')), { k: 'line', x1: 5, y1: -2, x2: 15, y2: -2 });
  assert.deepEqual(D.geometry(doc, byHandle(doc, '1B')), { k: 'circle', cx: 5, cy: 5, r: 2 }, 'neighbours untouched');
});
test('move a polyline and text', () => {
  const doc = fresh(); D.moveEntities(doc, [byHandle(doc, '1D').id, byHandle(doc, '1E').id], 1, 1);
  assert.deepEqual(D.geometry(doc, byHandle(doc, '1D')).pts.map((p) => p.slice(0, 2)), [[1, 1], [5, 1], [5, 4]]);
  const t = D.geometry(doc, byHandle(doc, '1E')); assert.equal(t.x, 2); assert.equal(t.y, 2);
});
test('rotate 90 degrees about the origin, including arc angles and text rotation', () => {
  const doc = fresh(); D.rotateEntities(doc, [byHandle(doc, '1A').id, byHandle(doc, '1C').id, byHandle(doc, '1E').id], 90, 0, 0);
  const l = D.geometry(doc, byHandle(doc, '1A')); assert.ok(Math.abs(l.x2) < 1e-9 && Math.abs(l.y2 - 10) < 1e-9);
  const a = D.geometry(doc, byHandle(doc, '1C')); assert.equal(a.a1, 90); assert.equal(a.a2, 180);
  assert.equal(D.geometry(doc, byHandle(doc, '1E')).rot, 90);
});
test('scale about a point scales radius, text height and insert scale', () => {
  const doc = fresh(); D.scaleEntities(doc, [byHandle(doc, '1B').id, byHandle(doc, '1E').id, byHandle(doc, '1F').id], 2, 0, 0);
  const c = D.geometry(doc, byHandle(doc, '1B')); assert.deepEqual([c.cx, c.cy, c.r], [10, 10, 4]);
  assert.equal(D.geometry(doc, byHandle(doc, '1E')).h, 4);
  const i = D.geometry(doc, byHandle(doc, '1F')); assert.deepEqual([i.x, i.y, i.sx, i.sy], [40, 20, 4, 4]);
});
test('entities the editor cannot transform are reported, not silently ignored', () => {
  const doc = fresh(); const r = D.moveEntities(doc, [byHandle(doc, '20').id], 1, 1);
  assert.deepEqual(r, { changed: 0, skipped: ['HATCH'] });
});
test('delete removes exactly those entities', () => {
  const doc = fresh(); const n = D.deleteEntities(doc, [byHandle(doc, '1A').id, byHandle(doc, '1D').id]);
  assert.equal(n, 2);
  assert.deepEqual(D.entities(doc).map((e) => e.handle), ['1B', '1C', '1E', '1F', '20', '21']);
});
test('changing layer creates the layer table record when missing', () => {
  const doc = fresh(); D.setLayer(doc, [byHandle(doc, '1A').id], 'DOORS');
  assert.equal(byHandle(doc, '1A').layer, 'DOORS');
  assert.ok(D.layers(doc).has('DOORS'));
  const again = D.parseDXF(D.serializeDXF(doc)); assert.ok(D.layers(again).has('DOORS'));
});
test('added entities get unique handles and bump $HANDSEED', () => {
  const doc = fresh();
  D.addLine(doc, { x1: 0, y1: 0, x2: 3, y2: 4, layer: 'WALLS' });
  D.addCircle(doc, { cx: 1, cy: 1, r: 1 });
  D.addRect(doc, { x1: 0, y1: 0, x2: 2, y2: 2 });
  D.addText(doc, { x: 0, y: 0, text: 'Hi' });
  D.addArc(doc, { cx: 0, cy: 0, r: 1, a1: 0, a2: 90 });
  const hs = D.entities(doc).map((e) => e.handle);
  assert.equal(new Set(hs).size, hs.length);
  const seed = doc.pairs[doc.pairs.findIndex((p) => p[0] === 9 && p[1] === '$HANDSEED') + 1][1];
  assert.ok(parseInt(seed, 16) > Math.max(...hs.map((h) => parseInt(h, 16))));
  const re = D.parseDXF(D.serializeDXF(doc));
  assert.equal(D.entities(re).length, 13);
  assert.deepEqual(D.geometry(re, D.entities(re).find((e) => e.type === 'LINE' && e.layer === 'WALLS' && !['1A', '21'].includes(e.handle))), { k: 'line', x1: 0, y1: 0, x2: 3, y2: 4 });
});
test('old-style POLYLINE with VERTEX children is read and moved', () => {
  const t = L('  0', 'SECTION', '  2', 'ENTITIES', '  0', 'POLYLINE', '  8', '0', ' 66', '1', ' 70', '1',
    '  0', 'VERTEX', '  8', '0', ' 10', '0', ' 20', '0', '  0', 'VERTEX', '  8', '0', ' 10', '5', ' 20', '0', '  0', 'VERTEX', '  8', '0', ' 10', '5', ' 20', '5', '  0', 'SEQEND', '  8', '0', '  0', 'ENDSEC', '  0', 'EOF');
  const doc = D.parseDXF(t), en = D.entities(doc);
  assert.equal(en.length, 1);
  assert.equal(D.geometry(doc, en[0]).pts.length, 3);
  D.moveEntities(doc, [en[0].id], 10, 0);
  assert.deepEqual(D.geometry(doc, D.entities(doc)[0]).pts.map((p) => p[0]), [10, 15, 15]);
  assert.equal(D.addLine.length, 2);
});
test('a DXF without handles (R12 style) gets entities without handles', () => {
  const t = L('  0', 'SECTION', '  2', 'ENTITIES', '  0', 'LINE', '  8', '0', ' 10', '0', ' 20', '0', ' 11', '1', ' 21', '1', '  0', 'ENDSEC', '  0', 'EOF');
  const doc = D.parseDXF(t); D.addCircle(doc, { cx: 0, cy: 0, r: 1 });
  assert.ok(!doc.pairs.some((p) => p[0] === 5));
});
test('binary and damaged files are refused with a readable message', () => {
  assert.throws(() => D.parseDXF('AutoCAD Binary DXF\r\n\x1a\0'), /binary DXF/);
  assert.throws(() => D.parseDXF('  0\nSECTION\n  2\n'), /cut off|damaged|Bad group/);
  assert.throws(() => D.parseDXF('abc\ndef\n'), /Bad group code/);
});
test('numbers never use exponent notation', () => {
  const doc = fresh(); D.moveEntities(doc, [byHandle(doc, '1A').id], 1e-12, 123456789.123456789);
  const out = D.serializeDXF(doc); assert.ok(!/e[-+]?\d/i.test(out.split('ENTITIES')[1]));
});
