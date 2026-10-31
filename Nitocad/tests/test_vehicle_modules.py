"""Modules (reusable internals), 'between', and the shipped vehicle specs. Pure Python, no CadQuery."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vehicle_spec as V


def load(name):
    return json.loads((ROOT / "vehicles" / "examples" / f"{name}.json").read_text())


def test_every_shipped_module_expands_alone_and_passes_its_own_checks():
    mods = V.list_modules()
    assert {"wheel_on_axle", "gear_pair", "bottom_bracket", "rotor_head"} <= set(mods)
    for m in mods:
        r = V.expand_vehicle({"name": "t", "components": [{"id": "x", "module": m}]})
        assert r["ok"], (m, r["errors"])
        assert r["parts"] and all(c["status"] == "pass" for c in r["checks"]), (m, r["checks"])


def test_shipped_vehicles_expand_with_all_design_checks_passing():
    expect = {"wheelbarrow": 13, "bicycle": 28, "rc_helicopter": 30}
    for name, n in expect.items():
        r = V.expand_vehicle(load(name))
        assert r["ok"], (name, r["errors"])
        assert len(r["parts"]) == n, (name, len(r["parts"]))
        assert all(c["status"] == "pass" for c in r["checks"]), (name, [c for c in r["checks"] if c["status"] != "pass"])
        names = [p["name"] for p in r["parts"]]
        assert len(names) == len(set(names))


def test_same_module_in_three_vehicles():
    uses = {n: [c["module"] for c in load(n)["components"] if "module" in c]
            for n in ("wheelbarrow", "bicycle", "rc_helicopter")}
    assert all("wheel_on_axle" in u for u in (uses["wheelbarrow"], uses["bicycle"]))
    assert "rotor_head" in uses["rc_helicopter"] and "gear_pair" in uses["rc_helicopter"]


def test_module_parts_are_prefixed_and_offset_by_the_instance_position():
    s = {"name": "t", "components": [
        {"id": "w1", "module": "wheel_on_axle", "position": [100, 0, 0]},
        {"id": "w2", "module": "wheel_on_axle", "position": [-100, 0, 0]}]}
    r = V.expand_vehicle(s)
    pos = {p["name"]: p["position"] for p in r["parts"]}
    assert pos["w1_axle"][0] == 100.0 and pos["w2_axle"][0] == -100.0
    assert {"w1_tyre", "w1_axle", "w1_bearing_l", "w1_bearing_r"} <= set(pos)


def test_module_params_are_bounded_and_isolated_from_vehicle_names():
    bad = V.expand_vehicle({"name": "t", "params": {"d": 400}, "components": [
        {"id": "w", "module": "wheel_on_axle", "params": {"tyre_dia": 99999}}]})
    assert not bad["ok"] and any("tyre_dia" in e for e in bad["errors"])
    unknown = V.expand_vehicle({"name": "t", "components": [{"id": "w", "module": "wheel_on_axle", "params": {"nope": 1}}]})
    assert any("unknown module param" in e for e in unknown["errors"])
    # a module cannot see the vehicle's own names
    s = {"name": "t", "params": {"vehicle_only": 5}, "modules": {"m": {"components": [
        {"id": "p", "type": "shaft", "parameters": {"diameter_mm": "=vehicle_only", "length_mm": 10}}]}},
        "components": [{"id": "x", "module": "m"}]}
    r = V.expand_vehicle(s)
    assert not r["ok"] and any("unknown name 'vehicle_only'" in e for e in r["errors"])


def test_module_misuse_is_reported():
    for extra, word in (({"rotation": [0, 90, 0]}, "rotation"), ({"repeat": {"n": 2, "step": [1, 0, 0]}}, "repeat"),
                        ({"mirror": "y"}, "mirror")):
        r = V.expand_vehicle({"name": "t", "components": [{"id": "w", "module": "wheel_on_axle", **extra}]})
        assert not r["ok"] and any(word in e for e in r["errors"])
    assert any("unknown module" in e for e in V.expand_vehicle(
        {"name": "t", "components": [{"id": "w", "module": "../../etc/passwd"}]})["errors"])


def test_failed_module_check_shows_up_prefixed():
    r = V.expand_vehicle({"name": "t", "components": [
        {"id": "w", "module": "wheel_on_axle", "params": {"rim_dia": 300, "tyre_dia": 250}}]})
    c = {x["name"]: x for x in r["checks"]}
    assert c["w:rim_inside_tyre"]["status"] == "fail"


def test_inline_module_and_internal_mates_get_prefixed():
    s = {"name": "t", "modules": {"stack": {"components": [
        {"id": "base", "type": "flat_plate", "fixed": True, "position": [0, 0, 0],
         "parameters": {"length_mm": 50, "width_mm": 30, "thickness_mm": 4}},
        {"id": "post", "type": "shaft", "parameters": {"diameter_mm": 4, "length_mm": 10},
         "mate": {"type": "face", "to": "base", "face": "+Z"}}]}},
        "components": [{"id": "a", "module": "stack"}]}
    r = V.expand_vehicle(s)
    assert r["ok"], r["errors"]
    post = [p for p in r["parts"] if p["name"] == "a_post"][0]
    assert post["mate"]["to"] == "a_base"


def test_between_computes_position_rotation_and_length():
    s = {"name": "t", "components": [
        {"id": "diag", "type": "tube", "between": [[0, 0, 0], [100, 0, 100]], "parameters": {"outer_diameter_mm": 25, "wall_thickness_mm": 2}},
        {"id": "lat", "type": "tube", "between": [[0, -50, 0], [0, 50, 0]], "parameters": {"outer_diameter_mm": 25, "wall_thickness_mm": 2}},
        {"id": "base_o", "type": "tube", "origin": "base", "between": [[10, 0, 0], [10, 0, 80]], "parameters": {"outer_diameter_mm": 25, "wall_thickness_mm": 2}}]}
    r = V.expand_vehicle(s)
    p = {x["name"]: x for x in r["parts"]}
    assert r["ok"], r["errors"]
    assert list(p["diag"]["position"]) == [50.0, 0.0, 50.0] and list(p["diag"]["rotation"]) == [0.0, 45.0, 0.0]
    assert abs(p["diag"]["parameters"]["length_mm"] - 141.4214) < 1e-3
    assert list(p["lat"]["rotation"]) == [-90.0, 0.0, 0.0] and p["lat"]["parameters"]["length_mm"] == 100.0
    assert list(p["base_o"]["position"]) == [10.0, 0.0, 0.0]


def test_between_rejects_unsupported_cases():
    base = {"type": "tube", "parameters": {"outer_diameter_mm": 25, "wall_thickness_mm": 2}}
    cases = [{"between": [[0, 0, 0], [1, 1, 1]]},                       # 3D diagonal
             {"between": [[0, 0, 0], [0, 0, 0]]},                       # zero length
             {"between": [[0, 0, 0], [5, 0, 5]], "position": [0, 0, 0]},
             {"between": [[0, 0, 0], [5, 0, 5]], "origin": "middle"},
             {"between": [[0, 0, 0], [5, 0, 5]], "parameters": {"outer_diameter_mm": 25, "wall_thickness_mm": 2, "length_mm": 9}}]
    for extra in cases:
        r = V.expand_vehicle({"name": "t", "components": [{"id": "x", **{**base, **extra}}]})
        assert not r["ok"], extra
