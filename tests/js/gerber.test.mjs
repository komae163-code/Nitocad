import test from 'node:test';
import assert from 'node:assert/strict';
import * as G from '../../frontend/import/gerber.js';

const BASIC = `%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.200000*%
%ADD11R,2.000000X1.000000*%
%ADD12O,1.500000X0.800000*%
%LPD*%
D10*
X1000000Y1000000D02*
X5000000Y1000000D01*
Y3000000D01*
D11*
X2000000Y2000000D03*
D12*
X6000000Y6000000D03*
G36*
X0Y0D02*
X1000000Y0D01*
X1000000Y1000000D01*
G37*
%LPC*%
D10*
X0Y0D03*
M02*`;

test('format, units and apertures are read', () => {
  const g = G.parseGerber(BASIC);
  assert.equal(g.units, 'mm'); assert.deepEqual(g.fmt, { zeros: 'L', xi: 4, xd: 6, yi: 4, yd: 6 });
  assert.deepEqual(g.apertures.get(10), { type: 'C', d: 0.2 });
  assert.deepEqual(g.apertures.get(11), { type: 'R', w: 2, h: 1 });
  assert.equal(g.apertures.get(12).type, 'O');
});
test('draws, flashes, regions and polarity', () => {
  const g = G.parseGerber(BASIC), t = g.ops.map((o) => o.t);
  assert.deepEqual(t, ['draw', 'draw', 'flash', 'flash', 'region', 'flash']);
  assert.deepEqual([g.ops[0].x1, g.ops[0].y1, g.ops[0].x2, g.ops[0].y2], [1, 1, 5, 1]);
  assert.deepEqual([g.ops[1].x1, g.ops[1].y1, g.ops[1].x2, g.ops[1].y2], [5, 1, 5, 3], 'modal X is kept');
  assert.equal(g.ops[2].ap.type, 'R'); assert.deepEqual([g.ops[2].x, g.ops[2].y], [2, 2]);
  assert.equal(g.ops[4].contours[0].length, 2);
  assert.equal(g.ops[5].dark, false, 'LPC makes the flash clear');
});
test('bounding box includes aperture size', () => {
  const g = G.parseGerber(BASIC);
  assert.ok(g.bbox.x0 <= -0.1 && g.bbox.x1 >= 6.7 && g.bbox.y1 >= 6.4);
});
test('arcs: G75 multi-quadrant, clockwise and counter-clockwise, and full circle', () => {
  const t = (body) => G.parseGerber(`%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\nG75*\n${body}\nM02*`).ops.find((o) => o.t === 'arc');
  const ccw = t('X10000Y0D02*\nG03X0Y10000I-10000J0D01*');
  assert.ok(Math.abs(ccw.r - 1) < 1e-9 && Math.abs(ccw.cx) < 1e-9 && ccw.ccw);
  assert.ok(Math.abs(ccw.a2 - ccw.a1 - Math.PI / 2) < 1e-9);
  const cw = t('X10000Y0D02*\nG02X0Y-10000I-10000J0D01*');
  assert.ok(Math.abs(cw.a1 - cw.a2 - Math.PI / 2) < 1e-9 && !cw.ccw);
  const full = t('X10000Y0D02*\nG03X10000Y0I-10000J0D01*');
  assert.ok(Math.abs(full.a2 - full.a1 - 2 * Math.PI) < 1e-9);
});
test('G74 single-quadrant picks the centre that makes a quarter arc', () => {
  const g = G.parseGerber('%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\nG74*\nX10000Y0D02*\nG03X0Y10000I10000J0D01*\nM02*').ops.find((o) => o.t === 'arc');
  assert.ok(Math.abs(g.cx) < 1e-9 && Math.abs(g.cy) < 1e-9 && Math.abs(g.r - 1) < 1e-9);
});
test('trailing-zero-omitted coordinates are padded', () => {
  const g = G.parseGerber('%FSTAX24Y24*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\nX15Y2D03*\nM02*');
  assert.deepEqual([g.ops[0].x, g.ops[0].y], [15, 20], 'Y2 with trailing zeros omitted is 2xxxxx = 20');
});
test('aperture macros are evaluated with a safe expression parser', () => {
  const g = G.parseGerber(`%FSLAX24Y24*%\n%MOMM*%\n%AMRR*\n21,1,$1,$2,0,0,0*\n1,1,$2/2,$1x0.5,0*%\n%ADD10RR,4X2*%\nD10*\nX0Y0D03*\nM02*`);
  const a = g.apertures.get(10); assert.equal(a.type, 'M');
  assert.deepEqual(a.prims[0], { k: 'cline', ex: 1, w: 4, h: 2, x: 0, y: 0, rot: 0 });
  assert.equal(a.prims[1].d, 1); assert.equal(a.prims[1].x, 2);
});
test('macro expressions cannot run code', () => {
  assert.throws(() => G.parseGerber('%FSLAX24Y24*%\n%AMX*\n1,1,process.exit(1),0,0*%\n%ADD10X*%\nM02*'), /Bad macro/);
});
test('unsupported constructs are reported, not hidden', () => {
  assert.ok(G.parseGerber('%FSLAX24Y24*%\n%MOMM*%\n%SRX2Y2I1J1*%\nM02*').warnings.some((w) => /step-and-repeat/.test(w)));
  assert.ok(G.parseGerber('%FSLAX24Y24*%\n%MOMM*%\n%AMT*\n7,0,0,1,0.8,0.1,45*%\n%ADD10T*%\nM02*').warnings.some((w) => /primitive 7/.test(w)));
});
test('malformed files raise readable errors', () => {
  assert.throws(() => G.parseGerber('X1Y1D03*'), /before the %FS/);
  assert.throws(() => G.parseGerber('%FSLAX24Y24*%\nX1Y1D03'), /missing its terminating/);
  assert.throws(() => G.parseGerber('%FSLAX24Y24'), /Unterminated/);
});

const SIMPLE = '%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.1*%\n%ADD11R,2X1*%\nD10*\nX10000Y10000D02*\nX50000D01*\nY30000D01*\nD11*\nX20000Y20000D03*\nM02*\n';
const pts = (txt) => G.parseGerber(txt).ops.map((o) => (o.t === 'draw' ? [o.x1, o.y1, o.x2, o.y2] : [o.x, o.y]));
test('translate moves every coordinate and leaves everything else alone', () => {
  const out = G.transformGerber(SIMPLE, { dx: 2, dy: -1 });
  assert.deepEqual(pts(out), [[3, 0, 7, 0], [7, 0, 7, 2], [4, 1]]);
  assert.match(out, /%ADD10C,0.1\*%/);
});
test('translating by zero reproduces the geometry exactly', () => assert.deepEqual(pts(G.transformGerber(SIMPLE, { dx: 0, dy: 0 })), pts(SIMPLE)));
test('mirror in X negates x and flips arc direction', () => {
  assert.deepEqual(pts(G.transformGerber(SIMPLE, { mirrorX: true })), [[-1, 1, -5, 1], [-5, 1, -5, 3], [-2, 2]]);
  const arc = '%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\nG75*\nX10000Y0D02*\nG03X0Y10000I-10000J0D01*\nM02*';
  const m = G.parseGerber(G.transformGerber(arc, { mirrorX: true })).ops.find((o) => o.t === 'arc');
  assert.equal(m.ccw, false); assert.ok(Math.abs(m.cx) < 1e-9 && Math.abs(m.r - 1) < 1e-9);
});
test('rotate 90 degrees swaps rectangular aperture sides and turns coordinates', () => {
  const out = G.transformGerber(SIMPLE, { rot: 90 });
  assert.deepEqual(pts(out), [[-1, 1, -1, 5], [-1, 5, -3, 5], [-2, 2]]);
  assert.match(out, /%ADD11R,1X2\*%/);
});
test('rotating four times by 90 returns to the start', () => {
  let t = SIMPLE; for (let k = 0; k < 4; k++) t = G.transformGerber(t, { rot: 90 });
  assert.deepEqual(pts(t), pts(SIMPLE));
});
test('mirror/rotate refuse files with macros, G74 arcs or SR, but allow plain moves', () => {
  const mac = '%FSLAX24Y24*%\n%MOMM*%\n%AMRR*\n1,1,1,0,0*%\n%ADD10RR*%\nD10*\nX0Y0D03*\nM02*';
  assert.throws(() => G.transformGerber(mac, { mirrorX: true }), /macro or polygon/);
  assert.ok(G.transformGerber(mac, { dx: 1 }));
  const g74 = '%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.1*%\nD10*\nG74*\nX10000Y0D02*\nG03X0Y10000I10000J0D01*\nM02*';
  assert.throws(() => G.transformGerber(g74, { rot: 90 }), /single-quadrant/);
  assert.throws(() => G.transformGerber('%FSLAX24Y24*%\n%MOMM*%\n%SRX2Y2I1J1*%\nM02*', { dx: 1 }), /cannot be moved safely/);
  assert.throws(() => G.transformGerber(SIMPLE, { rot: 45 }), /multiple of 90/);
});
test('transformed output is valid Gerber that parses to the same shape count', () => {
  const out = G.transformGerber(BASIC, { dx: 1, dy: 1 });
  assert.equal(G.parseGerber(out).ops.length, G.parseGerber(BASIC).ops.length);
});

const DRILL = 'M48\nMETRIC,TZ\nT1C0.800\nT2C1.000\n%\nT1\nX10.0Y20.0\nX30.5Y-5.25\nT2\nX0.0Y0.0G85X4.0Y0.0\nM30\n';
test('Excellon: tools, hits and slots', () => {
  const d = G.parseExcellon(DRILL);
  assert.equal(d.units, 'mm'); assert.deepEqual([...d.tools], [[1, 0.8], [2, 1]]);
  assert.deepEqual(d.hits.map((h) => [h.x, h.y, h.d]), [[10, 20, 0.8], [30.5, -5.25, 0.8]]);
  assert.deepEqual(d.slots, [{ x1: 0, y1: 0, x2: 4, y2: 0, d: 1 }]);
  assert.ok(d.bbox.x1 > 30.5);
});
test('Excellon transform moves hits and both ends of slots, not tool sizes', () => {
  const out = G.transformExcellon(DRILL, { dx: 5, dy: 5 }); const d = G.parseExcellon(out);
  assert.deepEqual(d.hits.map((h) => [h.x, h.y]), [[15, 25], [35.5, -0.25]]);
  assert.deepEqual([d.slots[0].x1, d.slots[0].x2], [5, 9]);
  assert.match(out, /T1C0.800/);
  assert.match(out, /X15\.0Y25\.0/, 'whole numbers must keep their decimal point or a drill reader sees 0.015');
  const r = G.parseExcellon(G.transformExcellon(DRILL, { rot: 90 }));
  assert.deepEqual([r.hits[0].x, r.hits[0].y], [-20, 10]);
});
test('Excellon integer coordinates are read with a stated assumption but refused for editing', () => {
  const t = 'M48\nMETRIC,LZ\nT1C0.8\n%\nT1\nX010000Y020000\nM30\n';
  assert.ok(G.parseExcellon(t).warnings.some((w) => /no decimal point/.test(w)));
  assert.throws(() => G.transformExcellon(t, { dx: 1 }), /integer coordinates/);
});
test('Excellon detection and layer styles', () => {
  assert.equal(G.isExcellonText(DRILL), true); assert.equal(G.isExcellonText(BASIC), false);
  assert.equal(G.layerStyle('board-F_Cu.gbr').label, 'Top copper'); assert.equal(G.layerStyle('x.gko').label, 'Outline'); assert.equal(G.layerStyle('x.drl').label, 'Drill');
});
