// Minimal ZIP reader/writer with no dependencies. Reads stored + deflate entries using the browser's built-in
// DecompressionStream. Guards: entry-count, size and expansion limits (zip bombs), CRC check, no encryption/ZIP64.
export class ZipError extends Error {}
const MAX_ENTRIES = 300, MAX_ENTRY = 100 * 1024 * 1024, MAX_TOTAL = 250 * 1024 * 1024;

let TABLE = null;
export function crc32(b) {
  if (!TABLE) { TABLE = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; TABLE[n] = c >>> 0; } }
  let c = 0xffffffff; for (let i = 0; i < b.length; i++) c = TABLE[(c ^ b[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

async function inflate(comp, limit) {
  const rd = new Blob([comp]).stream().pipeThrough(new DecompressionStream('deflate-raw')).getReader();
  const parts = []; let total = 0;
  for (;;) {
    const { done, value } = await rd.read(); if (done) break;
    total += value.length;
    if (total > limit) { await rd.cancel(); throw new ZipError('A file inside this zip expands to more than its declared size (possible zip bomb).'); }
    parts.push(value);
  }
  const out = new Uint8Array(total); let o = 0; for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

export async function readZip(input) {
  const b = input instanceof Uint8Array ? input : new Uint8Array(input);
  const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
  let e = -1;
  for (let i = b.length - 22; i >= Math.max(0, b.length - 65557); i--) if (dv.getUint32(i, true) === 0x06054b50) { e = i; break; }
  if (e < 0) throw new ZipError('This is not a valid zip file (no end-of-archive record).');
  const count = dv.getUint16(e + 10, true), cdOff = dv.getUint32(e + 16, true);
  if (count === 0xffff || cdOff === 0xffffffff) throw new ZipError('ZIP64 archives are not supported.');
  if (count > MAX_ENTRIES) throw new ZipError(`This zip has more than ${MAX_ENTRIES} files.`);
  const out = []; let p = cdOff, total = 0;
  for (let n = 0; n < count; n++) {
    if (p + 46 > b.length || dv.getUint32(p, true) !== 0x02014b50) throw new ZipError('The zip directory is damaged.');
    const flags = dv.getUint16(p + 8, true), method = dv.getUint16(p + 10, true), crc = dv.getUint32(p + 16, true);
    const csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true), nl = dv.getUint16(p + 28, true), el = dv.getUint16(p + 30, true), cl = dv.getUint16(p + 32, true), lo = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(b.subarray(p + 46, p + 46 + nl)); p += 46 + nl + el + cl;
    if (name.endsWith('/')) continue;
    if (flags & 1) throw new ZipError(`"${name}" is encrypted, which is not supported.`);
    if (usize > MAX_ENTRY) throw new ZipError(`"${name}" is too large.`);
    total += usize; if (total > MAX_TOTAL) throw new ZipError('The zip expands to too much data.');
    if (lo + 30 > b.length || dv.getUint32(lo, true) !== 0x04034b50) throw new ZipError('The zip is damaged (bad local header).');
    const start = lo + 30 + dv.getUint16(lo + 26, true) + dv.getUint16(lo + 28, true);
    if (start + csize > b.length) throw new ZipError('The zip is cut off.');
    const raw = b.subarray(start, start + csize);
    let data;
    if (method === 0) data = raw.slice();
    else if (method === 8) { if (typeof DecompressionStream === 'undefined') throw new ZipError('This browser cannot unzip files. Please update it.'); data = await inflate(raw, usize); }
    else throw new ZipError(`"${name}" uses an unsupported compression method (${method}).`);
    if (data.length !== usize) throw new ZipError(`"${name}" did not unpack to its declared size.`);
    if (crc32(data) !== crc) throw new ZipError(`"${name}" is corrupted (checksum mismatch).`);
    out.push({ name: name.replace(/\\/g, '/').split('/').pop() || name, path: name, data });
  }
  return out;
}

export function writeZip(entries) {
  const enc = new TextEncoder(), chunks = [], central = []; let off = 0;
  for (const { name, data } of entries) {
    const nb = enc.encode(name), crc = crc32(data), h = new DataView(new ArrayBuffer(30));
    h.setUint32(0, 0x04034b50, true); h.setUint16(4, 20, true); h.setUint16(6, 0x0800, true); h.setUint16(8, 0, true); h.setUint16(10, 0, true); h.setUint16(12, 0x21, true);
    h.setUint32(14, crc, true); h.setUint32(18, data.length, true); h.setUint32(22, data.length, true); h.setUint16(26, nb.length, true); h.setUint16(28, 0, true);
    chunks.push(new Uint8Array(h.buffer), nb, data);
    const c = new DataView(new ArrayBuffer(46));
    c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0x0800, true); c.setUint16(10, 0, true); c.setUint16(12, 0, true); c.setUint16(14, 0x21, true);
    c.setUint32(16, crc, true); c.setUint32(20, data.length, true); c.setUint32(24, data.length, true); c.setUint16(28, nb.length, true); c.setUint32(42, off, true);
    central.push(new Uint8Array(c.buffer), nb);
    off += 30 + nb.length + data.length;
  }
  const cdSize = central.reduce((s, x) => s + x.length, 0), end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, entries.length, true); end.setUint16(10, entries.length, true); end.setUint32(12, cdSize, true); end.setUint32(16, off, true);
  const parts = [...chunks, ...central, new Uint8Array(end.buffer)], out = new Uint8Array(parts.reduce((s, x) => s + x.length, 0)); let o = 0;
  for (const x of parts) { out.set(x, o); o += x.length; }
  return out;
}
