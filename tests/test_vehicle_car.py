"""The shipped car and the car_body module: built from real parts, nothing overlaps, checks pass. No CAD kernel."""
import json
from pathlib import Path

import pytest

import vehicle_decompose as VD
import vehicle_overlap as VO
import vehicle_shell as SH
import vehicle_spec as VS
from validator import validate_parameters

ROOT = Path(__file__).resolve().parents[1]
CAR = json.loads((ROOT / "vehicles" / "examples" / "car.json").read_text())
EX = VS.expand_vehicle(CAR)
PARTS = {p["name"]: p for p in EX["parts"]}


def panels(**params):
    spec = {"name": "t", "components": [{"id": "exterior", "module": "car_body", "assembly": "body", "position": [0, 0, 0],
                                         "params": params}]}
    return VS.expand_vehicle(spec)


# ------------------------------------------------------------------------------------------- the module
def test_car_body_makes_23_separate_thin_panels_and_its_checks_pass():
    ex = panels()
    assert ex["ok"], ex["errors"]
    assert len(ex["parts"]) == 23 and all(c["status"] == "pass" for c in ex["checks"])
    names = {p["name"] for p in ex["parts"]}
    assert {"exterior_floor", "exterior_roof", "exterior_hood", "exterior_door_front_left", "exterior_door_rear_right", "exterior_windscreen",
            "exterior_side_panel_left", "exterior_front_bumper"} <= names
    for p in ex["parts"]:                       # every panel is a sheet: under 0.03 m3 (a solid car body is about 7 m3)
        vol = sum((SH.polygon_area(f["profile"]["points"]) if f["profile"]["type"] == "polygon"
                   else f["profile"]["width"] * f["profile"]["height"]) * f["distance"]
                  for f in p["parameters"]["features"] if f.get("mode") in (None, "new", "add"))
        assert vol < 3e7, (p["name"], vol)
    assert ex["auto_shelled"] == [] and ex["shell_problems"] == []


@pytest.mark.parametrize("params", [{}, {"wheelbase": 2800, "body_len": 4600}, {"wheel_dia": 600, "wheelbase": 2500},
                                    {"body_w": 1900, "track": 1650, "tyre_w": 225}, {"rear_bay": 600, "body_len": 4400}])
def test_the_panels_never_overlap_each_other(params):
    ex = panels(**params)
    assert ex["ok"], ex["errors"]
    r = VO.find_overlaps(ex["parts"])
    assert r["overlaps"] == [] and r["unmodelled"] == []


def test_a_door_fits_its_opening_with_a_gap():
    ex = panels()
    side = next(p for p in ex["parts"] if p["name"] == "exterior_side_panel_left")["parameters"]["features"]
    opening = next(f for f in side if f["id"] == "door_front")["profile"]
    door = next(p for p in ex["parts"] if p["name"] == "exterior_door_front_left")["parameters"]["features"][0]["profile"]
    assert door["width"] == pytest.approx(opening["width"] - 6) and door["height"] == pytest.approx(opening["height"] - 6)
    assert door["center"] == opening["center"]


def test_the_firewall_has_a_hole_for_the_propshaft_and_the_floor_has_wheel_wells():
    ex = panels(tunnel_z=406, tunnel_d=90)
    fw = next(p for p in ex["parts"] if p["name"] == "exterior_firewall")["parameters"]["features"]
    assert fw[1]["mode"] == "cut" and fw[1]["profile"]["center"][1] == 406 and fw[1]["profile"]["width"] == 90
    floor = next(p for p in ex["parts"] if p["name"] == "exterior_floor")["parameters"]["features"]
    assert [f["id"] for f in floor[1:]] == ["well_rear_l", "well_rear_r"]


# ------------------------------------------------------------------------------------------ the shipped car
def test_the_car_is_ninety_seven_parts_in_eight_groups_and_every_check_passes():
    assert EX["ok"], EX["errors"]
    assert len(EX["parts"]) == 97
    assert set(EX["assemblies"]) == {"body", "wheels", "engine", "gearbox", "driveline", "exhaust", "chassis", "interior"}
    assert [c for c in EX["checks"] if c["status"] != "pass"] == []


def test_the_car_passes_its_own_splitting_rules_for_the_hardest_request():
    assert VD.coarse_issues(CAR, EX, "a car with a 5 speed gearbox and an engine") == []


def test_no_part_of_the_car_overlaps_another():
    r = VO.find_overlaps(EX["parts"])
    assert r["unmodelled"] == [], "every part must have a model, or this check says nothing about it"
    assert r["overlaps"] == []


def test_the_car_has_no_solid_body_block():
    for p in EX["parts"]:
        if p["part_type"] == "feature_tree" and SH.BODY_NAME.search(p["name"]):
            assert SH.has_hollow(p["parameters"]["features"]) or SH.is_sheet(p["parameters"]["features"], 2.5), p["name"]
    assert EX["auto_shelled"] == []


def test_the_gearbox_has_five_keyed_pairs_that_mesh():
    gears = [p for p in EX["parts"] if p["name"].startswith(("gearbox_input_gear_", "gearbox_counter_gear_"))]
    assert len(gears) == 10
    for p in gears:
        prm = p["parameters"]
        assert prm["keyway_width_mm"] > 0 and prm["keyway_depth_mm"] > 0 and prm["backlash_mm"] > 0
    for k in range(1, 6):
        a, b = PARTS[f"gearbox_input_gear_{k}"], PARTS[f"gearbox_counter_gear_{k}"]
        cd = a["parameters"]["module"] * (a["parameters"]["teeth"] + b["parameters"]["teeth"]) / 2
        assert cd == pytest.approx(PARTS["gearbox_input_shaft"]["position"][2] - PARTS["gearbox_counter_shaft"]["position"][2])
        assert a["position"][0] == b["position"][0]                      # the pair sits in one plane
    ratios = [PARTS[f"gearbox_counter_gear_{k}"]["parameters"]["teeth"] / PARTS[f"gearbox_input_gear_{k}"]["parameters"]["teeth"] for k in range(1, 6)]
    assert ratios == sorted(ratios, reverse=True) and ratios[0] > 1 > ratios[-1]


def test_the_planner_validator_changes_none_of_the_numbers_written_in_the_spec():
    """It used to lengthen a short piston and shrink a gear bore without a word, so the model was not what was drawn."""
    for comp in CAR["components"]:
        if "parameters" not in comp or comp["id"] not in PARTS:
            continue
        for k, v in comp["parameters"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                assert PARTS[comp["id"]]["parameters"][k] == pytest.approx(v), (comp["id"], k)


def test_the_expanded_values_are_already_what_the_validator_would_return():
    for p in EX["parts"]:
        fixed, _ = validate_parameters(p["part_type"], dict(p["parameters"]))
        for k, v in fixed.items():
            if k != "features":
                assert p["parameters"].get(k) == pytest.approx(v), (p["name"], k)


def test_a_longer_wheelbase_still_expands_with_the_checks_passing():
    ex = VS.expand_vehicle(CAR, {"wheelbase": 2800, "body_len": 4400})
    assert ex["ok"], ex["errors"]
    assert [c for c in ex["checks"] if c["status"] != "pass"] == []


def test_the_engine_sits_between_the_flywheel_and_the_pulley_and_the_gearbox_ends_at_the_flywheel():
    crank, fly, pulley = PARTS["engine_crankshaft"], PARTS["engine_flywheel"], PARTS["engine_crank_pulley"]
    assert fly["position"][0] + 24 == pytest.approx(crank["position"][0])
    assert pulley["position"][0] == pytest.approx(crank["position"][0] + 392)
    case = PARTS["gearbox_case"]
    case_front = case["position"][0] + case["parameters"]["length_mm"] / 2
    assert 0 < fly["position"][0] - case_front < 10                       # a few mm of clutch gap, not a collision
    assert all(PARTS[f"engine_liner_{i}"]["position"][2] > PARTS["engine_crankshaft"]["position"][2] for i in range(1, 5))


def test_the_propshaft_passes_through_the_firewall_hole():
    shaft, fw = PARTS["propshaft"], PARTS["exterior_firewall"]["parameters"]["features"]
    hole = fw[1]["profile"]
    assert shaft["position"][2] == pytest.approx(hole["center"][1]) and shaft["parameters"]["diameter_mm"] < hole["width"]
    assert shaft["position"][0] < 0 < shaft["position"][0] + shaft["parameters"]["length_mm"]


def test_the_seed_is_small_enough_to_be_shown_to_the_model():
    import vehicle_planner as VP
    spec = {k: v for k, v in CAR.items() if k not in ("notes", "dev_notes")}
    assert len(json.dumps(spec, separators=(",", ":"))) <= VP.MAX_EXAMPLE_CHARS


def test_a_wheels_mirrored_bearings_stay_on_that_wheel():
    """Mirroring inside a module used to apply after the instance offset, so wheel_front_left_bearing_right sat on the right wheel."""
    for wheel in ("front_left", "front_right", "rear_left", "rear_right"):
        axle_y = PARTS[f"wheel_{wheel}_axle"]["position"][1] + PARTS[f"wheel_{wheel}_axle"]["parameters"]["length_mm"] / 2
        for side in ("left", "right"):
            assert abs(PARTS[f"wheel_{wheel}_bearing_{side}"]["position"][1] - axle_y) < 120, (wheel, side)


def _boxes(p):
    lo, hi = VO._local_box(p["part_type"], p["parameters"])
    return tuple(lo) + tuple(hi)


@pytest.mark.parametrize("group", [None, "body", "wheels", "engine", "gearbox", "driveline", "exhaust", "chassis", "interior"])
def test_every_group_and_the_whole_car_explode_with_nothing_left_stuck(group):
    import vehicle_views as VV
    r = VV.exploded(CAR, EX["parts"], _boxes, group=group)
    assert r["ok"], r.get("errors")
    assert r["warnings"] == [], r["warnings"]


def test_a_part_inside_another_that_goes_the_same_way_is_pulled_further():
    import vehicle_views as VV
    boxes = {"panel": {"min": (0, 0, 0), "max": (100, 2, 100)}, "door": {"min": (10, 0, 10), "max": (60, 2, 60)},
             "far": {"min": (500, 0, 0), "max": (600, 2, 100)}}
    dirs = {n: (0.0, 1.0, 0.0) for n in boxes}
    s = VV.nest_scales(boxes, dirs)
    assert s["panel"] == 1.0 and s["door"] > s["panel"] and s["far"] == 1.0
    assert max(VV.nest_scales({str(i): {"min": (0, 0, 0), "max": (100 - i, 100 - i, 100 - i)} for i in range(12)},
                              {str(i): (0.0, 1.0, 0.0) for i in range(12)}).values()) <= VV.NEST_SCALE_CAP
