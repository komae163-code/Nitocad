import test from 'node:test';
import assert from 'node:assert/strict';
import { sanitizeSVG, parseXML, SvgError } from '../../frontend/import/svgsafe.js';

const wrap = (inner, attrs = '') => `<svg xmlns="http://www.w3.org/2000/svg" ${attrs}>${inner}</svg>`;
const clean = (inner, attrs) => sanitizeSVG(wrap(inner, attrs));

test('keeps ordinary shapes and their geometry', () => {
  const r = clean('<g id="a"><path d="M0 0L10 10" stroke="red" fill="none"/><rect x="1" y="2" width="3" height="4"/><circle cx="5" cy="5" r="2"/><text x="1" y="1">Hi &amp; bye</text></g>', 'viewBox="0 0 100 100"');
  assert.match(r.svg, /<path d="M0 0L10 10" stroke="red" fill="none"\/>/);
  assert.match(r.svg, /viewBox="0 0 100 100"/);
  assert.match(r.svg, /<text x="1" y="1">Hi &amp; bye<\/text>/);
  assert.deepEqual(r.removed, []);
});
test('removes scripts, event handlers and foreignObject', () => {
  const r = clean('<script>alert(1)</script><rect width="1" height="1" onclick="alert(1)" onload="x()"/><foreignObject><div>hi</div></foreignObject>');
  assert.ok(!/script|onclick|onload|foreignObject|alert/i.test(r.svg), r.svg);
  assert.ok(r.removed.length >= 3);
});
test('rejects DOCTYPE and entity definitions (entity bombs / XXE)', () => {
  assert.throws(() => sanitizeSVG('<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>'), /DOCTYPE|entity/i);
});
test('unknown entity references are dropped, not expanded', () => {
  assert.ok(!clean('<text>x&foo;y</text>').svg.includes('&foo;'));
});
test('drops links that are external or scriptable', () => {
  const r = clean('<use href="http://evil.example/x.svg#a"/><use xlink:href="javascript:alert(1)"/><image href="https://evil.example/p.png" width="1" height="1"/><a href="javascript:alert(1)"><rect width="1" height="1"/></a>');
  assert.ok(!/evil|javascript|<a /i.test(r.svg), r.svg);
});
test('keeps internal #id links and inline raster images', () => {
  const r = clean('<defs><path id="p" d="M0 0"/></defs><use href="#p"/><image href="data:image/png;base64,iVBORw0KGgo=" width="2" height="2"/>');
  assert.match(r.svg, /<use href="#p"\/>/);
  assert.match(r.svg, /<image href="data:image\/png;base64,iVBORw0KGgo="/);
});
test('refuses data: HTML/SVG payloads in images', () => {
  assert.ok(!/data:text/.test(clean('<image href="data:text/html;base64,PHNjcmlwdD4=" width="1" height="1"/>').svg));
});
test('style attribute is filtered property by property', () => {
  const r = clean('<rect width="1" height="1" style="fill:red;background:url(http://evil.example/x);stroke:url(#g);behavior:url(x.htc)"/>');
  assert.match(r.svg, /style="fill:red;stroke:url\(#g\)"/);
  assert.ok(!/evil|behavior|background/.test(r.svg));
});
test('external url() in presentation attributes is dropped; local url(#id) is kept', () => {
  const r = clean('<rect width="1" height="1" fill="url(http://evil.example/x)" stroke="url(#grad)"/>');
  assert.ok(!r.svg.includes('evil'));
  assert.match(r.svg, /stroke="url\(#grad\)"/);
});
test('removes style elements, animation and unknown attributes', () => {
  const r = clean('<style>@import url(http://evil.example)</style><animate attributeName="href" to="javascript:x"/><set/><rect width="1" height="1" data-x="1" xlink:foo="1"/>');
  assert.ok(!/style|animate|<set|data-x|evil/.test(r.svg), r.svg);
});
test('root must be svg and markup must be well formed', () => {
  assert.throws(() => sanitizeSVG('<html><body/></html>'), SvgError);
  assert.throws(() => sanitizeSVG('<svg><g></svg>'), SvgError);
  assert.throws(() => sanitizeSVG('<svg xmlns="http://www.w3.org/2000/svg"/><svg/>'), SvgError);
  assert.throws(() => sanitizeSVG(''), SvgError);
});
test('adds the SVG namespace when missing', () => assert.match(sanitizeSVG('<svg><rect width="1" height="1"/></svg>').svg, /xmlns="http:\/\/www.w3.org\/2000\/svg"/));
test('mixed-case gradient elements keep their canonical names', () => assert.match(clean('<defs><linearGradient id="g"><stop offset="0" stop-color="red"/></linearGradient></defs>').svg, /<linearGradient id="g">/));
test('comments, XML declarations and CDATA are handled', () => {
  const r = sanitizeSVG('<?xml version="1.0"?><!-- c --><svg xmlns="http://www.w3.org/2000/svg"><text><![CDATA[a < b]]></text></svg>');
  assert.match(r.svg, /a &lt; b/);
});
test('deep nesting is refused', () => assert.throws(() => sanitizeSVG(wrap('<g>'.repeat(250) + '</g>'.repeat(250))), /deeply/));
test('attribute values with quotes are re-escaped', () => assert.match(clean('<text font-family="a&quot;b">x</text>').svg, /font-family="a&quot;b"/));
test('parser reads self-closing and single-quoted attributes', () => {
  const n = parseXML("<svg a='1'><g b=\"2\"/></svg>");
  assert.equal(n.attrs.a, '1'); assert.equal(n.children[0].attrs.b, '2');
});
