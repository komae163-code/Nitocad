"""Pure-Python tests (no CadQuery) for vehicle_spec."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import vehicle_spec as V


def load(name):
    return json.loads((ROOT / "vehicles" / "examples" / f"{name}.json").read_text())


def tiny(**over):
    s = {"name": "t", "params": {"w": {"default": 40, "min": 10, "max": 100}},
         "components": [{"id": "plate", "type": "flat_plate",
                         "parameters": {"length_mm": "=w*2", "width_mm": "=w", "thickness_mm": 5}}]}
    s.update(over)
    return s


# --- the two shipped examples: same code, different vehicles -------------------------------------------------
def test_examples_expand_and_pass_the_planner_validator():
    loco = V.expand_vehicle(load("loco_0_4_0_core"))
    cart = V.expand_vehicle(load("two_axle_cart"))
    assert loco["ok"], loco["errors"]
    assert cart["ok"], cart["errors"]
    names = [p["name"] for p in loco["parts"]]
    assert len(names) == len(set(names))
    assert {"wheel_l_1", "wheel_l_2", "wheel_r_1", "wheel_r_2"} <= set(names)
    assert set(loco["assemblies"]) == {"loco", "tender"}
    assert loco["fixed"] == {"loco": "chassis", "tender": "tender_frame"}
    assert [e["name"] for e in loco["external"]] == ["coupling_pin"]          # external parts are kept out of the planner list
    assert all(c["status"] == "pass" for c in loco["checks"])


def test_overrides_change_the_result_and_are_bounded():
    base = load("loco_0_4_0_core")
    a = V.expand_vehicle(base)
    b = V.expand_vehicle(base, {"gauge": 45})
    assert b["ok"] and b["params"]["axle_len"] > a["params"]["axle_len"]
    y = {p["name"]: p["position"][1] for p in b["parts"]}
    assert y["wheel_l_1"] == -y["wheel_r_1"] and y["wheel_l_1"] > a["parts"][0]["position"][1] * 0 + 20
    bad = V.expand_vehicle(base, {"gauge": 9999})
    assert not bad["ok"] and any("gauge" in e for e in bad["errors"])
    assert not V.expand_vehicle(base, {"nope": 1})["ok"]


def test_design_check_fails_when_the_limit_is_broken():
    r = V.expand_vehicle(load("loco_0_4_0_core"), {"gauge": 80, "max_width": 65})
    c = {x["name"]: x for x in r["checks"]}
    assert r["ok"] and c["overall_width_within_limit"]["status"] == "fail"
    assert "wider than" in c["overall_width_within_limit"]["detail"] and r["warnings"]


# --- expressions are a strict whitelist ----------------------------------------------------------------------------
def test_expression_whitelist_blocks_everything_dangerous():
    for bad in ("__import__('os').system('x')", "(1).__class__", "[x for x in range(3)]", "lambda: 1",
                "open('/etc/passwd')", "a.b", "2**100", "1/0", "unknown_name + 1", "'text'", "max(a=1)"):
        try:
            V.evaluate(bad, {"a": 1.0})
        except V.SpecError:
            continue
        raise AssertionError(f"should have been rejected: {bad}")
    assert V.evaluate("sqrt(16) + max(1, 2) * 3", {}) == 10.0
    assert V.evaluate("approx(0.1+0.2, 0.3)", {}) is True
    assert V.evaluate("1 < x <= 5 and not (x == 3)", {"x": 4.0}) is True


def test_derived_values_resolve_in_any_order_and_cycles_are_reported():
    for order in ({"a": "=w + 1", "b": "=a * 2"}, {"b": "=a * 2", "a": "=w + 1"}):       # a database sorts keys
        r = V.expand_vehicle(tiny(derived=order))
        assert r["ok"] and r["params"]["b"] == 82.0
    cyc = V.expand_vehicle(tiny(derived={"a": "=b + 1", "b": "=a + 1"}))
    assert not cyc["ok"] and any("unknown name" in e for e in cyc["errors"])
    missing = V.expand_vehicle(tiny(derived={"a": "=nope + 1"}))
    assert not missing["ok"] and any("unknown name 'nope'" in e for e in missing["errors"])


def test_every_shipped_vehicle_survives_a_database_round_trip():
    import json as _j
    for name in ("wheelbarrow", "bicycle", "rc_helicopter", "loco_0_4_0_core", "two_axle_cart"):
        spec = load(name)
        again = _j.loads(_j.dumps(spec, sort_keys=True))
        a, b = V.expand_vehicle(spec), V.expand_vehicle(again)
        assert a["ok"] and b["ok"], (name, b["errors"][:2])
        assert [p["name"] for p in a["parts"]] == [p["name"] for p in b["parts"]]
        assert a["params"] == b["params"]


# --- repeat / mirror -----------------------------------------------------------------------------------------------
def test_repeat_and_mirror_naming_and_positions():
    s = tiny(components=[{"id": "post", "type": "shaft", "parameters": {"diameter_mm": 4, "length_mm": 10},
                          "repeat": {"n": 3, "step": [10, 0, 0]}, "position": [0, 5, 0], "mirror": "y"}])
    r = V.expand_vehicle(s)
    assert r["ok"], r["errors"]
    pos = {p["name"]: list(p["position"]) for p in r["parts"]}
    assert len(pos) == 6 and pos["post_3_l"] == [20.0, 5.0, 0.0] and pos["post_3_r"] == [20.0, -5.0, 0.0]


def test_no_part_cap_but_a_runaway_guard():
    big = tiny(components=[{"id": "p", "type": "flat_plate", "repeat": {"n": 500, "step": [1, 0, 0]},
                            "parameters": {"length_mm": 5, "width_mm": 5, "thickness_mm": 1}}])
    r = V.expand_vehicle(big)
    assert r["ok"] and len(r["parts"]) == 500                                  # well past the old 40
    runaway = copy.deepcopy(big)
    runaway["components"][0]["repeat"]["n"] = 10**9
    r2 = V.expand_vehicle(runaway)
    assert not r2["ok"] and any("too many parts" in e for e in r2["errors"])


# --- errors are reported, not raised -------------------------------------------------------------------------------
def test_bad_specs_return_errors_instead_of_raising():
    assert not V.expand_vehicle(None)["ok"]
    assert not V.expand_vehicle({"name": "x", "components": []})["ok"]
    r = V.expand_vehicle(tiny(components=[{"id": "Bad Id", "type": "shaft"}]))
    assert not r["ok"] and any("snake_case" in e for e in r["errors"])
    r = V.expand_vehicle(tiny(components=[{"id": "a", "type": "warp_drive"}]))
    assert not r["ok"] and any("not in the part catalog" in e for e in r["errors"])
    r = V.expand_vehicle(tiny(components=[{"id": "a", "type": "shaft", "parameters": {"bogus_mm": 3}}]))
    assert not r["ok"] and any("planner" in e and "bogus_mm" in e for e in r["errors"])
    dup = tiny(components=[{"id": "a", "type": "shaft"}, {"id": "a", "type": "shaft"}])
    assert not V.expand_vehicle(dup)["ok"]
    r = V.expand_vehicle(tiny(assemblies={"ghost": {}}))
    assert not r["ok"] and any("ghost" in e for e in r["errors"])
    r = V.expand_vehicle(tiny(components=[{"id": "a", "type": "shaft", "position": ["=1/0", 0, 0]}]))
    assert not r["ok"] and any("division by zero" in e for e in r["errors"])


def test_check_with_bad_expression_is_an_error_not_a_pass():
    r = V.expand_vehicle(tiny(checks=[{"name": "c1", "expr": "=nope > 1"}, {"name": "c2", "expr": "w > 1"}]))
    st = {c["name"]: c["status"] for c in r["checks"]}
    assert st == {"c1": "error", "c2": "error"}


# --- mates in the spec ---------------------------------------------------------------------------------------------
def with_mates(post_mate, **post):
    return {"name": "m", "components": [
        {"id": "base", "type": "flat_plate", "fixed": True, "position": [0, 0, 0],
         "parameters": {"length_mm": 50, "width_mm": 30, "thickness_mm": 4}},
        {"id": "post", "type": "shaft", "parameters": {"diameter_mm": 4, "length_mm": 10}, "mate": post_mate, **post}]}


def test_mate_replaces_position_and_expressions_work_inside_it():
    s = with_mates({"type": "face", "to": "base", "face": "+Z", "gap": "=w_gap"})
    s["params"] = {"w_gap": 1.5}
    r = V.expand_vehicle(s)
    assert r["ok"], r["errors"]
    post = r["parts"][1]
    assert post["mate"]["gap"] == 1.5 and post["mate"]["to"] == "base"


def test_repeat_with_placeholder_targets_and_offsets():
    s = {"name": "m", "components": [
        {"id": "rail", "type": "shaft", "repeat": {"n": 2, "step": [0, 20, 0]}, "position": [0, 0, 0],
         "parameters": {"diameter_mm": 4, "length_mm": 40}},
        {"id": "clip", "type": "shaft", "repeat": {"n": 2, "step": [0, 0, 0]}, "parameters": {"diameter_mm": 6, "length_mm": 3},
         "mate": {"type": "coaxial", "to": "rail_{i}", "axis": "Z", "along": "min", "offset": "=i*5"}}]}
    r = V.expand_vehicle(s)
    assert r["ok"], r["errors"]
    m = {p["name"]: p["mate"] for p in r["parts"] if "mate" in p}
    assert m["clip_1"]["to"] == "rail_1" and m["clip_2"]["to"] == "rail_2" and m["clip_2"]["offset"] == 5.0


def test_mate_misuse_is_reported():
    both = with_mates({"type": "face", "to": "base", "face": "+Z"}, position=[0, 0, 0])
    assert any("not both" in e for e in V.expand_vehicle(both)["errors"])
    later = with_mates({"type": "face", "to": "ghost", "face": "+Z"})
    assert any("EARLIER" in e for e in V.expand_vehicle(later)["errors"])
    tilted = with_mates({"type": "face", "to": "base", "face": "+Z"}, rotation=[0, 30, 0])
    assert any("multiples of 90" in e for e in V.expand_vehicle(tilted)["errors"])      # the real solver limit
    stepped = with_mates({"type": "face", "to": "base", "face": "+Z"}, repeat={"n": 2, "step": [10, 0, 0]})
    assert any("no effect on a mated part" in e for e in V.expand_vehicle(stepped)["errors"])
    ext = {"name": "m", "components": [{"id": "x", "source": {"query": "bolt"}, "mate": {"type": "face", "to": "y", "face": "+Z"}}]}
    assert any("placed by position" in e for e in V.expand_vehicle(ext)["errors"])
