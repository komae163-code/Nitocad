// STEP / IGES / BREP editor. The B-rep kernel runs on the server (sandboxed); this page keeps an ordered list of
// edit operations, asks the server for a preview mesh after each change, and exports STEP/BREP/STL from the original.
import { h, btn, field, num, sel, row, download, baseName, num2, toast } from './ui.js';
import { stage } from './viewerpanel.js';
import * as M from './mesh.js';

async function errText(res) {
  try { const j = await res.json(); return j.message || j.detail || j.error || `Server error ${res.status}`; } catch { return `Server error ${res.status}`; }
}

export function open(ctx) {
  const ops = [], st = stage(), info = h('div', { class: 'info' }), status = h('div', { class: 'info' }), list = h('div', { class: 'list' });
  let busy = false, seq = 0;
  const send = async (path, extra = {}) => {
    const fd = new FormData(); fd.append('file', new Blob([ctx.bytes]), ctx.name); fd.append('ops', JSON.stringify(ops));
    for (const [k, v] of Object.entries(extra)) fd.append(k, v);
    let res; try { res = await fetch(ctx.apiBase() + path, { method: 'POST', body: fd }); } catch { throw new Error('Could not reach the server. Check your connection and try again.'); }
    if (!res.ok) throw new Error(await errText(res)); return res;
  };
  const preview = async () => {
    const my = ++seq; busy = true; status.textContent = 'Working on the server…'; setBusy(true);
    try {
      const res = await send('/convert'); if (my !== seq) return;
      const stats = JSON.parse(res.headers.get('X-NitoCAD-Stats') || '{}'), p = M.parseSTL(new Uint8Array(await res.arrayBuffer()));
      st.set(p);
      info.textContent = stats.size ? `${stats.solids} solid(s) · ${stats.faces} faces · ${stats.size.map((v) => num2(v, 3)).join(' × ')} · volume ${num2(stats.volume, 2)}${stats.valid === false ? ' · geometry has errors' : ''}` : `${p.length / 9} triangles`;
      status.textContent = '';
    } catch (e) { if (my === seq) { status.textContent = ''; toast(e.message, 'error'); } }
    finally { if (my === seq) { busy = false; setBusy(false); } }
  };
  const buttons = [];
  const setBusy = (b) => buttons.forEach((x) => { x.disabled = b; });
  const add = async (op) => { if (busy) return; ops.push(op); drawList(); ctx.setDirty(true); await preview(); if (status.textContent === '' && !busy) { /* ok */ } };
  const drawList = () => { list.textContent = ''; if (!ops.length) list.append(h('div', { class: 'note', text: 'No edits yet.' })); ops.forEach((o, i) => list.append(h('div', {}, h('span', { text: `${i + 1}. ${describe(o)}` }), h('b', { text: '×', style: { cursor: 'pointer' }, title: 'Remove', onclick: async () => { if (busy) return; ops.splice(i, 1); drawList(); await preview(); } })))); };
  const describe = (o) => ({ translate: `Move ${o.x}, ${o.y}, ${o.z}`, rotate: `Rotate ${o.deg}° about ${o.axis}`, scale: `Scale ×${o.factor}`, mirror: `Mirror ${o.plane}`, center: 'Centre on origin', fillet: `Fillet all edges ${o.radius}`, chamfer: `Chamfer all edges ${o.length}` }[o.op]);
  const B = (label, fn, p) => { const b = btn(label, fn, p); buttons.push(b); return b; };
  const [mx, my, mz] = [num(0), num(0), num(0)], ax = sel(['x', 'y', 'z'], 'z'), deg = num(90), sc = num(1), fr = num(1), ch = num(1), pl = sel(['XY', 'YZ', 'XZ'], 'XY');
  const need = (v, name) => { if (!(+v.value > 0)) { toast(`${name} must be above zero.`, 'error'); return false; } return true; };
  const exportAs = async (fmt) => { if (busy) return; busy = true; setBusy(true); status.textContent = `Preparing ${fmt.toUpperCase()}…`; try { const res = await send('/api/files/cad/export', { format: fmt }); download(`${baseName(ctx.name)}-edited.${fmt}`, await res.blob()); status.textContent = ''; } catch (e) { status.textContent = ''; toast(e.message, 'error'); } finally { busy = false; setBusy(false); } };
  const panel = h('div', { class: 'panel' },
    h('h3', { text: 'Model' }), info, status,
    h('p', { class: 'note', text: 'STEP, IGES and BREP files keep their exact surfaces. Edits are recorded as operations and applied to the original on the server. There is no feature history in these formats, so edits are transforms, mirror, centre, and fillet/chamfer of all edges.' }),
    h('h3', { text: 'Move' }), row(field('X', mx), field('Y', my), field('Z', mz), B('Move', () => add({ op: 'translate', x: +mx.value || 0, y: +my.value || 0, z: +mz.value || 0 }))),
    h('h3', { text: 'Rotate about the origin' }), row(field('Axis', ax), field('Degrees', deg), B('Rotate', () => add({ op: 'rotate', axis: ax.value, deg: +deg.value || 0, about: [0, 0, 0] }))),
    h('h3', { text: 'Scale' }), row(field('Factor', sc), B('Scale', () => need(sc, 'Scale') && add({ op: 'scale', factor: +sc.value }))),
    h('h3', { text: 'Mirror' }), row(field('Plane', pl), B('Mirror', () => add({ op: 'mirror', plane: pl.value }))),
    h('h3', { text: 'Edges' }), row(field('Fillet radius', fr), B('Fillet all', () => need(fr, 'Radius') && add({ op: 'fillet', radius: +fr.value }))), row(field('Chamfer length', ch), B('Chamfer all', () => need(ch, 'Length') && add({ op: 'chamfer', length: +ch.value }))),
    row(B('Centre on origin', () => add({ op: 'center' }))),
    h('h3', { text: 'Edits' }), list,
    h('h3', { text: 'Export' }), row(B('STEP', () => exportAs('step'), { primary: true }), B('BREP', () => exportAs('brep')), B('STL', () => exportAs('stl'))));
  ctx.mount.append(h('div', { class: 'ed' }, h('div', {}, st.toolbar, st.host), panel)); drawList();
  const first = preview();
  return { destroy: st.destroy, shown: () => st.viewer && st.viewer.resize(), ready: first, _ops: ops };
}
