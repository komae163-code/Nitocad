// Tiny DOM helpers shared by every editor. No dependencies.
export function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v); else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c !== undefined && c !== null && c !== false) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
export const btn = (label, onclick, props = {}) => h('button', { type: 'button', class: props.primary ? 'btn primary' : 'btn', onclick, title: props.title, disabled: props.disabled }, label);
export const field = (label, input, hint) => h('label', { class: 'fld' }, h('span', { text: label }), input, hint ? h('small', { text: hint }) : null);
export const num = (value, props = {}) => h('input', { type: 'number', step: props.step || 'any', value, min: props.min, max: props.max, inputmode: 'decimal', style: props.style });
export const sel = (opts, value) => { const s = h('select', {}, opts.map((o) => h('option', { value: Array.isArray(o) ? o[0] : o, text: Array.isArray(o) ? o[1] : o }))); if (value !== undefined) s.value = value; return s; };
export const row = (...kids) => h('div', { class: 'row' }, kids);
export const fmtBytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);
export function download(name, data, type = 'application/octet-stream') {
  const url = URL.createObjectURL(data instanceof Blob ? data : new Blob([data], { type }));
  const a = h('a', { href: url, download: name }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 4000);
}
export const baseName = (n) => n.replace(/\.[^.]+$/, '');
export function num2(v, d = 3) { return Number.isFinite(v) ? String(+v.toFixed(d)) : ''; }
export const toast = (msg, kind = 'info') => {
  const host = document.getElementById('toasts') || document.body;
  const t = h('div', { class: 'toast ' + kind, role: 'status', text: msg }); host.append(t); setTimeout(() => t.remove(), kind === 'error' ? 9000 : 4000);
};
