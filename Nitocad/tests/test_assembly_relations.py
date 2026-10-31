"""assembly_relations: an edit to the shaft carries through to the parts on it. No CAD, no network."""
import copy

import assembly_relations as ar
import studio_templates as st

ASSEMBLIES = [t.id for t in st.TEMPLATES if t.spec["part_type"] == "assembly" and t.category != "vehicles"]   # vehicles are not shaft assemblies


def _set(spec, name, **kw):
    s = copy.deepcopy(spec)
    for p in s["parameters"]["parts"]:
        if p["name"] == name:
            p["parameters"].update(kw)
    return s


def _part(spec, name):
    return next(p for p in spec["parameters"]["parts"] if p["name"] == name)


def _tpl(tid):
    return st.spec_of(st.get(tid))


def test_fit_bore_is_the_clearance_over_the_shaft():
    assert ar.fit_bore(8) == 8.2 and ar.fit_bore(10) == 10.2 and ar.fit_bore(12.5) == 12.7


def test_templates_use_fit_bore():
    spec = _tpl("asm-gear-shaft")
    assert _part(spec, "gear")["parameters"]["bore_diameter_mm"] == ar.fit_bore(8)


def test_gear_bore_follows_a_wider_shaft():
    old = _tpl("asm-gear-shaft")
    r = ar.reconcile(old, _set(old, "shaft", diameter_mm=10))
    assert _part(r.spec, "gear")["parameters"]["bore_diameter_mm"] == 10.2
    assert r.changes == ["gear bore_diameter_mm: 8.2 to 10.2 (follows shaft diameter)"] and not r.blocked


def test_every_follower_bore_follows_on_every_template():
    for tid in ASSEMBLIES:
        old = _tpl(tid)
        r = ar.reconcile(old, _set(old, "shaft", diameter_mm=12))
        for p in r.spec["parameters"]["parts"]:
            if p["name"] == "shaft":
                continue
            q = p["parameters"]
            bore = q.get("bore_diameter_mm", q.get("inner_diameter_mm"))
            assert bore == 12.2, (tid, p["name"], bore)


def test_clearance_is_kept_not_reset():
    old = _tpl("asm-gear-shaft")
    old = _set(old, "gear", bore_diameter_mm=8.3)             # a looser fit chosen by the person
    r = ar.reconcile(old, _set(old, "shaft", diameter_mm=10))
    assert _part(r.spec, "gear")["parameters"]["bore_diameter_mm"] == 10.3


def test_end_part_keeps_its_distance_from_the_end():
    old = _tpl("asm-shaft-bearings")                          # shaft 60, bearings at z=5 and z=48
    r = ar.reconcile(old, _set(old, "shaft", length_mm=80))
    assert _part(r.spec, "bearing_left")["position"] == [0.0, 0.0, 5.0]
    assert _part(r.spec, "bearing_right")["position"] == [0.0, 0.0, 68]
    r = ar.reconcile(old, _set(old, "shaft", length_mm=50))
    assert _part(r.spec, "bearing_right")["position"][2] == 38


def test_middle_part_keeps_its_fractional_place():
    old = _tpl("asm-gear-shaft")                              # gear centre at 25 of 60
    r = ar.reconcile(old, _set(old, "shaft", length_mm=120))
    z = _part(r.spec, "gear")["position"][2]
    assert abs((z + 5) / 120 - 25 / 60) < 1e-3


def test_x_and_y_are_left_alone():
    old = _tpl("asm-bearing-shaft")
    r = ar.reconcile(old, _set(old, "shaft", length_mm=90))
    pos = _part(r.spec, "bearing")["position"]
    assert pos[0] == 0 and pos[1] == 0


def test_every_template_stays_valid_after_any_shaft_edit():
    """The same rules test_studio_templates holds the starting specs to: running fit, on the shaft, no overlap."""
    for tid in ASSEMBLIES:
        for kw in ({"diameter_mm": 10}, {"diameter_mm": 12}, {"length_mm": 80}, {"length_mm": 120}, {"length_mm": 70, "diameter_mm": 9}):
            old = _tpl(tid)
            r = ar.reconcile(old, _set(old, "shaft", **kw))
            parts = r.spec["parameters"]["parts"]
            shaft = next(p for p in parts if p["part_type"] == "shaft")
            d, length = shaft["parameters"]["diameter_mm"], shaft["parameters"]["length_mm"]
            spans = []
            for p in parts:
                if p is shaft:
                    continue
                q = p["parameters"]
                bore = q.get("bore_diameter_mm", q.get("inner_diameter_mm"))
                h = q.get("thickness_mm", q.get("width_mm"))
                z = p["position"][2]
                assert 0 < bore - d <= 0.5, (tid, kw, p["name"])
                assert 0 <= z and z + h <= length + 1e-9, (tid, kw, p["name"])
                spans.append((z, z + h))
            spans.sort()
            for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
                assert a1 <= b0 + 1e-9, (tid, kw)


def test_a_value_the_edit_changed_itself_is_left_alone():
    old = _tpl("asm-gear-shaft")
    new = _set(_set(old, "shaft", diameter_mm=10), "gear", bore_diameter_mm=15)
    r = ar.reconcile(old, new)
    assert _part(r.spec, "gear")["parameters"]["bore_diameter_mm"] == 15 and not r.changes
    new = _set(old, "shaft", length_mm=80)
    _part(new, "gear")["position"] = [0.0, 0.0, 30.0]
    r = ar.reconcile(old, new)
    assert _part(r.spec, "gear")["position"] == [0.0, 0.0, 30.0]


def test_a_bore_that_cannot_hold_the_new_shaft_is_left_and_explained():
    old = _tpl("asm-bearing-shaft")                           # 608 bearing, 22 mm outside
    r = ar.reconcile(old, _set(old, "shaft", diameter_mm=20))
    assert _part(r.spec, "bearing")["parameters"]["inner_diameter_mm"] == 8.2
    assert r.blocked and "bearing" in r.blocked[0] and "wall" in r.blocked[0]


def test_a_shaft_too_short_for_its_parts_moves_nothing():
    old = _tpl("asm-shaft-bearings")
    r = ar.reconcile(old, _set(old, "shaft", length_mm=15))
    assert _part(r.spec, "bearing_right")["position"] == _part(old, "bearing_right")["position"]
    assert r.blocked and not r.changes


def test_loose_parts_are_not_treated_as_fitted():
    old = _tpl("asm-gear-shaft")
    old = _set(old, "gear", bore_diameter_mm=14)              # not a running fit on an 8 mm shaft
    r = ar.reconcile(old, _set(old, "shaft", diameter_mm=10))
    assert _part(r.spec, "gear")["parameters"]["bore_diameter_mm"] == 14 and not r.changes


def test_no_op_cases():
    old = _tpl("asm-gear-shaft")
    assert not ar.reconcile(old, copy.deepcopy(old)).changes                          # nothing changed
    assert not ar.reconcile(old, _set(old, "gear", teeth=30)).changes                 # shaft untouched
    assert not ar.reconcile(None, old).changes and not ar.reconcile(old, st.spec_of(st.get("bearing-608"))).changes
    added = _set(old, "shaft", diameter_mm=10)
    added["parameters"]["parts"].append(copy.deepcopy(_part(added, "gear")) | {"name": "gear2"})
    assert not ar.reconcile(old, added).changes                                       # parts added: structural edit
    mated = _set(old, "shaft", diameter_mm=10)
    mated["parameters"]["layout"] = "mated"
    assert not ar.reconcile(old, mated).changes                                       # the mate engine places these
    two = _set(old, "shaft", diameter_mm=10)
    old2 = copy.deepcopy(old)
    old2["parameters"]["parts"].append(copy.deepcopy(_part(old, "shaft")) | {"name": "shaft2"})
    two["parameters"]["parts"].append(copy.deepcopy(_part(old, "shaft")) | {"name": "shaft2"})
    assert not ar.reconcile(old2, two).changes                                        # two shafts: ambiguous


def test_row_layout_follows_the_bore_but_has_no_positions_to_move():
    old = _tpl("asm-gear-shaft")
    old["parameters"]["layout"] = "row"
    for p in old["parameters"]["parts"]:
        p.pop("position", None)
    r = ar.reconcile(old, _set(old, "shaft", diameter_mm=10, length_mm=90))
    assert _part(r.spec, "gear")["parameters"]["bore_diameter_mm"] == 10.2
    assert "position" not in _part(r.spec, "gear")


def test_inputs_are_not_changed():
    old = _tpl("asm-shaft-bearings")
    new = _set(old, "shaft", diameter_mm=10, length_mm=80)
    snap_old, snap_new = copy.deepcopy(old), copy.deepcopy(new)
    ar.reconcile(old, new)
    assert old == snap_old and new == snap_new
