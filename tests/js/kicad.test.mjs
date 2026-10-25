import test from 'node:test';
import assert from 'node:assert/strict';
import * as K from '../../frontend/import/kicad.js';

const PCB = `(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "GND")
  (gr_line (start 0 0) (end 50 0) (stroke (width 0.1) (type default)) (layer "Edge.Cuts"))
  (gr_circle (center 25 25) (end 30 25) (layer "Edge.Cuts") (width 0.1))
  (footprint "Resistor_SMD:R_0603" (layer "F.Cu") (at 10 20 90)
    (property "Reference" "R1" (at 0 -1.4 90) (layer "F.SilkS"))
    (property "Value" "10k" (at 0 1.4 90) (layer "F.Fab"))
    (fp_line (start -0.8 -0.4) (end 0.8 -0.4) (layer "F.SilkS") (width 0.12))
    (pad "1" smd roundrect (at -0.8 0 90) (size 0.9 0.95) (layers "F.Cu" "F.Paste" "F.Mask"))
    (pad "2" smd roundrect (at 0.8 0 90) (size 0.9 0.95) (layers "F.Cu" "F.Paste" "F.Mask")))
  (footprint "Connector:Pin" (layer "B.Cu") (at 30 10)
    (property "Reference" "J1" (at 0 0) (layer "B.SilkS"))
    (pad "1" thru_hole circle (at 2 0) (size 1.6 1.6) (drill 0.9) (layers "*.Cu")))
  (segment (start 10 19.2) (end 20 19.2) (width 0.25) (layer "F.Cu") (net 1))
  (via (at 20 19.2) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))
  (zone (net 1) (layer "F.Cu") (polygon (pts (xy 0 0) (xy 5 0) (xy 5 5)))))`;

test('parse/serialize round trip keeps the whole tree', () => {
  const a = K.parse(PCB), b = K.parse(K.serialize(a));
  assert.deepEqual(b, a);
});
test('strings with quotes, backslashes and newlines survive', () => {
  const root = K.parse('(x (text "a \\"q\\" \\\\ b\\nc"))');
  assert.equal(K.val(K.child(root, 'text').args[0]), 'a "q" \\ b\nc');
  assert.deepEqual(K.parse(K.serialize(root)), root);
});
test('malformed files give readable errors', () => {
  assert.throws(() => K.parse('hello'), /does not look like a KiCad file/);
  assert.throws(() => K.parse('(a (b)'), /never closed|Unbalanced/);
  assert.throws(() => K.parse('(a "oops)'), /Unterminated/);
  assert.throws(() => K.parse('(a) (b)'), /Unexpected content/);
  assert.throws(() => K.parse('('.repeat(300)), /nested too deeply/);
});
test('pcb model reads segments, vias, footprints, nets and graphics', () => {
  const m = K.pcbModel(K.parse(PCB));
  assert.equal(m.nets.get(1), 'GND');
  assert.equal(m.segments.length, 1); assert.deepEqual(m.segments[0].a, [10, 19.2]); assert.equal(m.segments[0].net, 1);
  assert.deepEqual(m.vias[0].p, [20, 19.2]);
  assert.equal(m.footprints.length, 2);
  const r1 = m.footprints[0]; assert.equal(r1.ref, 'R1'); assert.equal(r1.value, '10k'); assert.equal(r1.rot, 90); assert.equal(r1.pads.length, 2);
  assert.equal(m.graphics.length, 2);
  assert.equal(m.graphics.find((g) => g.k === 'circle').r, 5);
  assert.equal(m.zones[0].pts.length, 3);
});
test('footprint rotation: a pad 0.8 to the right of a 90 degree part sits 0.8 above its centre (Y is down)', () => {
  const fp = K.pcbModel(K.parse(PCB)).footprints[0];
  const w = K.fpToWorld(fp, 0.8, 0);
  assert.ok(Math.abs(w[0] - 10) < 1e-9 && Math.abs(w[1] - 19.2) < 1e-9, JSON.stringify(w));
});
test('bottom-side parts are mirrored in x', () => {
  const j = K.pcbModel(K.parse(PCB)).footprints[1];
  assert.deepEqual(K.fpToWorld(j, 2, 0).map((v) => +v.toFixed(6)), [28, 10]);
});
test('KiCad 5 arcs (centre, start, angle) become three-point arcs', () => {
  const m = K.pcbModel(K.parse('(kicad_pcb (version 4) (gr_arc (start 0 0) (end 10 0) (angle 90) (layer "Edge.Cuts") (width 0.1)))'));
  const g = m.graphics[0]; assert.equal(g.k, 'arc3');
  assert.ok(Math.abs(g.b[0]) < 1e-9 && Math.abs(g.b[1] - 10) < 1e-9);
});
test('move footprint updates the file text and the model', () => {
  const root = K.parse(PCB), fp = K.pcbModel(root).footprints[0];
  K.moveFootprint(fp, 5, -2.5);
  const again = K.pcbModel(K.parse(K.serialize(root))).footprints[0];
  assert.deepEqual([again.x, again.y, again.rot], [15, 17.5, 90]);
  assert.deepEqual([again.pads[0].lx, again.pads[0].ly], [-0.8, 0], 'pads are relative, so they must not change');
});
test('rotate footprint changes its angle and the absolute pad angles', () => {
  const root = K.parse(PCB), fp = K.pcbModel(root).footprints[0];
  K.rotateFootprint(fp, 90);
  const a = K.pcbModel(K.parse(K.serialize(root))).footprints[0];
  assert.equal(a.rot, 180); assert.equal(a.pads[0].angle, 180);
  K.rotateFootprint(a, -270);
  assert.equal(a.rot, 270);
});
test('rotating a footprint with no angle adds one', () => {
  const root = K.parse(PCB), fp = K.pcbModel(root).footprints[1];
  K.rotateFootprint(fp, 45);
  assert.equal(K.pcbModel(K.parse(K.serialize(root))).footprints[1].rot, 45);
});
test('move segment and via', () => {
  const root = K.parse(PCB), m = K.pcbModel(root);
  K.moveSegment(m.segments[0], 1, 1); K.moveVia(m.vias[0], 1, 1);
  const r = K.pcbModel(K.parse(K.serialize(root)));
  assert.deepEqual(r.segments[0].a, [11, 20.2]); assert.deepEqual(r.segments[0].b, [21, 20.2]); assert.deepEqual(r.vias[0].p, [21, 20.2]);
});
test('delete removes the node from the file', () => {
  const root = K.parse(PCB), m = K.pcbModel(root);
  assert.ok(K.remove(root, m.segments[0].node)); assert.ok(K.remove(root, m.footprints[1].node));
  const r = K.pcbModel(K.parse(K.serialize(root)));
  assert.equal(r.segments.length, 0); assert.equal(r.footprints.length, 1);
  assert.equal(K.remove(root, m.segments[0].node), false, 'already removed');
});
test('edit reference and value text', () => {
  const root = K.parse(PCB), fp = K.pcbModel(root).footprints[0];
  K.setFootprintText(fp, 'Reference', 'R42'); K.setFootprintText(fp, 'Value', '4k7');
  const a = K.pcbModel(K.parse(K.serialize(root))).footprints[0];
  assert.equal(a.ref, 'R42'); assert.equal(a.value, '4k7');
});
test('older fp_text fields are edited too', () => {
  const root = K.parse('(kicad_pcb (module "X" (layer F.Cu) (at 1 2) (fp_text reference "U1" (at 0 0) (layer F.SilkS)) (fp_text value "IC" (at 0 1) (layer F.Fab))))');
  const fp = K.pcbModel(root).footprints[0]; assert.equal(fp.ref, 'U1');
  K.setFootprintText(fp, 'Reference', 'U9');
  assert.equal(K.pcbModel(K.parse(K.serialize(root))).footprints[0].ref, 'U9');
});
test('setLeaf keeps strings quoted and atoms bare', () => {
  const root = K.parse('(a "text" 12)');
  K.setLeaf(root, 0, 'new "q"'); K.setLeaf(root, 1, '34');
  assert.equal(K.serialize(root), '(a "new \\"q\\"" 34)');
  assert.throws(() => K.setLeaf(K.parse('(a (b))'), 0, 'x'), /not a value/);
});
test('other KiCad files (schematic, footprint) parse and round trip', () => {
  const sch = '(kicad_sch (version 20230121) (symbol (lib_id "Device:R") (at 100 50 0) (property "Reference" "R1" (at 0 0 0))) (wire (pts (xy 0 0) (xy 10 0))))';
  assert.deepEqual(K.parse(K.serialize(K.parse(sch))), K.parse(sch));
  assert.throws(() => K.pcbModel(K.parse(sch)), /not a PCB/);
});
