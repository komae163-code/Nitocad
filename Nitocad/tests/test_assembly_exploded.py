"""Pure-Python tests (no CadQuery) for assembly_exploded."""
from __future__ import annotations

import sys
import xml.dom.minidom
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assembly_exploded as AE


def box(x0, y0, z0, x1, y1, z1):
    return {"min": (x0, y0, z0), "max": (x1, y1, z1)}


def cart():
    """Chassis plate, a wheel each side (touching it), and an axle through the chassis centre."""
    return {
        "chassis": box(-50, -20, 10, 50, 20, 14),
        "wheel_l": box(-8, -30, 0, 8, -18, 16),     # overlaps the chassis edge slightly
        "wheel_r": box(-8, 18, 0, 8, 30, 16),
        "axle":    box(-2, -25, 10, 2, 25, 14),      # centred on the chassis, so radial has no direction
    }


def test_radial_separates_parts_that_have_a_direction():
    r = AE.explode({k: v for k, v in cart().items() if k != "axle"}, fixed="chassis")
    assert r["ok"] and r["unseparated"] == [] and r["no_direction"] == []
    assert r["offsets"]["chassis"] == (0, 0, 0)
    assert r["offsets"]["wheel_l"][1] < 0 < r["offsets"]["wheel_r"][1]     # pushed apart sideways
    assert r["factor"] > 1.0


def test_part_on_the_reference_centre_is_flagged_not_guessed():
    r = AE.explode(cart(), fixed="chassis")
    assert r["no_direction"] == ["axle"]
    assert r["offsets"]["axle"] == (0, 0, 0)
    assert ["chassis", "axle"] in r["unseparated"] or ["axle", "chassis"] in r["unseparated"]


def test_explicit_direction_fixes_it():
    r = AE.explode(cart(), fixed="chassis", directions={"axle": (0, 0, -1)})
    assert r["no_direction"] == []
    assert r["offsets"]["axle"][2] < 0
    assert r["unseparated"] == []


def test_intended_contact_is_not_reported():
    boxes = {"a": box(0, 0, 0, 10, 10, 10), "b": box(5, 0, 0, 15, 10, 10)}
    r = AE.explode(boxes, fixed="a", directions={"b": (1, 0, 0)}, intended_contact=[("a", "b")])
    assert r["unseparated"] == []


def test_unseparable_pair_is_reported_at_the_cap():
    boxes = {"a": box(0, 0, 0, 10, 10, 10), "b": box(0, 0, 0, 10, 10, 10)}   # identical, nothing moves them apart
    r = AE.explode(boxes)
    assert r["ok"] and r["unseparated"] and sorted(r["no_direction"]) in (["a", "b"],)


def test_bad_inputs_return_errors():
    assert not AE.explode({})["ok"]
    assert not AE.explode(cart(), fixed="nope")["ok"]
    assert not AE.explode(cart(), directions={"ghost": (1, 0, 0)})["ok"]


def test_sequence_starts_with_fixed_and_is_stable():
    r = AE.explode(cart(), fixed="chassis", directions={"axle": (0, 0, -1)})
    assert r["sequence"][0] == "chassis"
    assert sorted(r["sequence"]) == sorted(cart())
    assert r["sequence"] == AE.explode(cart(), fixed="chassis", directions={"axle": (0, 0, -1)})["sequence"]


def test_apply_to_parts_moves_position_and_keeps_originals():
    parts = [{"name": "wheel_r", "placement": {"position": [0, 24, 8], "rotation": [0, 0, 0]}},
             {"name": "chassis"}]
    res = {"offsets": {"wheel_r": (0, 10, 0), "chassis": (0, 0, 0)}}
    out = AE.apply_to_parts(parts, res)
    assert out[0]["placement"]["position"] == [0, 34, 8]
    assert out[1]["placement"]["position"] == [0, 0, 0]
    assert parts[0]["placement"]["position"] == [0, 24, 8]       # input untouched


def test_svg_is_valid_xml_and_says_it_is_schematic():
    boxes = cart()
    r = AE.explode(boxes, fixed="chassis", directions={"axle": (0, 0, -1)})
    for view in ("front", "top", "side"):
        svg = AE.to_svg(boxes, r, view=view)
        xml.dom.minidom.parseString(svg)
        assert "SCHEMATIC" in svg and "chassis" in svg
    try:
        AE.to_svg(boxes, r, view="iso")
    except ValueError:
        pass
    else:
        raise AssertionError("bad view should raise")


def test_viewer_json_roundtrips_and_matches_offsets():
    import json
    boxes = cart()
    r = AE.explode(boxes, fixed="chassis", directions={"axle": (0, 0, -1)})
    d = json.loads(json.dumps(AE.to_viewer_json(boxes, r, "t")))
    assert d["title"] == "t" and len(d["parts"]) == len(boxes)
    p = {x["name"]: x for x in d["parts"]}
    assert p["chassis"]["step"] == 0 and p["wheel_r"]["offset"] == list(r["offsets"]["wheel_r"])
