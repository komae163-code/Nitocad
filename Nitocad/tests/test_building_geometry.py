"""Building parts built with real CadQuery geometry. Skipped where CadQuery is not installed."""
import math

import pytest

cq = pytest.importorskip("cadquery")

from building import design_building  # noqa: E402
from building.model3d import build_parts  # noqa: E402
from feature_builder import build_feature_tree  # noqa: E402

HOUSE = {"name": "Two-storey house", "width_mm": 13000, "depth_mm": 10000, "levels": [
    {"rooms": [{"type": "living"}, {"type": "dining"}, {"type": "kitchen"},
               {"type": "bedroom", "name": "Guest bedroom"}, {"type": "toilet"}, {"type": "store"}]},
    {"rooms": [{"type": "master_bedroom"}, {"type": "bedroom", "count": 2}, {"type": "bathroom", "count": 2}]}]}


@pytest.fixture(scope="module")
def built():
    model = design_building(HOUSE)["model"]
    return model, {p["name"]: build_feature_tree(p["parameters"]["features"]) for p in build_parts(model)}


def test_every_part_is_one_valid_solid(built):
    _, solids = built
    for name, body in solids.items():
        assert len(body.solids().vals()) == 1, name
        assert body.val().isValid() and body.val().Volume() > 0, name


def test_ground_floor_walls_envelope(built):
    model, solids = built
    g = model["geometry"]
    bb = solids["level_0_walls"].val().BoundingBox()
    assert (bb.xmin, bb.xmax) == pytest.approx((0, g["W"]), abs=0.5)
    assert (bb.ymin, bb.ymax) == pytest.approx((0, g["D"]), abs=0.5)
    assert (bb.zmin, bb.zmax) == pytest.approx((0, g["h_w"]), abs=0.5)


def test_walls_lose_material_for_every_opening(built):
    model, solids = built
    g = model["geometry"]
    lv = model["levels"][0]
    solid_volume = sum(abs(w["b"][0] - w["a"][0]) + abs(w["b"][1] - w["a"][1]) for w in lv["walls"])  # centerline length
    assert solids["level_0_walls"].val().Volume() < solid_volume * g["h_w"] * g["e"]  # strictly less than fully solid


def test_slab_with_stair_void_has_the_right_volume(built):
    model, solids = built
    g, st = model["geometry"], model["levels"][0]["stair"]
    void = (st["x1"] - st["x0"]) * (st["y1"] - st["y0"])
    assert solids["slab_1"].val().Volume() == pytest.approx((g["W"] * g["D"] - void) * g["slab"], rel=1e-4)
    assert solids["slab_2"].val().Volume() == pytest.approx(g["W"] * g["D"] * g["slab"], rel=1e-4)


def test_stair_flight_reaches_the_floor_above(built):
    model, solids = built
    bb = solids["stair_0"].val().BoundingBox()
    assert bb.zmax - bb.zmin == pytest.approx(model["geometry"]["fh"], abs=0.5)
    st = model["levels"][0]["stair"]
    assert bb.xmin == pytest.approx(st["x0"], abs=0.5) and bb.ylen == pytest.approx(st["width"], abs=0.5)


def test_roof_apex_and_extent(built):
    model, solids = built
    g = model["geometry"]
    bb = solids["roof"].val().BoundingBox()
    assert bb.zmax == pytest.approx(2 * g["fh"] + (g["D"] / 2) * math.tan(math.radians(25)), abs=1.0)
    assert bb.xlen == pytest.approx(g["W"] + 900, abs=1.0)


def test_foundation_parts_are_single_solids_spanning_footing_to_ground(built):
    from building.structure import foundation_parts, preliminary_foundation
    model, _ = built
    est = preliminary_foundation(model, 150.0)
    for part in foundation_parts(model, est):
        body = build_feature_tree(part["parameters"]["features"])
        bb = body.val().BoundingBox()
        assert len(body.solids().vals()) == 1 and body.val().isValid(), part["name"]
        assert bb.zmin == pytest.approx(-est["founding_depth_mm"], abs=0.5) and bb.zmax == pytest.approx(0.0, abs=0.5), part["name"]


def test_gable_walls_and_roof_fuse_into_valid_solids(built):
    _, solids = built
    for name in ("gable_west", "gable_east"):
        assert name in solids and solids[name].val().isValid() and solids[name].val().Volume() > 0


def test_furniture_boxes_have_the_catalog_volume(built):
    from building.furniture import furniture_parts, place_furniture
    model, _ = built
    items, _w = place_furniture(model)
    for item, part in list(zip(items, furniture_parts(items, model)))[:8]:
        body = build_feature_tree(part["parameters"]["features"])
        expected = (item["x1"] - item["x0"]) * (item["y1"] - item["y0"]) * item["height"]
        assert body.val().Volume() == pytest.approx(expected, rel=1e-4)
