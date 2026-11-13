"""Part names people can read: labels for display, an audit for new vehicles, and the rule the planner is given."""
import json
from pathlib import Path

import part_naming as PN
import vehicle_planner as VP
import vehicle_spec as VS


def test_labels_read_like_the_reference_tree():
    assert PN.readable("panels_door_fl") == "Front left door"
    assert PN.readable("wheel_fl_tyre") == "Front left wheel tyre"
    assert PN.readable("half_shaft_l") == "Left half shaft"
    assert PN.readable("side_glazing_1_left") == "Left side glazing 1"
    assert PN.readable("engine_piston_3") == "Engine piston 3"      # a meaningful prefix is kept
    assert PN.readable("panels_roof") == "Roof" and PN.readable("exterior_roof") == "Roof"      # an empty prefix is dropped
    assert PN.readable("exterior_door_front_left") == "Front left door"
    assert PN.readable("wheel_front_left_tyre") == "Front left wheel tyre"
    assert PN.readable("front_wheel_bearing_left") == "Left front wheel bearing"
    assert PN.readable("fuselage") == "Fuselage"


def test_a_name_that_is_all_prefix_or_empty_is_never_lost():
    assert PN.readable("panels") == "Panels" and PN.readable("") == ""


def test_the_audit_finds_abbreviations_and_empty_prefixes_and_suggests_the_fix():
    got = {i["name"]: i for i in PN.audit(["panels_door_fl", "rail_l", "panels_roof", "front_left_tire", "engine_piston_1"])}
    assert got["panels_door_fl"]["suggestion"] == "panels_door_front_left"
    assert got["rail_l"]["suggestion"] == "rail_left"
    assert got["panels_roof"]["suggestion"] == "roof"
    assert "front_left_tire" not in got and "engine_piston_1" not in got


def test_the_reference_names_pass_clean():
    ref = ["sculpted_lower_body", "dark_glass_greenhouse", "front_windscreen_glass", "side_glazing_1_left", "side_glazing_2_right",
           "front_left_tire", "front_left_rim", "rear_diffuser", "rear_wing_support_left", "side_blade_right"]
    assert PN.audit(ref) == []


def test_every_shipped_example_vehicle_has_readable_names():
    root = Path(__file__).resolve().parent.parent / "vehicles" / "examples"
    for f in sorted(root.glob("*.json")):
        ex = VS.expand_vehicle(json.loads(f.read_text()), None, None, None)
        names = [p["name"] for p in ex["parts"]]
        assert ex["ok"] and PN.audit(names) == [], (f.stem, PN.audit(names)[:3])
    car = [p["name"] for p in VS.expand_vehicle(json.loads((root / "car.json").read_text()), None, None, None)["parts"]]
    assert "exterior_door_front_left" in car and "wheel_front_left_tyre" in car and "half_shaft_left" in car


def test_mirrored_parts_get_spelled_out_sides_and_old_names_still_resolve():
    s = {"name": "t", "params": {}, "components": [
        {"id": "post", "type": "shaft", "parameters": {"diameter_mm": 4, "length_mm": 10}, "repeat": {"n": 2, "step": [10, 0, 0]},
         "position": [0, 5, 0], "mirror": "y", "assembly": "a"}],
        "explode": {"directions": {"post_2_l": [0, 0, 1]}, "intended_contact": [["post_1_l", "post_1_r"]]}}
    ex = VS.expand_vehicle(s, None, None, None)
    names = {p["name"] for p in ex["parts"]}
    assert names == {"post_1_left", "post_1_right", "post_2_left", "post_2_right"}
    assert ex["explode"]["directions"] == {"post_2_left": [0, 0, 1]}                  # a spec saved with _l/_r keeps working
    assert ex["explode"]["intended_contact"] == [["post_1_left", "post_1_right"]]


def test_the_planner_is_told_the_naming_rule():
    assert PN.RULE in VP.FORMAT_GUIDE
