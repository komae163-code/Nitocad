// 3D stage + view buttons shared by the mesh and CAD editors.
import { Viewer3D } from './viewer3d.js';
import { h, btn } from './ui.js';

export function stage() {
  const canvas = h('canvas'), host = h('div', { class: 'stage' }, canvas), msg = h('p', { class: 'note', style: { padding: '12px' } });
  let viewer = null;
  try { viewer = new Viewer3D(canvas); } catch (e) { host.textContent = ''; host.append(msg); msg.textContent = e.message; }
  const toolbar = h('div', { class: 'row' }, ...['iso', 'top', 'front', 'right'].map((v) => btn(v[0].toUpperCase() + v.slice(1), () => viewer && viewer.setView(v))),
    btn('Fit', () => viewer && viewer.fit()), btn('Edges', () => { if (viewer) { viewer.showEdges = !viewer.showEdges; viewer.draw(); } }));
  return { host, toolbar, get viewer() { return viewer; }, set(p) { if (viewer) viewer.setMesh(p); }, destroy() { if (viewer) viewer.dispose(); } };
}
