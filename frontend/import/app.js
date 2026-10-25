// Import & edit workspace: one upload button for every supported type. Detects each file by name AND contents,
// then loads the matching editor on demand. Most editors run entirely in the browser (nothing is uploaded).
import { detect, accept, extOf, LIMIT_MB, isExcellon, FORMATS } from './formats.js';
import { readZip, ZipError } from './zip.js';
import { h, toast, fmtBytes } from './ui.js';

const DEFAULT_API_BASE = 'https://nitocad-production-2df5.up.railway.app';
const apiBase = () => (localStorage.getItem('nitocad_api_base') || DEFAULT_API_BASE).replace(/\/$/, '');
const EDITORS = { mesh: './ed-mesh.js', cad: './ed-cad.js', dxf: './ed-dxf.js', svg: './ed-svg.js', pdf: './ed-pdf.js', image: './ed-image.js', kicad: './ed-kicad.js', gerber: './ed-gerber.js' };

const $ = (id) => document.getElementById(id);
const docs = []; let active = null, seq = 0;

$('picker').accept = accept();
$('choose').onclick = () => $('picker').click();
$('picker').onchange = (e) => { intake([...e.target.files]); e.target.value = ''; };
const drop = $('drop');
['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => intake([...e.dataTransfer.files]));
window.addEventListener('dragover', (e) => e.preventDefault()); window.addEventListener('drop', (e) => { e.preventDefault(); if (!drop.contains(e.target)) intake([...e.dataTransfer.files]); });
window.addEventListener('beforeunload', (e) => { if (docs.some((d) => d.dirty)) { e.preventDefault(); e.returnValue = ''; } });

async function readFile(f) {
  const lim = Math.max(...Object.values(LIMIT_MB)) * 1024 * 1024;
  if (f.size > lim) throw new Error(`${f.name}: this file is too large (${fmtBytes(f.size)}).`);
  return new Uint8Array(await f.arrayBuffer());
}

export async function intake(files) {
  const gerbers = [], problems = [];
  for (const f of files) {
    try {
      const bytes = await readFile(f), ext = extOf(f.name);
      if (ext === 'zip') {
        const d = detect(f.name, bytes); if (!d.ok) throw new Error(`${f.name}: ${d.error}`);
        let n = 0, skipped = 0;
        for (const e of await readZip(bytes)) { const r = detect(e.name, e.data); if (r.ok && r.kind === 'gerber') { gerbers.push({ name: e.name, bytes: e.data, ext: r.ext }); n++; } else skipped++; }
        if (!n) throw new Error(`${f.name}: no Gerber or drill files were found inside this zip.`);
        if (skipped) toast(`${f.name}: ${skipped} other file${skipped > 1 ? 's' : ''} in the zip were skipped.`);
        continue;
      }
      const d = detect(f.name, bytes);
      if (!d.ok) throw new Error(`${f.name}: ${d.error}`);
      if (d.kind === 'gerber') gerbers.push({ name: f.name, bytes, ext: d.ext }); else await openDoc({ name: f.name, kind: kindFor(d), ext: d.ext, bytes });
    } catch (e) { problems.push(e instanceof ZipError ? `${f.name}: ${e.message}` : e.message || String(e)); }
  }
  if (gerbers.length) await openDoc({ name: gerbers.length === 1 ? gerbers[0].name : `Gerber set (${gerbers.length} files)`, kind: 'gerber', layers: gerbers });
  for (const p of problems) toast(p, 'error');
}
const kindFor = (d) => (d.kind === 'cad' ? 'cad' : d.kind);

async function openDoc(spec) {
  const id = ++seq, mount = h('div', { class: 'ed-host' }), doc = { id, ...spec, mount, dirty: false, title: spec.name };
  const tab = h('div', { class: 'tab', role: 'tab' }, h('span', { text: spec.name }), h('b', { text: '×', title: 'Close', onclick: (ev) => { ev.stopPropagation(); close(doc); } }));
  tab.onclick = () => activate(doc); doc.tab = tab; docs.push(doc); $('tabs').append(tab); $('work').append(mount); activate(doc);
  mount.append(h('p', { class: 'note', text: 'Opening…' }));
  try {
    let mod;
    try { mod = await import(EDITORS[spec.kind]); }
    catch (err) { throw new Error(`the ${spec.kind.toUpperCase()} editor is not available yet in this build. The file was recognised and checked, but it cannot be opened here.`); }
    mount.textContent = '';
    const ctx = { mount, name: spec.name, ext: spec.ext, bytes: spec.bytes, layers: spec.layers, apiBase, toast,
      setDirty: (v = true) => { doc.dirty = v; tab.firstChild.textContent = (v ? '● ' : '') + doc.title; }, setTitle: (t) => { doc.title = t; tab.firstChild.textContent = (doc.dirty ? '● ' : '') + t; } };
    doc.editor = (await mod.open(ctx)) || {};
  } catch (e) {
    mount.textContent = ''; mount.append(h('p', { class: 'warn', text: `Could not open ${spec.name}: ${e.message || e}` }), h('button', { class: 'btn', onclick: () => close(doc, true) }, 'Close'));
    toast(`Could not open ${spec.name}: ${e.message || e}`, 'error');
  }
  return doc;
}
function activate(doc) { active = doc; for (const d of docs) { d.mount.style.display = d === doc ? '' : 'none'; d.tab.classList.toggle('active', d === doc); if (d === doc && d.editor && d.editor.shown) d.editor.shown(); } }
function close(doc, force) {
  if (doc.dirty && !force && !confirm(`"${doc.title}" has unsaved changes. Close it anyway?`)) return;
  if (doc.editor && doc.editor.destroy) try { doc.editor.destroy(); } catch (e) { /* ignore */ }
  doc.tab.remove(); doc.mount.remove(); docs.splice(docs.indexOf(doc), 1); if (active === doc) activate(docs[docs.length - 1]);
}

// Files handed over from the preview page (File objects cannot cross pages, so it stores bytes in IndexedDB).
async function pending() {
  try {
    const db = await new Promise((res, rej) => { const r = indexedDB.open('nitocad-handoff', 1); r.onupgradeneeded = () => r.result.createObjectStore('files'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
    const items = await new Promise((res) => { const q = db.transaction('files').objectStore('files').getAll(); q.onsuccess = () => res(q.result); q.onerror = () => res([]); });
    await new Promise((res) => { const t = db.transaction('files', 'readwrite'); t.objectStore('files').clear(); t.oncomplete = res; t.onerror = res; });
    db.close();
    if (items.length) intake(items.map((i) => new File([i.bytes], i.name)));
  } catch (e) { /* no handoff available */ }
}
pending();
window.__nitocadImport = { intake, docs };
