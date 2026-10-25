import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const SRC = fs.readFileSync(new URL('../../frontend/studio.js', import.meta.url), 'utf8');
const HTML = fs.readFileSync(new URL('../../frontend/studio.html', import.meta.url), 'utf8');

/* Pull one top-level function out of studio.js by matching its braces, so the panels can be tested
   without loading three.js or a DOM. */
function grab(name) {
  const m = new RegExp(`(?:async )?function ${name}\\(`).exec(SRC);
  assert.ok(m, `function ${name} not found`);
  let i = SRC.indexOf('{', m.index), depth = 0, j = i;
  for (; j < SRC.length; j++) {
    const ch = SRC[j];
    if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) break;
  }
  return SRC.slice(m.index, j + 1);
}
function grabConst(name) {
  const m = new RegExp(`const ${name} = [^\\n]*\\n`).exec(SRC);
  assert.ok(m, `const ${name} not found`);
  return m[0];
}

function sandbox(S = {}, DEMO = false) {
  const ctx = { S, DEMO, out: {} };
  vm.createContext(ctx);
  const code = [grabConst('CHECK_PROCESSES'), grabConst('CHECK_TAG'), grabConst('NO_CHECK'),
    'const esc = (s) => String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");',
    grab('reportHtml'), grab('checkCardHtml'), grab('assemblyChips'), grab('assemblyHtml'), grab('requirementsHtml'),
    'out.reportHtml = reportHtml; out.checkCardHtml = checkCardHtml; out.assemblyHtml = assemblyHtml; out.assemblyChips = assemblyChips;'].join('\n');
  vm.runInContext(code, ctx);
  return ctx.out;
}

const REPORT = { process: 'cnc', headline: '1 problem, 1 to review, 1 passed, 1 not checked.', disclaimer: 'Guidance only.',
  assumptions: ['No material is set.'],
  checks: [{ id: 'a', title: 'Thin <walls>', status: 'fail', detail: 'Too thin.', measured: '0.3', limit: '0.8' },
           { id: 'b', title: 'Size', status: 'warn', detail: 'Big.' }, { id: 'c', title: 'Closed', status: 'pass', detail: 'Fine.' },
           { id: 'd', title: 'Access', status: 'not_checked', detail: 'Needs a plan.' }] };

test('the page has the Templates button and keeps the wider attach types', () => {
  assert.match(HTML, /id="tplBtn"/);
  for (const ext of ['.dxf', '.svg', '.kicad_pcb', '.gbr', '.drl']) assert.ok(HTML.includes(ext), ext);
});

test('a report shows one tag per status and escapes the part text', () => {
  const { reportHtml } = sandbox();
  const h = reportHtml(REPORT);
  for (const cls of ['chk-tag bad', 'chk-tag warn', 'chk-tag ok', 'chk-tag nc']) assert.ok(h.includes(cls), cls);
  assert.ok(h.includes('Thin &lt;walls&gt;') && !h.includes('Thin <walls>'));
  assert.ok(h.includes('0.3') && h.includes('limit 0.8'));
  assert.ok(h.includes('No material is set.') && h.includes('Guidance only.') && h.includes(REPORT.headline));
});

test('the check card prices the run from the server and picks the process', () => {
  const { checkCardHtml } = sandbox({ ai: { check_credits: 7 }, checkProc: 'fdm' });
  const h = checkCardHtml({ partType: 'l_bracket', checks: {} });
  assert.ok(h.includes('Run check · 7 credits') && h.includes('data-check="fdm"'));
  assert.ok(h.includes('data-proc="resin"') && h.includes('data-proc="injection_molding"') && h.includes('data-proc="sheet_metal"'));
  assert.ok(h.includes('class="chip on" data-proc="fdm"'));
});

test('the check card defaults to 5 credits and CNC', () => {
  const { checkCardHtml } = sandbox({});
  const h = checkCardHtml({ partType: 'gear', checks: {} });
  assert.ok(h.includes('Run check · 5 credits') && h.includes('data-check="cnc"'));
});

test('a paid check shows its saved report and no Run button, and marks the chip done', () => {
  const { checkCardHtml } = sandbox({ checkProc: 'cnc' });
  const h = checkCardHtml({ partType: 'gear', checks: { cnc: REPORT } });
  assert.ok(h.includes(REPORT.headline) && !h.includes('data-check='));
  assert.ok(h.includes('CNC · done'));
});

test('assemblies get a note and parts the server cannot check get nothing', () => {
  const { checkCardHtml } = sandbox({});
  assert.ok(checkCardHtml({ partType: 'assembly', checks: {} }).includes('Assemblies are not checked yet'));
  for (const t of ['pcb_board', 'building', 'road_segment', 'box_culvert', 'retaining_wall'])
    assert.equal(checkCardHtml({ partType: t, checks: {} }), '', t);
});

const ASM = { bom: { part_count: 2, line_count: 2, rows: [{ item: 1, quantity: 1, names: ['shaft'], part_type: 'shaft', material: null, total_mass_g: null }] },
  mass_properties: { complete: true, mass_g: 12.5, parts_counted: 2, center_of_mass_mm: [0, 0, 10] }, overall_size_mm: [44, 44, 60],
  interferences: [{ a: 'plate', b: 'bracket', volume_mm3: 12.5, severity: 'major' }], disconnected_groups: [['nut']], unchecked_pairs: [],
  bom_csv: 'item,qty\n1,1',
  parts: [{ name: 'shaft', part_type: 'shaft', position_mm: [0, 0, 0], size_mm: [8, 8, 60], mass_g: 2.4 },
          { name: 'gear', part_type: 'gear', position_mm: [0, 0, 20], size_mm: [44, 44, 10], mass_g: null }] };

test('the assembly section has the per-part table AND the overlap, floating and mass findings', () => {
  const { assemblyHtml } = sandbox({}, true);
  const h = assemblyHtml({ partType: 'assembly', facts: { assembly: ASM }, notes: [] });
  assert.ok(h.includes('<th>Position mm</th>') && h.includes('<th>Size mm</th>'));
  assert.ok(h.includes('0, 0, 20') && h.includes('44 x 44 x 10') && h.includes('2.4'));
  assert.ok(h.includes('overlap by 12.5') && h.includes('(serious)'));
  assert.ok(h.includes('Not touching the rest: nut'));
  assert.ok(h.includes('12.5 g') && h.includes('44.0 x 44.0 x 60.0 mm'));
  assert.ok(h.includes('data-bom-csv'));
});

test('an older saved assembly without the per-part rows falls back to the parts list', () => {
  const { assemblyHtml } = sandbox({}, true);
  const old = { ...ASM }; delete old.parts;
  const h = assemblyHtml({ partType: 'assembly', facts: { assembly: old }, notes: [] });
  assert.ok(h.includes('<th>Qty</th>') && !h.includes('<th>Position mm</th>'));
});

test('a clean assembly says so', () => {
  const { assemblyHtml } = sandbox({}, true);
  const h = assemblyHtml({ partType: 'assembly', facts: { assembly: { ...ASM, interferences: [], disconnected_groups: [] } }, notes: [] });
  assert.ok(h.includes('No overlaps or floating parts found.'));
});

test('the new download rows and the template call are wired', () => {
  for (const s of ['["GLB", "glb"', '["3MF", "3mf"', '["URDF", "urdf"', 'F.filter((r) => r[1] !== "urdf")']) assert.ok(SRC.includes(s), s);
  assert.ok(SRC.includes('`/chats/${chat.id}/templates/${id}`') && SRC.includes('`/chats/${chat.id}/parts/${part.id}/check`'));
  assert.ok(SRC.includes('api("GET", "/templates")'));
  // the merged frontend's own features are still there
  for (const s of ['pcb:kicad', 'data-req-add', 'data-req-remove', '"drl"', 'requirements: d.requirements']) assert.ok(SRC.includes(s), s);
});
