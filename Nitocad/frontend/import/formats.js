// File type table, sniffing and safety limits for the import workspace. No dependencies.
const T = (id, label, kind, exts, extra = {}) => ({ id, label, kind, exts, ...extra });

export const FORMATS = [
  T('step', 'STEP', 'cad', ['step', 'stp'], { server: true }),
  T('iges', 'IGES', 'cad', ['iges', 'igs'], { server: true }),
  T('brep', 'BREP', 'cad', ['brep', 'brp'], { server: true }),
  T('stl', 'STL', 'mesh', ['stl']),
  T('obj', 'OBJ', 'mesh', ['obj']),
  T('pdf', 'PDF', 'pdf', ['pdf']),
  T('image', 'Image', 'image', ['png', 'jpg', 'jpeg', 'webp']),
  T('dxf', 'DXF', 'dxf', ['dxf']),
  T('svg', 'SVG', 'svg', ['svg']),
  T('kicad', 'KiCad', 'kicad', ['kicad_pcb', 'kicad_sch', 'kicad_sym', 'kicad_mod', 'kicad_pro', 'kicad_prl', 'kicad_dru', 'kicad_wks']),
  T('gerber', 'Gerber', 'gerber', ['gbr', 'ger', 'gtl', 'gbl', 'gts', 'gbs', 'gto', 'gbo', 'gtp', 'gbp', 'gko', 'gm1', 'gm2', 'gml', 'g1', 'g2', 'g3', 'g4', 'gd1', 'gg1', 'gp1', 'gpt', 'gpb', 'pho', 'art', 'drl', 'xln', 'exc']),
  T('zip', 'Gerber bundle (zip)', 'zip', ['zip']),
];

const BY_EXT = new Map();
for (const f of FORMATS) for (const e of f.exts) BY_EXT.set(e, f);

// Size caps per kind, in MB. Server-bound files are capped lower because they cost CPU.
export const LIMIT_MB = { cad: 25, mesh: 100, pdf: 60, image: 40, dxf: 50, svg: 10, kicad: 50, gerber: 30, zip: 40 };

export function accept() {
  return FORMATS.flatMap((f) => f.exts.map((e) => '.' + e)).join(',');
}

export function extOf(name) {
  const base = String(name || '').split(/[\\/]/).pop();
  const i = base.lastIndexOf('.');
  return i < 0 ? '' : base.slice(i + 1).toLowerCase();
}

const dec = (b, n = 512) => new TextDecoder('latin1').decode(b.subarray(0, Math.min(n, b.length)));
const starts = (b, sig) => sig.every((v, i) => b[i] === v);

// Returns true if the leading bytes are plausible for the format. Extension alone is never trusted.
export function sniff(format, bytes) {
  const b = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const head = dec(b, 1024);
  const text = head.replace(/\r/g, '');
  switch (format.id) {
    case 'step': return /ISO-10303-21/.test(head);
    case 'iges': { const first = text.split('\n')[0] || ''; return first.length >= 73 && first[72] === 'S'; }
    case 'brep': return /DBRep_DrawableShape|CASCADE Topology|^Open CASCADE/m.test(head);
    case 'stl': {
      if (/^\s*solid\b/i.test(head) && /facet|endsolid/i.test(dec(b, 4096))) return true;
      if (b.length < 84) return false;
      const n = new DataView(b.buffer, b.byteOffset, b.byteLength).getUint32(80, true);
      return b.length === 84 + 50 * n;
    }
    case 'obj': return /^\s*(#|v\s|vn\s|vt\s|o\s|g\s|f\s|mtllib|usemtl|s\s)/m.test(head);
    case 'pdf': return /^%PDF-/.test(head.trimStart());
    case 'image': return starts(b, [0x89, 0x50, 0x4e, 0x47]) || starts(b, [0xff, 0xd8, 0xff]) || (head.slice(0, 4) === 'RIFF' && head.slice(8, 12) === 'WEBP');
    case 'dxf': return /^\s*0\s*\n\s*SECTION/.test(text) || /AutoCAD Binary DXF/.test(head) || /^\s*999\s*\n/.test(text);
    case 'svg': return /<svg[\s>]/i.test(dec(b, 4096));
    case 'kicad': return /^\s*\((kicad_|footprint|module|symbol|fp_lib|version|generator)/.test(text) || /^\s*[{[]/.test(text) /* .kicad_pro is JSON */;
    case 'gerber': return /%FS|%MO|^G04|^M48|^%|^X-?\d|^T\d+C|^;|^M30|^METRIC|^INCH/m.test(text);
    case 'zip': return starts(b, [0x50, 0x4b, 0x03, 0x04]) || starts(b, [0x50, 0x4b, 0x05, 0x06]);
    default: return false;
  }
}

// -> { ok, format, kind } or { ok:false, error }
export function detect(name, bytes, size = bytes ? bytes.length : 0) {
  const ext = extOf(name);
  const format = BY_EXT.get(ext);
  if (!format) return { ok: false, error: `.${ext || '?'} is not a supported file type.` };
  if (size > LIMIT_MB[format.kind] * 1024 * 1024) return { ok: false, error: `${format.label} files can be at most ${LIMIT_MB[format.kind]} MB here.` };
  if (size === 0) return { ok: false, error: 'This file is empty.' };
  if (bytes && !sniff(format, bytes)) return { ok: false, error: `This does not look like a real ${format.label} file (the contents do not match the .${ext} extension).` };
  return { ok: true, format, kind: format.kind, ext };
}

// Drill files share extensions with nothing else here, but Excellon vs Gerber matters to the parser.
export function isExcellon(text) {
  return /^\s*M48/m.test(text) || /^\s*(METRIC|INCH)[,\s]/m.test(text) && /^T\d+C/m.test(text);
}
