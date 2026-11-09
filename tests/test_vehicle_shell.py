"""Automatic body shells: a solid body is hollowed by the expander. Pure Python, no CAD kernel."""
import json

import numpy as np
import pytest

import vehicle_overlap as VO
import vehicle_shell as SH
import vehicle_spec as VS

SIDE = [[-2100, 150], [2100, 150], [2100, 670], [1720, 780], [-1840, 780], [-2100, 710]]


def car(features=None, name="body", ptype="feature_tree", params=None, extra=None):
    feats = features or [{"id": "lower", "op": "extrude", "plane": "XZ", "symmetric": True, "distance": 1780,
                          "profile": {"type": "polygon", "points": SIDE}}]
    comp = {"id": name, "type": ptype, "assembly": "body", "position": [0, 0, 0],
            "parameters": params if params is not None else {"features": feats}}
    return {"name": "test_car", "components": [comp], **(extra or {})}


def body_features(ex, name="body"):
    return next(p for p in ex["parts"] if p["name"] == name)["parameters"]["features"]


def test_inset_rectangle_loses_one_wall_on_every_side():
    pts = [[0, 0], [100, 0], [100, 50], [0, 50]]
    inner = SH.inset_polygon(pts, 5)
    assert SH.polygon_area(inner) == pytest.approx(90 * 40)
    assert min(p[0] for p in inner) == pytest.approx(5) and max(p[1] for p in inner) == pytest.approx(45)


def test_inset_works_in_either_winding_and_on_a_slanted_edge():
    trap = [[0, 0], [100, 0], [80, 40], [20, 40]]
    a, b = SH.inset_polygon(trap, 3), SH.inset_polygon(trap[::-1], 3)
    assert SH.polygon_area(a) == pytest.approx(SH.polygon_area(b)) and SH.polygon_area(a) < SH.polygon_area(trap)
    # every inner vertex is at least the wall away from every outer edge
    assert all(p[1] >= 3 - 1e-6 for p in a)


def test_concave_and_collapsing_outlines_are_refused_not_guessed():
    assert SH.inset_polygon([[0, 0], [10, 0], [10, 10], [5, 3], [0, 10]], 1) is None          # a notch
    assert SH.inset_polygon([[0, 0], [4, 0], [4, 4], [0, 4]], 3) is None                       # too small for the wall


def test_wall_follows_body_size_within_limits():
    assert SH.default_wall(4200) == pytest.approx(2.52) and SH.default_wall(60) == 1.5 and SH.default_wall(90000) == 3.0


def test_a_solid_body_is_hollowed_and_still_validates():
    ex = VS.expand_vehicle(car())
    assert ex["ok"], ex["errors"]
    assert ex["auto_shelled"] == ["body"] and ex["shell_problems"] == []
    feats = body_features(ex)
    cut = next(f for f in feats if f["id"] == "interior_lower")
    assert cut["mode"] == "cut" and cut["distance"] == pytest.approx(1780 - 2 * 2.52) and cut["symmetric"]
    assert SH.has_hollow(feats)


def test_the_shelled_body_has_a_credible_mass():
    ex = VS.expand_vehicle(car())
    feats = body_features(ex)
    area = lambda f: SH.polygon_area(f["profile"]["points"]) * f["distance"]
    solid = area(feats[0])
    shell = solid - area(next(f for f in feats if f["id"] == "interior_lower"))
    assert solid * 7850e-9 > 10000 and shell * 7850e-9 < 1000                  # kg of steel: a block against a skin


def test_the_cavity_is_really_empty_and_the_wall_really_solid():
    part = next(p for p in VS.expand_vehicle(car())["parts"] if p["name"] == "body")
    mask = lambda pts: VO._local_mask("feature_tree", part["parameters"], np.array(pts, dtype=float))
    inside, wall_side, wall_floor, outside = mask([[0, 0, 400], [0, 889, 400], [0, 0, 151], [0, 0, 100]])
    assert not inside and wall_side and wall_floor and not outside


def test_one_sided_and_reversed_extrudes_keep_the_wall_on_both_faces():
    for rev in (False, True):
        f = {"id": "blk", "op": "extrude", "plane": "XY", "offset": 0, "distance": 100, "reverse": rev,
             "profile": {"type": "rect", "width": 200, "height": 200}}
        part = next(p for p in VS.expand_vehicle(car([f]))["parts"] if p["name"] == "body")
        z0, z1 = (-100, 0) if rev else (0, 100)
        m = lambda z: bool(VO._local_mask("feature_tree", part["parameters"], np.array([[0.0, 0.0, z]]))[0])
        assert m(z0 + 0.5) and m(z1 - 0.5) and not m((z0 + z1) / 2)


def test_rect_and_circle_bodies_are_hollowed_too():
    for prof in ({"type": "rect", "width": 500, "height": 300}, {"type": "circle", "diameter": 400}):
        ex = VS.expand_vehicle(car([{"id": "tub", "op": "extrude", "plane": "XY", "distance": 900, "profile": prof}]))
        assert ex["auto_shelled"] == ["body"], prof


def test_a_simple_box_body_becomes_a_hollow_tub():
    ex = VS.expand_vehicle(car(ptype="simple_box", params={"length_mm": 800, "width_mm": 400, "height_mm": 300}))
    prm = ex["parts"][0]["parameters"]
    assert ex["auto_shelled"] == ["body"] and prm["hollow"] is True and prm["wall_thickness_mm"] == 1.5


def test_the_vehicle_can_choose_the_wall():
    ex = VS.expand_vehicle(car(extra={"params": {"body_wall_mm": {"default": 4, "min": 1, "max": 6}}}))
    assert next(f for f in body_features(ex) if f["id"] == "interior_lower")["distance"] == pytest.approx(1772)


def test_a_body_that_is_already_hollow_or_a_thin_panel_is_left_alone():
    feats = [{"id": "lower", "op": "extrude", "plane": "XY", "distance": 500, "profile": {"type": "rect", "width": 900, "height": 600}},
             {"id": "interior", "op": "extrude", "plane": "XY", "offset": 5, "distance": 490, "mode": "cut",
              "profile": {"type": "rect", "width": 890, "height": 590}}]
    ex = VS.expand_vehicle(car(feats))
    assert ex["auto_shelled"] == [] and len(body_features(ex)) == 2
    panel = [{"id": "p", "op": "extrude", "plane": "XY", "distance": 2.5, "profile": {"type": "rect", "width": 900, "height": 600}}]
    ex = VS.expand_vehicle(car(panel, name="body_floor"))
    assert ex["auto_shelled"] == [] and ex["shell_problems"] == []


def test_only_body_named_parts_are_touched():
    ex = VS.expand_vehicle(car(name="bracket"))
    assert ex["auto_shelled"] == [] and len(body_features(ex, "bracket")) == 1


def test_a_concave_body_is_reported_and_left_for_the_splitting_check():
    notch = [[0, 0], [1000, 0], [1000, 500], [600, 300], [0, 500]]
    ex = VS.expand_vehicle(car([{"id": "lower", "op": "extrude", "plane": "XZ", "distance": 800,
                                 "profile": {"type": "polygon", "points": notch}}]))
    assert ex["ok"] and ex["auto_shelled"] == [] and "concave" in ex["shell_problems"][0]
    assert any("auto shell" in w for w in ex["warnings"])


def test_a_revolved_body_is_reported_not_guessed():
    ex = VS.expand_vehicle(car([{"id": "hull", "op": "revolve", "plane": "XY", "axis": "x", "angle": 360,
                                 "profile": {"type": "polygon", "points": [[0, 0], [500, 0], [500, 100], [0, 100]]}}],
                               name="fuselage"))
    assert ex["auto_shelled"] == [] and "revolve" in ex["shell_problems"][0]


def test_a_second_expansion_does_not_hollow_twice():
    spec = car()
    first = body_features(VS.expand_vehicle(spec))
    again = body_features(VS.expand_vehicle(spec))
    assert first == again and len([f for f in first if f["mode"] == "cut"]) == 1
