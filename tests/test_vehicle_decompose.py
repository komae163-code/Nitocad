"""Coarse vehicle plans are rejected with a message that says how to split them. No CAD kernel needed."""
import json
from pathlib import Path

import pytest

import vehicle_decompose as VD
import vehicle_planner as VP
import vehicle_spec as VS
import vehicle_views as VV
import spec_library as SL

ROOT = Path(__file__).resolve().parents[1]


def coarse():
    spec = {"name": "car", "components": [{"id": "body", "type": "feature_tree", "parameters": {"features": []}},
                                          {"id": "exhaust", "type": "tube", "parameters": {}}]}
    ex = {"parts": [{"name": "body", "part_type": "feature_tree", "parameters": {"features": []}},
                    {"name": "exhaust", "part_type": "tube", "parameters": {}}], "assemblies": {"main": ["body", "exhaust"]}}
    return spec, ex


def test_two_part_car_is_rejected_with_every_reason():
    spec, ex = coarse()
    text = " | ".join(VD.coarse_issues(spec, ex, "a car with a gearbox"))
    assert "only 2 parts" in text and "group" in text and "'body' is one feature_tree" in text and "gear part" in text


def test_a_solid_body_with_panels_is_asked_to_become_a_shell():
    parts = [{"name": n, "part_type": "flat_plate", "parameters": {}} for n in ("floor", "roof", "door_l", "door_r")]
    parts.append({"name": "body", "part_type": "feature_tree", "parameters": {"features": [{"id": "arch", "op": "extrude", "mode": "cut"}]}})
    ex = {"parts": parts + [{"name": f"p{i}", "part_type": "shaft", "parameters": {}} for i in range(9)],
          "assemblies": {"a": [], "b": [], "c": []}}
    assert any("solid block" in m for m in VD.coarse_issues({"name": "car"}, ex, "a car"))
    parts[-1]["parameters"]["features"].append({"id": "interior", "op": "extrude", "mode": "cut"})
    assert not any("solid block" in m for m in VD.coarse_issues({"name": "car"}, ex, "a car"))


def test_gears_need_a_keyway_and_a_flywheel_is_not_a_wheel():
    gear = lambda: {"name": "g", "part_type": "gear", "parameters": {"module": 2}}
    ex = {"parts": [gear(), dict(gear(), name="g2"), {"name": "flywheel", "part_type": "feature_tree", "parameters": {}},
                    {"name": "s", "part_type": "shaft", "parameters": {}}], "assemblies": {"a": []}}
    msgs = VD.coarse_issues({"name": "gearbox"}, ex, "a gearbox")
    assert any("no keyway" in m for m in msgs) and not any("axle" in m for m in msgs)


def test_shipped_examples_are_not_flagged_by_the_small_machine_rules():
    for name in ("wheelbarrow", "bicycle", "mini_engine_inline_four", "rc_helicopter", "two_axle_cart"):
        spec = json.loads((ROOT / "vehicles" / "examples" / f"{name}.json").read_text())
        assert VD.coarse_issues(spec, VS.expand_vehicle(spec, None, None, None), "") == []


class Script:
    def __init__(self, *replies): self.replies, self.calls = list(replies), 0
    def __call__(self, messages):
        self.calls += 1
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _two_part_car():
    return {"name": "family_car", "components": [
        {"id": "body", "type": "feature_tree", "parameters": {"features": [
            {"id": "block", "op": "extrude", "plane": "XY", "distance": 1300,
             "profile": {"type": "polygon", "points": [[-2100, -890], [2100, -890], [2100, 890], [-2100, 890]]}}]}},
        {"id": "exhaust", "type": "tube", "parameters": {"outer_diameter_mm": 50, "wall_thickness_mm": 2, "length_mm": 3150},
         "position": [-1950, 300, 100]}]}


def test_the_planner_sends_the_split_rules_back_and_returns_unresolved_checks():
    lib = SL.SpecLibrary(SL.MemoryStore())
    llm = Script(json.dumps(_two_part_car()))
    r = VP.plan_vehicle("a car with a 5 speed gearbox", llm, lib, max_attempts=2, save_candidate=False)
    assert r["ok"] and llm.calls == 2
    assert any(c["name"] == "split into parts" for c in r["unresolved_checks"])


def test_the_planner_accepts_the_shipped_car_without_complaint():
    lib = SL.SpecLibrary(SL.MemoryStore())
    spec = json.loads((ROOT / "vehicles" / "examples" / "car.json").read_text())
    r = VP.plan_vehicle("a car with a gearbox and an engine", Script(json.dumps(spec)), lib, max_attempts=2, save_candidate=False)
    assert r["ok"] and r["attempts"] == 1 and r["unresolved_checks"] == []


def test_the_library_example_a_car_request_finds_is_the_panel_built_one():
    lib = SL.SpecLibrary(SL.MemoryStore())
    lib.seed_from_dir(ROOT / "vehicles" / "examples")
    top = lib.search("a car with a gearbox", k=1)[0]
    assert top["name"] == "hatchback_car" and "body" not in {c["id"] for c in top["spec"]["components"]}


def test_default_directions_send_wheels_out_roof_up_exhaust_down():
    ref = (0.0, 0.0, 500.0)
    assert VV.default_direction("wheel_fl_tyre", (1000, 800, 300), ref) == (0.0, 1.0, 0.0)
    assert VV.default_direction("wheel_rr_tyre", (-1000, -800, 300), ref) == (0.0, -1.0, 0.0)
    assert VV.default_direction("roof", (0, 0, 1400), ref) == (0.0, 0.0, 1.0)
    assert VV.default_direction("exhaust", (-500, 300, 100), ref) == (0.0, 0.0, -1.0)
    assert VV.default_direction("mystery", (0, 0, 500), ref) is None


def _car():
    spec = json.loads((ROOT / "vehicles" / "examples" / "car.json").read_text())
    return spec, VS.expand_vehicle(spec, None, None, None)


def test_the_shipped_car_passes_the_gate_even_when_it_is_asked_for_gears_and_an_engine():
    spec, ex = _car()
    assert ex["ok"] and not [c for c in ex["checks"] if c["status"] != "pass"]
    assert VD.coarse_issues(spec, ex, "a car with a gearbox and an engine") == []


def test_the_shipped_car_has_no_monolith_and_real_sub_parts():
    spec, ex = _car()
    parts = {p["name"]: p for p in ex["parts"]}
    assert "body" not in parts and len(parts) >= 60
    assert {"powertrain", "gearbox", "doors", "exhaust", "wheels", "interior", "body"} <= set(ex["assemblies"])
    gears = [p for p in parts.values() if p["part_type"] == "gear"]
    assert len(gears) == 4 and all(g["parameters"]["keyway_width_mm"] > 0 and g["parameters"]["backlash_mm"] > 0 for g in gears)
    assert any(f["id"] == "interior" and f["mode"] == "cut" for f in parts["fuel_tank"]["parameters"]["features"])


def test_the_shipped_gear_pairs_mesh_and_are_valid_gears():
    import validator as V
    _, ex = _car()
    parts = {p["name"]: p for p in ex["parts"]}
    for pair in (("gear_in_1", "gear_ct_1"), ("gear_in_2", "gear_ct_2")):
        a, b = (parts[n] for n in pair)
        m = a["parameters"]["module"]
        assert m == b["parameters"]["module"]
        centre = m * (a["parameters"]["teeth"] + b["parameters"]["teeth"]) / 2
        assert abs((a["position"][2] - b["position"][2]) - centre) < 1e-9 and a["position"][0] == b["position"][0]
        for g in (a, b):
            r = V.validate_gear(g["parameters"])
            assert not r.errors, r.errors


def test_the_shipped_driveline_is_one_straight_line_from_gearbox_to_each_wheel():
    _, ex = _car()
    p = {x["name"]: x for x in ex["parts"]}
    wheel_z = p["wheel_fr_axle"]["position"][2]
    out = p["counter_shaft"]
    assert abs(out["position"][2] - wheel_z) < 1e-9                                   # output shaft on the wheel-centre line
    assert abs(out["position"][0] - p["wheel_fr_axle"]["position"][0]) < 1e-9        # and it ends at the front axle
    for hs, wheel in (("halfshaft_r", "wheel_fr_axle"), ("halfshaft_l", "wheel_fl_axle")):
        h, w = p[hs], p[wheel]
        end = h["position"][1] + (1 if h["position"][1] > 0 else -1) * h["parameters"]["length_mm"]
        inner = w["position"][1] + (w["parameters"]["length_mm"] if w["position"][1] < 0 else 0)   # the axle end nearest the car
        assert abs(end - inner) < 1e-6, hs                                             # the half shaft meets the wheel axle
        assert abs(h["position"][2] - wheel_z) < 1e-9 and abs(h["rotation"][0]) == 90
    assert "driveline" in ex["assemblies"] and "diff_housing" in p


def test_the_output_shaft_misses_the_flywheel_and_the_housing_misses_the_crank():
    _, ex = _car()
    p = {x["name"]: x for x in ex["parts"]}
    fly = p["engine_flywheel"]
    fly_bottom = fly["position"][2] - fly["parameters"]["outer_diameter_mm"] / 2
    assert fly_bottom > p["counter_shaft"]["position"][2] + p["counter_shaft"]["parameters"]["diameter_mm"] / 2
