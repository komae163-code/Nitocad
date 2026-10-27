import test from 'node:test';
import assert from 'node:assert/strict';
import zlib from 'node:zlib';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { readZip, writeZip, crc32, ZipError } from '../../frontend/import/zip.js';

const enc = (s) => new TextEncoder().encode(s);
const dec = (b) => new TextDecoder().decode(b);

// Builds a zip with deflate entries by hand (independent of the code under test).
function deflateZip(entries, { lieAboutSize, flags = 0, method = 8 } = {}) {
  const parts = [], central = []; let off = 0;
  for (const [name, data] of entries) {
    const comp = method === 8 ? zlib.deflateRawSync(data) : data, nb = enc(name), crc = crc32(data);
    const usize = lieAboutSize ?? data.length;
    const lh = Buffer.alloc(30); lh.writeUInt32LE(0x04034b50, 0); lh.writeUInt16LE(20, 4); lh.writeUInt16LE(flags, 6); lh.writeUInt16LE(method, 8); lh.writeUInt32LE(crc, 14); lh.writeUInt32LE(comp.length, 18); lh.writeUInt32LE(usize, 22); lh.writeUInt16LE(nb.length, 26);
    const ch = Buffer.alloc(46); ch.writeUInt32LE(0x02014b50, 0); ch.writeUInt16LE(20, 4); ch.writeUInt16LE(20, 6); ch.writeUInt16LE(flags, 8); ch.writeUInt16LE(method, 10); ch.writeUInt32LE(crc, 16); ch.writeUInt32LE(comp.length, 20); ch.writeUInt32LE(usize, 24); ch.writeUInt16LE(nb.length, 28); ch.writeUInt32LE(off, 42);
    parts.push(lh, Buffer.from(nb), Buffer.from(comp)); central.push(ch, Buffer.from(nb)); off += 30 + nb.length + comp.length;
  }
  const cd = Buffer.concat(central), end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10); end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(off, 16);
  return new Uint8Array(Buffer.concat([...parts, cd, end]));
}

test('crc32 matches the standard check value', () => assert.equal(crc32(enc('123456789')), 0xcbf43926));
test('stored round trip keeps names and bytes', async () => {
  const zip = writeZip([{ name: 'a.gtl', data: enc('hello') }, { name: 'b/c.drl', data: new Uint8Array([0, 1, 2, 255]) }]);
  const r = await readZip(zip);
  assert.deepEqual(r.map((e) => e.name), ['a.gtl', 'c.drl']);
  assert.equal(dec(r[0].data), 'hello'); assert.deepEqual(Array.from(r[1].data), [0, 1, 2, 255]);
});
test('reads deflate-compressed entries made by another tool', async () => {
  const big = enc('G04 test*\n'.repeat(5000)), r = await readZip(deflateZip([['big.gbr', big], ['small.txt', enc('x')]]));
  assert.equal(r[0].data.length, big.length); assert.equal(dec(r[1].data), 'x');
});
test('python reads what we write, and we read what python writes', () => {
  let py; try { execFileSync('python3', ['-c', '1']); py = true; } catch { py = false; }
  if (!py) return;
  const dir = mkdtempSync(join(tmpdir(), 'zip-')), f = join(dir, 'ours.zip');
  writeFileSync(f, writeZip([{ name: 'top.gtl', data: enc('copper') }, { name: 'drill.drl', data: enc('M48') }]));
  const out = execFileSync('python3', ['-c', `import zipfile,sys;z=zipfile.ZipFile(sys.argv[1]);assert z.testzip() is None;print(",".join(sorted(z.namelist())),z.read("top.gtl").decode())`, f]).toString().trim();
  assert.equal(out, 'drill.drl,top.gtl copper');
  const g = join(dir, 'theirs.zip');
  execFileSync('python3', ['-c', `import zipfile,sys;z=zipfile.ZipFile(sys.argv[1],"w",zipfile.ZIP_DEFLATED);z.writestr("board.gbr","G04 x*\\n"*400);z.writestr("sub/b.drl","M48\\n")`, g]);
  return readZip(readFileSync(g)).then((r) => { assert.deepEqual(r.map((e) => e.name).sort(), ['b.drl', 'board.gbr']); assert.equal(r.find((e) => e.name === 'board.gbr').data.length, 7 * 400); });
});
test('a zip bomb (declared size far below the real size) is stopped', async () => {
  const bomb = deflateZip([['bomb.gbr', new Uint8Array(20 * 1024 * 1024)]], { lieAboutSize: 1000 });
  await assert.rejects(() => readZip(bomb), (e) => e instanceof ZipError && /expands to more than|declared size/.test(e.message));
});
test('declared sizes over the limit are refused before unpacking', async () => {
  await assert.rejects(() => readZip(deflateZip([['x.gbr', enc('a')]], { lieAboutSize: 200 * 1024 * 1024 })), /too large/);
});
test('corrupted data fails the checksum', async () => {
  const z = writeZip([{ name: 'a.gbr', data: enc('hello world') }]); z[30 + 5 + 3] ^= 0xff;
  await assert.rejects(() => readZip(z), /corrupted|checksum/);
});
test('encrypted, unsupported-method, truncated and non-zip inputs give clear errors', async () => {
  await assert.rejects(() => readZip(deflateZip([['a', enc('x')]], { flags: 1 })), /encrypted/);
  await assert.rejects(() => readZip(deflateZip([['a', enc('x')]], { method: 9 })), /unsupported compression/);
  await assert.rejects(() => readZip(new Uint8Array(100)), /not a valid zip/);
  const z = writeZip([{ name: 'a', data: enc('abc') }]); await assert.rejects(() => readZip(z.slice(0, z.length - 30)), ZipError);
});
test('too many entries are refused', async () => {
  const entries = Array.from({ length: 301 }, (_, i) => ({ name: `f${i}.gbr`, data: enc('x') }));
  await assert.rejects(() => readZip(writeZip(entries)), /more than 300/);
});
test('directories are skipped and non-ASCII names survive', async () => {
  const r = await readZip(writeZip([{ name: 'ü/é.gbr', data: enc('x') }]));
  assert.equal(r[0].name, 'é.gbr');
});
