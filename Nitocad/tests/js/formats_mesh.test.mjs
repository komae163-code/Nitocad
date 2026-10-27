import test from 'node:test';
import assert from 'node:assert/strict';
import { detect, accept, extOf, sniff, FORMATS, LIMIT_MB } from '../../frontend/import/formats.js';
import * as M from '../../frontend/import/mesh.js';

const enc = (s) => new TextEncoder().encode(s);
const CUBE_FACES = [[0,1,2],[0,2,3],[4,6,5],[4,7,6],[0,4,5],[0,5,1],[1,5,6],[1,6,2],[2,6,7],[2,7,3],[3,7,4],[3,4,0]];
const CV = [[0,0,0],[10,0,0],[10,10,0],[0,10,0],[0,0,10],[10,0,10],[10,10,10],[0,10,10]];
// triangles listed (a, c, b) so every normal points outward
const cube = (s = 1) => new Float32Array(CUBE_FACES.flatMap(([a, b, c]) => [a, c, b].flatMap((i) => CV[i].map((v) => v * s))));

test('accept() lists every requested type', () => {
  const a = accept().split(',');
  for (const e of ['.step', '.stp', '.iges', '.igs', '.brep', '.pdf', '.png', '.jpg', '.jpeg', '.webp', '.dxf', '.svg', '.kicad_pcb', '.kicad_sch', '.gbr', '.gtl', '.drl', '.stl', '.obj', '.zip'])
    assert.ok(a.includes(e), e);
});
test('extOf handles paths and no extension', () => {
  assert.equal(extOf('C:\\a\\b\\Board.KiCad_PCB'), 'kicad_pcb');
  assert.equal(extOf('noext'), '');
  assert.equal(extOf('a.tar.gz'), 'gz');
});
test('unsupported and empty files are rejected with a clear message', () => {
  assert.equal(detect('x.exe', enc('MZ')).ok, false);
  assert.match(detect('x.stl', new Uint8Array(0), 0).error, /empty/);
});
test('contents must match the extension', () => {
  assert.equal(detect('a.step', enc('ISO-10303-21;\nHEADER;')).ok, true);
  assert.match(detect('a.step', enc('<html>')).error, /does not look like/);
  assert.equal(detect('a.pdf', enc('%PDF-1.7\n')).ok, true);
  assert.equal(detect('a.pdf', enc('PK\x03\x04')).ok, false);
  assert.equal(detect('a.png', Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 13, 10])).ok, true);
  assert.equal(detect('a.jpg', Uint8Array.from([0xff, 0xd8, 0xff, 0xe0])).ok, true);
  assert.equal(detect('a.webp', enc('RIFF\0\0\0\0WEBPVP8 ')).ok, true);
  assert.equal(detect('a.svg', enc('<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>')).ok, true);
  assert.equal(detect('a.dxf', enc('  0\nSECTION\n  2\nHEADER\n')).ok, true);
  assert.equal(detect('a.kicad_pcb', enc('(kicad_pcb (version 20221018)')).ok, true);
  assert.equal(detect('a.gbr', enc('%FSLAX46Y46*%\n%MOMM*%\nM02*')).ok, true);
  assert.equal(detect('a.zip', Uint8Array.from([0x50, 0x4b, 3, 4])).ok, true);
});
test('IGES is recognised by its fixed-column section letter', () => {
  const line = ' '.repeat(72) + 'S      1\n';
  assert.equal(detect('a.igs', enc(line)).ok, true);
  assert.equal(detect('a.igs', enc('hello world')).ok, false);
});
test('size limits apply per kind', () => {
  const r = detect('a.step', enc('ISO-10303-21'), (LIMIT_MB.cad + 1) * 1024 * 1024);
  assert.equal(r.ok, false); assert.match(r.error, /at most 25 MB/);
});
test('binary STL is recognised by its exact size, text STL by keywords', () => {
  const bin = M.toSTL(cube());
  assert.equal(sniff(FORMATS.find((f) => f.id === 'stl'), new Uint8Array(bin)), true);
  assert.equal(sniff(FORMATS.find((f) => f.id === 'stl'), enc('solid x\n facet normal 0 0 1\n')), true);
  assert.equal(sniff(FORMATS.find((f) => f.id === 'stl'), new Uint8Array(200)), false);
});

test('STL round trip keeps every triangle', () => {
  const p = cube(2), back = M.parseSTL(M.toSTL(p));
  assert.deepEqual(Array.from(back), Array.from(p));
});
test('ASCII STL parses', () => {
  const s = 'solid t\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid';
  assert.deepEqual(Array.from(M.parseSTL(enc(s))), [0, 0, 0, 1, 0, 0, 0, 1, 0]);
  assert.throws(() => M.parseSTL(enc('solid empty\nendsolid')));
});
test('OBJ parses quads into triangles and negative indices', () => {
  const o = 'v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n';
  assert.equal(M.parseOBJ(o).length / 9, 2);
  assert.equal(M.parseOBJ('v 0 0 0\nv 1 0 0\nv 0 1 0\nf -3 -2 -1\n').length / 9, 1);
  assert.throws(() => M.parseOBJ('v 0 0 0\nf 1 2 3\n'));
});
test('measure: a 10 mm cube has volume 1000 and area 600', () => {
  const m = M.measure(cube());
  assert.equal(m.triangles, 12);
  assert.ok(Math.abs(m.volume - 1000) < 1e-3 && Math.abs(m.area - 600) < 1e-3);
});
test('translate, scale and bbox', () => {
  const t = M.transform(cube(), M.translation(5, -5, 2));
  assert.deepEqual(M.bbox(t).min, [5, -5, 2]);
  const s = M.transform(cube(), M.scaling(2));
  assert.deepEqual(M.bbox(s).size, [20, 20, 20]);
  assert.ok(Math.abs(M.measure(s).volume - 8000) < 1e-2);
});
test('rotation by 90 degrees about z keeps the cube a cube', () => {
  const r = M.transform(cube(), M.rotation('z', 90)), b = M.bbox(r);
  assert.ok(Math.abs(b.size[0] - 10) < 1e-4 && Math.abs(b.min[0] + 10) < 1e-4);
  assert.ok(Math.abs(M.measure(r).volume - 1000) < 1e-2);
});
test('mirroring keeps the solid right-way-out (winding is flipped)', () => {
  const m = M.transform(cube(), M.mirror('x'));
  assert.ok(M.measure(m).volume > 999, 'volume must stay positive after a mirror');
});
test('non-uniform scale and flipWinding', () => {
  const s = M.transform(cube(), M.scaling(1, 2, 3));
  assert.ok(Math.abs(M.measure(s).volume - 6000) < 1e-1);
  assert.ok(M.measure(M.flipWinding(cube())).volume < -999);
});
test('center on origin and drop to z=0', () => {
  const c = M.bbox(M.centerOnOrigin(cube())).center;
  assert.ok(c.every((v) => Math.abs(v) < 1e-5));
  const d = M.dropToZ0(M.transform(cube(), M.translation(0, 0, 7)));
  assert.equal(M.bbox(d).min[2], 0);
});
test('matrix composition applies right-to-left', () => {
  const m = M.mul(M.translation(10, 0, 0), M.scaling(2));
  assert.deepEqual(Array.from(M.transform(new Float32Array([1, 0, 0, 0, 0, 0, 0, 0, 0]), m)).slice(0, 3), [12, 0, 0]);
});
test('OBJ export welds shared vertices', () => {
  const o = M.toOBJ(cube()), v = o.split('\n').filter((l) => l.startsWith('v ')).length;
  assert.equal(v, 8);
  assert.equal(M.parseOBJ(o).length / 9, 12);
});
