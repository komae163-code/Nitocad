// Run from the project root:  node tests/studio_codegen_part_check.mjs
// Exercises the code-part functions in frontend/studio.js with stand-ins for the page. No browser, no network.
import fs from "node:fs";
const src = fs.readFileSync("frontend/studio.js", "utf8");
const a = src.indexOf("/* ============================================================ code parts");
const b = src.indexOf("/* ============================================================ manufacturability check");
const block = src.slice(a, b);

const calls = { fetch: [], viewer: 0, blobs: [] };
const dom = {}; const listeners = {};
const mk = (id) => (dom[id] = dom[id] || { id, innerHTML: "", textContent: "", disabled: false, dataset: {}, addEventListener(t, f) { listeners[t] = f; } });
const $ = (id) => (id === "panel" ? mk("panel") : dom[id] || null);
const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const S = { activePartId: "p1" };
const part = { id: "p1", title: "Lightning bolt bracket", partType: "codegen", jobId: null, spec: { parameters: { codegen: { id: "cg1", values: { width: 40 } } } }, facts: { size_mm: [40, 30, 20], mass_g: null, mass_note: "set a material to get mass" } };
const chat = () => ({ parts: [part] }); const partOf = (c, id) => c.parts.find((p) => p.id === id);
const stlCache = new Map(); stlCache.set("p1:", "stale");
const Auth = { authHeader: () => ({ Authorization: "Bearer t" }) };
const BASE = "http://srv";
const blobDownload = async (blob, name) => calls.blobs.push([name, blob.size]);
const viewerShow = () => { calls.viewer++; };
globalThis.fetch = async (url, opt = {}) => {
  calls.fetch.push([opt.method || "GET", url, opt.body || ""]);
  if (url.endsWith("/rebuild")) return { ok: true, json: async () => ({ id: "cg1", mesh_url: "/codegen/mesh/tok", params: [{ name: "width", label: "Width", unit: "mm", min: 10, max: 100, default: 40 }, { name: "holes", label: "Holes", unit: "count", min: 1, max: 8, default: 2, int: true }], values: { width: 40 } }) };
  if (url.includes("/codegen/mesh/")) return { ok: true, arrayBuffer: async () => new Uint8Array([1, 2, 3, 4]).buffer };
  if (url.endsWith("/export")) return { ok: true, blob: async () => ({ size: 99 }) };
  return { ok: false, status: 500, json: async () => ({ detail: "nope" }) };
};
const fn = new Function("$", "esc", "S", "chat", "partOf", "stlCache", "Auth", "BASE", "blobDownload", "viewerShow", "setTimeout", "clearTimeout",
  block + "\nreturn { isCodegen, codegenRef, codegenStl, codegenMount, codegenDownload, codegenChips, codegenSpecsHtml, codegenFilesHtml };");
const timers = [];
const api = fn($, esc, S, chat, partOf, stlCache, Auth, BASE, blobDownload, viewerShow, (f) => { timers.push(f); return timers.length; }, () => { timers.length = 0; });

const ok = (c, m) => { if (!c) { console.log("FAIL:", m); process.exitCode = 1; } else console.log("ok  :", m); };
ok(api.isCodegen(part) && !api.isCodegen({ partType: "flat_plate", spec: {} }), "isCodegen only for code parts");
ok(api.codegenChips(part).includes("written as code") && api.codegenChips(part).includes("40 x 30 x 20 mm"), "card chips show size");
ok(api.codegenSpecsHtml(part).includes("set a material to get mass") && api.codegenSpecsHtml(part).includes("cgSliders"), "specs html has facts and slider box");
ok(api.codegenFilesHtml().includes('data-cg-dl="package"') && !api.codegenFilesHtml().includes("Generate"), "files html offers STEP/STL/ZIP with no Generate");
mk("cgSliders");
const buf1 = await api.codegenStl(part), buf2 = await api.codegenStl(part);
ok(buf1.byteLength === 4 && buf2.byteLength === 4 && buf1 !== buf2, "mesh fetched, copies are independent");
ok(calls.fetch.filter((c) => c[1].endsWith("/rebuild")).length === 1 && calls.fetch.filter((c) => c[1].includes("/codegen/mesh/")).length === 1, "one rebuild + one mesh download for repeated views");
await api.codegenMount(part);
ok(dom.cgSliders.innerHTML.includes('data-cg-p="width"') && dom.cgSliders.innerHTML.includes('data-int="1"') && dom.cgSliders.innerHTML.includes('step="1"'), "sliders built, integer slider steps by 1");
ok(calls.fetch.filter((c) => c[1].endsWith("/rebuild")).length === 1, "mounting sliders reused the same rebuild");
// a slider moves
mk("cgo_width");
listeners.input({ target: { closest: () => ({ dataset: { cgP: "width", int: "0", unit: "mm" }, value: "55" }) } });
ok(part.cgValues.width === 55 && dom.cgo_width.textContent === "55 mm", "slider updates the part values and its label");
ok(stlCache.get("p1:") === "stale" && timers.length === 1, "viewer refresh is debounced");
timers[0]();
ok(!stlCache.has("p1:") && calls.viewer === 1, "after the pause the stale cache is dropped and the viewer reloads");
const g = api.codegenRef(part);
ok(g.values.width === 55, "new values are used for the next rebuild and downloads");
await api.codegenStl(part);
ok(calls.fetch.filter((c) => c[1].endsWith("/rebuild")).length === 2 && JSON.parse(calls.fetch.filter((c) => c[1].endsWith("/rebuild")).pop()[2]).params.width === 55, "changed values trigger exactly one new rebuild with them");
// download
const btn = { textContent: "Download", disabled: false };
mk("cgDlErr");
await api.codegenDownload(btn, part, "package");
const exp = calls.fetch.find((c) => c[1].endsWith("/export"));
ok(JSON.parse(exp[2]).fmt === "package" && JSON.parse(exp[2]).params.width === 55 && calls.blobs[0][0] === "Lightning_bolt_bracket.zip", "export uses current values, saved as .zip");
ok(btn.textContent === "Download" && !btn.disabled, "button restored after download");
// failure path
globalThis.fetch = async () => ({ ok: false, status: 402, json: async () => ({ detail: "Not allowed." }) });
await api.codegenDownload(btn, part, "step");
ok(dom.cgDlErr.textContent === "Not allowed.", "server message is shown on a failed download");
