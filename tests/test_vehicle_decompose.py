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
    """A valid plan that is still a toy: one body block and one exhaust tube."""
    side = [[-2100, 150], [2100, 150], [2100, 670], [1720, 780], [-1840, 780], [-2100, 710]]
    return {"name": "family_car", "components": [
        {"id": "body", "type": "feature_tree", "assembly": "main", "parameters": {"features": [
            {"id": "lower", "op": "extrude", "plane": "XZ", "symmetric": True, "distance": 1780,
             "profile": {"type": "polygon", "points": side}}]}, "position": [0, 0, 0]},
        {"id": "exhaust", "type": "tube", "assembly": "main", "parameters": {"outer_diameter_mm": 50, "wall_thickness_mm": 2,
                                                                          "length_mm": 3000}, "position": [0, 300, 100]}]}


def test_the_planner_sends_the_split_rules_back_and_returns_unresolved_checks():
    llm = Script(json.dumps(_two_part_car()))
    lib = SL.SpecLibrary(SL.MemoryStore())
    r = VP.plan_vehicle("a car with a 5 speed gearbox", llm, lib, max_attempts=2, save_candidate=False)
    assert r["ok"] and llm.calls == 2
    assert any(c["name"] == "split into parts" for c in r["unresolved_checks"])


def test_the_planner_accepts_the_shipped_car_on_the_first_try():
    spec = json.loads((ROOT / "vehicles" / "examples" / "car.json").read_text())
    llm = Script(json.dumps(spec))
    r = VP.plan_vehicle("a car with a 5 speed gearbox and a petrol engine", llm, SL.SpecLibrary(SL.MemoryStore()),
                        max_attempts=2, save_candidate=False)
    assert r["ok"] and llm.calls == 1 and r["unresolved_checks"] == []


def test_a_snake_case_name_still_counts_as_a_car():
    spec = _two_part_car()
    spec["name"] = "hatchback_car"
    assert VD.coarse_issues(spec, VS.expand_vehicle(spec), "")          # the name alone is enough


def test_default_directions_send_wheels_out_roof_up_exhaust_down():
    ref = (0.0, 0.0, 500.0)
    assert VV.default_direction("wheel_front_left_tyre", (1000, 800, 300), ref) == (0.0, 1.0, 0.0)
    assert VV.default_direction("wheel_rear_right_tyre", (-1000, -800, 300), ref) == (0.0, -1.0, 0.0)
    assert VV.default_direction("roof", (0, 0, 1400), ref) == (0.0, 0.0, 1.0)
    assert VV.default_direction("exhaust", (-500, 300, 100), ref) == (0.0, 0.0, -1.0)
    assert VV.default_direction("mystery", (0, 0, 500), ref) is None
