import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const SRC = fs.readFileSync(new URL('../../frontend/studio.js', import.meta.url), 'utf8');
const HTML = fs.readFileSync(new URL('../../frontend/studio.html', import.meta.url), 'utf8');
const VIEWER = fs.readFileSync(new URL('../../frontend/exploded.html', import.meta.url), 'utf8');

function grab(name) {
  const m = new RegExp(`(?:async )?function ${name}\\(`).exec(SRC);
  assert.ok(m, `function ${name} not found`);
  let i = SRC.indexOf('{', m.index), depth = 0, j = i;
  for (; j < SRC.length; j++) { const ch = SRC[j]; if (ch === '{') depth++; else if (ch === '}' && --depth === 0) break; }
  return SRC.slice(m.index, j + 1);
}
function sandbox() {
  const ctx = { out: {} };
  vm.createContext(ctx);
  vm.runInContext(['const esc = (s) => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");',
    grab('isVehicle'), grab('vehicleBoxHtml'), grab('vehicleFileRows'),
    'out.isVehicle = isVehicle; out.vehicleBoxHtml = vehicleBoxHtml; out.vehicleFileRows = vehicleFileRows;'].join('\n'), ctx);
  return ctx.out;
}

const VEH = { id: 'p1', title: 'Assembly', partType: 'assembly', spec: { parameters: { vehicle: { name: 'wheelbarrow' }, parts: [] } } };

test('only an assembly that carries a vehicle spec is a vehicle', () => {
  const { isVehicle } = sandbox();
  assert.equal(isVehicle(VEH), true);
  assert.equal(isVehicle({ partType: 'assembly', spec: { parameters: { parts: [] } } }), false);
  assert.equal(isVehicle({ partType: 'l_bracket', spec: { parameters: { vehicle: {} } } }), false);
  assert.equal(isVehicle({ partType: 'assembly' }), false);
});

test('the plan box shows sizes as tap-to-change chips, groups, checks, and the honesty note', () => {
  const { vehicleBoxHtml } = sandbox();
  const h = vehicleBoxHtml({ ...VEH, vehicleSummary: { name: 'wheel_barrow', parts: 13, notes: 'Concept <layout>',
    sizes: [{ label: 'wheel dia', value: 400 }], groups: { tray: 1, wheel: 4 },
    checks: [{ name: 'fits_through_door', status: 'pass', detail: '' }, { name: 'handles_long_enough', status: 'fail', detail: 'too short' }], external: ['bolt'] } });
  assert.ok(h.includes('data-fill="Make the wheel dia "') && h.includes('wheel dia 400'));
  assert.ok(h.includes('tray 1') && h.includes('fits through door') && h.includes('too short'));
  assert.ok(h.includes('Not load-rated') && h.includes('Concept &lt;layout&gt;') && !h.includes('<layout>'));
  assert.ok(h.includes('bolt'));
});

test('the plan box has a loading state and a failure state', () => {
  const { vehicleBoxHtml } = sandbox();
  assert.ok(vehicleBoxHtml(VEH).includes('Loading the plan'));
  assert.ok(vehicleBoxHtml({ ...VEH, vehicleSummary: null }).includes('could not load'));
});

test('files: the exploded view is always open, the package is locked until Generate', () => {
  const { vehicleFileRows } = sandbox();
  const locked = vehicleFileRows(VEH, true), open = vehicleFileRows(VEH, false);
  assert.ok(locked.includes('data-veh-exploded="p1"') && open.includes('data-veh-exploded="p1"'));
  assert.ok(locked.includes('Unlocks when you press Generate') && !locked.includes('data-veh-package'));
  assert.ok(open.includes('data-veh-package="p1"'));
});

test('the Assembly panel offers the vehicle starters and the free-text box', () => {
  for (const id of ['asmVehicle', 'asmVehText', 'asmVehGo']) assert.ok(HTML.includes(`id="${id}"`), id);
  assert.ok(HTML.includes('data-veh="I want to create a wheelbarrow"'));
  assert.ok(SRC.includes('"Vehicle: " + t'));
});

test('the exploded viewer accepts data from the studio, same origin only', () => {
  assert.ok(VIEWER.includes("type!=='nitocad-exploded'") && VIEWER.includes('ev.origin!==location.origin'));
});

function cardSandbox() {
  const ctx = { out: {}, num2: (v) => String(v) };
  vm.createContext(ctx);
  vm.runInContext(['const esc = (s) => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");',
    grab('fileCardHtml'), 'out.fileCardHtml = fileCardHtml;'].join('\n'), ctx);
  return ctx.out.fileCardHtml;
}
const CARD = { name: 'plate.png', kind: 'png', size: '12 KB', rows: [['Flat size', '100 x 69 mm']], dims: [], features: [], missing: [], notes: [], warnings: ['No scale.'], description: 'A flat part.', text: '', numbers: [] };

test('a kept file offers Edit as a part, and a traced outline is shown on the card', () => {
  const html = cardSandbox();
  const h = html({ id: 'm1', fileCard: { ...CARD, importId: 'imp1', previewSvg: '<svg xmlns="http://www.w3.org/2000/svg"></svg>' } });
  assert.ok(h.includes('data-fedit="m1"') && h.includes('Edit as a part') && h.includes('fc-trace'));
});

test('a file the server did not keep has no edit button', () => {
  const h = cardSandbox()({ id: 'm2', fileCard: CARD });
  assert.ok(!h.includes('data-fedit'));
});

test('the edit button opens the kept import through send() without clearing the composer', () => {
  assert.ok(SRC.includes('async function send(text, templateId, importId)'));
  assert.ok(SRC.includes('Backend.startImport({ chat: c, importId })'));
  assert.ok(SRC.includes('/imports/${importId}/part'));
  assert.ok(SRC.includes('if (!templateId && !importId)'));
});

// ---------------------------------------------------------------- picture tracing controls on the file card
function traceSandbox() {
  const ctx = { out: {}, fileStore: {} };
  vm.createContext(ctx);
  vm.runInContext(['const esc = (s) => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");',
    grab('traceControlsHtml'), 'out.traceControlsHtml = traceControlsHtml;'].join('\n'), ctx);
  return ctx;
}

test('a traced picture offers a real-size box and Trace again, and keeps the mode it was traced in', () => {
  const ctx = traceSandbox();
  ctx.fileStore.m1 = { file: { name: 'plate.png' } };
  const h = ctx.out.traceControlsHtml({ id: 'm1' }, { kindRaw: 'png', trace: { mode: 'outline' } });
  assert.match(h, /data-fretrace="m1"/);
  assert.match(h, /overall_width_mm/); assert.match(h, /scale_mm_per_px/);
  assert.match(h, /<option value="outline" selected>/);
  assert.match(h, /Trace again/);
});

test('a PDF offers to trace page 1; nothing is offered when the file was not kept in this tab', () => {
  const ctx = traceSandbox();
  ctx.fileStore.m2 = { file: { name: 'drawing.pdf' } };
  assert.match(ctx.out.traceControlsHtml({ id: 'm2' }, { kindRaw: 'pdf' }), /Trace page 1/);
  assert.equal(ctx.out.traceControlsHtml({ id: 'm3' }, { kindRaw: 'png', trace: {} }), '');
  assert.equal(ctx.out.traceControlsHtml({ id: 'm2' }, { kindRaw: 'pdf', error: 'x' }), '');
});
