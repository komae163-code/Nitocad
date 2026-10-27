// STL / OBJ editor (browser only). Edits are applied to the triangle list; everything can be undone.
import * as M from './mesh.js';
import { h, btn, field, num, sel, row, download, baseName, num2, fmtBytes, toast } from './ui.js';
import { stage } from './viewerpanel.js';

export function open(ctx) {
  let pos = ctx.ext === 'stl' ? M.parseSTL(ctx.bytes) : M.parseOBJ(new TextDecoder().decode(ctx.bytes));
  const history = [], st = stage(), info = h('div', { class: 'info' }), warn = h('div', { class: 'info warn' });
  const refresh = () => {
    st.set(pos); const b = M.bbox(pos), m = M.measure(pos);
    info.textContent = `${m.triangles.toLocaleString()} triangles · ${b.size.map((v) => num2(v, 3)).join(' × ')} · volume ${num2(Math.abs(m.volume), 2)} · area ${num2(m.area, 2)} (units as in the file)`;
    warn.textContent = m.volume < 0 ? 'The surface is inside-out (negative volume). Use "Flip normals" to fix it.' : '';
  };
  const apply = (fn, label) => { history.push(pos); if (history.length > 10) history.shift(); try { pos = fn(pos); } catch (e) { history.pop(); return toast(e.message, 'error'); } ctx.setDirty(true); refresh(); undoBtn.disabled = false; if (label) toast(label); };
  const undoBtn = btn('Undo', () => { if (history.length) { pos = history.pop(); refresh(); undoBtn.disabled = !history.length; } }, { disabled: true });
  const about = sel([['center', 'its centre'], ['origin', 'the origin']], 'center');
  const around = (m) => (p) => { if (about.value === 'origin') return M.transform(p, m); const c = M.bbox(p).center; return M.transform(p, M.mul(M.translation(c[0], c[1], c[2]), M.mul(m, M.translation(-c[0], -c[1], -c[2])))); };
  const [mx, my, mz] = [num(0), num(0), num(0)], rotAxis = sel(['x', 'y', 'z'], 'z'), rotDeg = num(90), sU = num(1), [sx, sy, sz] = [num(1), num(1), num(1)];
  const panel = h('div', { class: 'panel' },
    h('h3', { text: 'File' }), info, warn, h('div', { class: 'info', text: `${ctx.name} · ${fmtBytes(ctx.bytes.length)}` }),
    h('h3', { text: 'Move' }), row(field('X', mx), field('Y', my), field('Z', mz), btn('Move', () => apply((p) => M.transform(p, M.translation(+mx.value || 0, +my.value || 0, +mz.value || 0))))),
    h('h3', { text: 'Rotate' }), row(field('Axis', rotAxis), field('Degrees', rotDeg), field('About', about), btn('Rotate', () => apply(around(M.rotation(rotAxis.value, +rotDeg.value || 0))))),
    h('h3', { text: 'Scale' }), row(field('Uniform', sU), btn('Scale', () => { const k = +sU.value; if (!(k > 0)) return toast('Scale must be above zero.', 'error'); apply(around(M.scaling(k))); })),
    row(field('X', sx), field('Y', sy), field('Z', sz), btn('Scale XYZ', () => { const k = [+sx.value, +sy.value, +sz.value]; if (!k.every((v) => v > 0)) return toast('Scale must be above zero.', 'error'); apply(around(M.scaling(...k))); })),
    h('h3', { text: 'Mirror' }), row(...['x', 'y', 'z'].map((a) => btn(a.toUpperCase(), () => apply((p) => M.transform(p, M.mirror(a)))))),
    h('h3', { text: 'Position' }), row(btn('Centre on origin', () => apply(M.centerOnOrigin)), btn('Drop to Z=0', () => apply(M.dropToZ0)), btn('Flip normals', () => apply(M.flipWinding))),
    h('h3', { text: 'Export' }), row(btn('STL', () => download(`${baseName(ctx.name)}-edited.stl`, M.toSTL(pos), 'model/stl'), { primary: true }), btn('OBJ', () => download(`${baseName(ctx.name)}-edited.obj`, M.toOBJ(pos), 'text/plain'))),
    row(undoBtn));
  ctx.mount.append(h('div', { class: 'ed' }, h('div', {}, st.toolbar, st.host), panel)); refresh();
  return { destroy: st.destroy, shown: () => st.viewer && st.viewer.resize(), _state: () => ({ pos }) };
}
