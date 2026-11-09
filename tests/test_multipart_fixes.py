"""Multi-part builds: per-solid verification, real-part exploded view, staged-vehicle guard, build order, bare yes.
Pure Python + numpy: none of these need the CAD kernel."""
import base64

import numpy as np

import codegen_chat as cg
import explode_mesh as EM
import geometry_verify as gv
import vehicle_chat as VC


def cube(x0, y0, z0, s=10.0):
    v = np.array([[x0 + dx * s, y0 + dy * s, z0 + dz * s] for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)], float)
    f = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    return v, f


def test_a_closed_cube_is_closed_and_an_open_one_is_not():
    v, f = cube(0, 0, 0)
    assert gv.mesh_is_closed(v, f)[0]
    ok, info = gv.mesh_is_closed(v, f[:-1])
    assert not ok and info["open_edges"] > 0


def test_touching_parts_pass_one_by_one():
    """The vehicle bug: parts that touch share edges once merged, so the merged mesh is never one closed shell."""
    v1, f1 = cube(0, 0, 0)
    v2, f2 = cube(10, 0, 0)                       # shares a face with the first
    merged_v = np.vstack([v1, v2]); merged_f = f1 + [tuple(i + 8 for i in t) for t in f2]
    assert gv.mesh_is_closed(v1, f1)[0] and gv.mesh_is_closed(v2, f2)[0]
    # merged into one mesh with their shared corners welded (what the STL check did), the pair is NOT a closed surface
    assert not gv.mesh_is_closed(merged_v, merged_f)[0]
    # each solid is fine on its own; verify_solids says so
    meshes = {"a": cube(0, 0, 0), "b": cube(10, 0, 0), "c": cube(5, 5, 5)}
    checks, errors, measured = gv.verify_solids([(n, n) for n in meshes], mesher=lambda n: meshes[n])
    assert checks["every_part_is_closed_solid"] and not errors and measured["parts_checked"] == 3


def test_a_broken_part_is_named():
    v, f = cube(0, 0, 0)
    meshes = {"good": (v, f), "bad": (v, f[:-2])}
    checks, errors, _ = gv.verify_solids([("good", "good"), ("bad", "bad")], mesher=lambda n: meshes[n])
    assert not checks["every_part_is_closed_solid"] and len(errors) == 1 and "'bad'" in errors[0]


def test_multi_part_skips_the_merged_watertight_test(tmp_path):
    p = tmp_path / "a.stl"; p.write_text("solid x\nendsolid x\n")
    c, e, m = gv._stl_watertight_check(p, require_watertight=False)
    assert "watertight" not in c and not [x for x in e if "watertight" in x]


def test_explode_uses_each_real_part():
    meshes = {"base": cube(0, 0, 0, 20), "lid": cube(0, 0, 20, 20), "pin": cube(8, 8, 20, 4)}
    out = EM.build(meshes, directions={"lid": (0, 0, 1), "pin": (0, 0, 1)}, fixed="base")
    assert out["ok"] and [p["name"] for p in out["parts"]] == ["base", "lid", "pin"]
    by = {p["name"]: p for p in out["parts"]}
    assert by["base"]["off"] == [0.0, 0.0, 0.0] and by["lid"]["off"][2] > 0
    assert all(p["tris"] == 12 and len(base64.b64decode(p["pos"])) == 12 * 9 * 4 for p in out["parts"])


def test_explode_needs_two_parts():
    assert not EM.build({"only": cube(0, 0, 0)})["ok"]


def spec(stage=None, **top):
    p = {"parts": [], **({"stage": stage} if stage else {})}
    return {"part_type": "assembly", "parameters": p, **top}


def test_steps_remaining_flags_partial_vehicles_and_assemblies_only():
    import chat_service as cs
    assert cs.steps_remaining(spec({"shown": 1, "of": 3})) == (1, 3)
    assert cs.steps_remaining(spec({"shown": 3, "of": 3})) is None
    assert cs.steps_remaining(spec(stage={"k": 2, "of": 4})) is None            # a parameters-level stage needs shown/of
    assert cs.steps_remaining({"part_type": "assembly", "parameters": {}, "stage": {"k": 2, "of": 4}}) == (2, 4)
    assert cs.steps_remaining({"part_type": "bracket", "parameters": {"stage": {"shown": 1, "of": 3}}}) is None
    assert cs.steps_remaining(None) is None


def test_running_gear_comes_before_body_and_doors_follow_the_body():
    ranks = {g: VC._group_rank(g) for g in ("wheels", "chassis", "body", "doors", "powertrain", "trim", "mystery")}
    order = sorted(ranks, key=ranks.get)
    assert order.index("chassis") < order.index("wheels") < order.index("body") <= order.index("doors") < order.index("powertrain") < order.index("trim")
    assert order.index("body") < order.index("mystery") < order.index("powertrain")      # unknown groups go with the body
    assert VC._group_rank("wheels", motor=False) > VC._group_rank("tub", motor=False)   # a bin is built tub first


def test_a_bare_yes_with_nothing_behind_it_asks_instead_of_inventing():
    assert cg.start_prompt("Yes write it as code", []) == ""
