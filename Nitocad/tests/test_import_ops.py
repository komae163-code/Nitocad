"""Edits to an attached file (import_ops.py): checking, wording, and the rule-based reader. Pure."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import import_ops as O  # noqa: E402

DXF = {"part_type": "imported", "parameters": {"import_id": "a" * 16, "kind": "dxf", "filename": "plate.dxf", "thickness_mm": 3.0, "edits": []}}
MESH = {"part_type": "imported", "parameters": {"import_id": "b" * 16, "kind": "stl", "filename": "m.stl", "edits": []}}


class TestCleaning:
    def test_good_edits_come_back_canonical(self):
        out = O.clean_edits([{"op": "HOLE", "diameter_mm": "6", "x": 10, "y": 20}, {"op": "fillet", "radius_mm": 2}, {"op": "mirror", "plane": "YZ"}], "step")
        assert out[0] == {"op": "hole", "diameter_mm": 6.0, "x": 10.0, "y": 20.0, "depth_mm": None, "face": "top"}
        assert out[1] == {"op": "fillet", "radius_mm": 2.0, "edges": "all"} and out[2]["plane"] == "yz"

    @pytest.mark.parametrize("bad,kind", [
        ([{"op": "hole", "diameter_mm": 6}], "stl"), ([{"op": "scale", "factor": 0}], "step"), ([{"op": "nope"}], "step"),
        ([{"op": "mirror"}], "step"), ([{"op": "rotate", "degrees": 999}], "step"), ("x", "step"), ([5], "step"),
        ([{"op": "fillet", "radius_mm": 2, "edges": "diagonal"}], "step"), ([{"op": "scale", "factor": "big"}], "step"),
        ([{"op": "scale", "factor": float("nan")}], "step"), ([{"op": "scale", "factor": 2}] * 41, "step"),
    ])
    def test_bad_edits_are_refused_with_a_message(self, bad, kind):
        with pytest.raises(ValueError):
            O.clean_edits(bad, kind)

    def test_meshes_only_take_moves(self):
        assert O.ops_for("stl") == O.MESH_OPS and "hole" not in O.MESH_OPS and "ground" in O.MESH_OPS
        assert [e["op"] for e in O.clean_edits([{"op": "ground"}, {"op": "center"}], "obj")] == ["ground", "center"]

    def test_params(self):
        out = O.clean_import_params({**DXF["parameters"], "thickness_mm": "5", "edits": [{"op": "scale", "factor": 2}]})
        assert out["thickness_mm"] == 5.0 and out["edits"] == [{"op": "scale", "factor": 2.0}]
        assert "thickness_mm" not in O.clean_import_params(MESH["parameters"])

    @pytest.mark.parametrize("bad", [
        {"import_id": "zz", "kind": "dxf"}, {"import_id": "a" * 16, "kind": "exe"}, {"import_id": "a" * 16, "kind": "dxf", "thickness_mm": 9999},
        {"kind": "dxf"}, {"import_id": "../../etc", "kind": "dxf"},
    ])
    def test_params_an_ai_must_not_get_wrong(self, bad):
        with pytest.raises(ValueError):
            O.clean_import_params(bad)

    def test_unknown_keys_are_dropped(self):
        assert "bbox_mm" not in O.clean_import_params({**DXF["parameters"], "bbox_mm": [1, 2, 3], "evil": "x"})


class TestWording:
    def test_every_edit_can_be_described(self):
        for e in O.clean_edits([{"op": "scale", "factor": 2}, {"op": "scale_to", "mm": 150}, {"op": "hole", "diameter_mm": 6, "x": 1, "y": 2},
                                {"op": "fillet", "radius_mm": 2}, {"op": "chamfer", "size_mm": 1}, {"op": "translate", "x": 1}, {"op": "rotate", "degrees": 90},
                                {"op": "mirror", "plane": "xy"}], "step"):
            assert O.describe_edit(e)
        assert O.describe_edit({"op": "ground"}) and O.describe_edit({"op": "center"})


def say(text, spec=DXF):
    new, changes = O.apply_import_edit_rules(text, spec)
    return new["parameters"], changes


class TestRules:
    @pytest.mark.parametrize("text,thickness", [("make it 5 mm thick", 5.0), ("thickness 8mm", 8.0), ("2 mm thick", 2.0), ("extrude it to 1.5 mm", 1.5)])
    def test_thickness_for_drawings(self, text, thickness):
        p, ch = say(text)
        assert p["thickness_mm"] == thickness and p["edits"] == [] and ch

    def test_thickness_does_not_apply_to_a_solid(self):
        step = {"part_type": "imported", "parameters": {"import_id": "c" * 16, "kind": "step", "filename": "a.step", "edits": []}}
        assert say("make it 5 mm thick", step)[1] == []

    @pytest.mark.parametrize("text,axis,mm", [("scale it to 150 mm long", "x", 150), ("make it 120 mm wide", "y", 120), ("make it 80 mm tall", "z", 80),
                                              ("scale to 15 cm", "largest", 150), ("resize to 2 in", "largest", 50.8)])
    def test_scale_to_a_size(self, text, axis, mm):
        e = say(text)[0]["edits"][-1]
        assert e["op"] == "scale_to" and e["axis"] == axis and e["mm"] == pytest.approx(mm)

    @pytest.mark.parametrize("text,factor", [("scale by 2", 2), ("scale it up by 1.5", 1.5), ("enlarge 3x", 3), ("scale to 50%", 0.5)])
    def test_scale_by_a_factor(self, text, factor):
        e = say(text)[0]["edits"][-1]
        assert e["op"] == "scale" and e["factor"] == pytest.approx(factor)

    def test_holes_need_a_position(self):
        assert say("a 6 mm hole")[1] == []
        e = say("add a 6 mm hole at x 10 y 20")[0]["edits"][-1]
        assert (e["op"], e["diameter_mm"], e["x"], e["y"], e["depth_mm"]) == ("hole", 6.0, 10.0, 20.0, None)
        assert say("add a 6mm hole at the center")[0]["edits"][-1]["x"] == 0
        e = say("add 8mm hole at (10, -5) 4mm deep")[0]["edits"][-1]
        assert (e["x"], e["y"], e["depth_mm"]) == (10.0, -5.0, 4.0)

    def test_fillet_and_chamfer_pick_the_edge_set(self):
        assert say("fillet the top edges 2mm")[0]["edits"][-1] == {"op": "fillet", "radius_mm": 2.0, "edges": "top"}
        assert say("chamfer 1 mm")[0]["edits"][-1] == {"op": "chamfer", "size_mm": 1.0, "edges": "all"}

    def test_rotate_move_mirror(self):
        assert say("rotate it 90 degrees about x")[0]["edits"][-1] == {"op": "rotate", "axis": "x", "degrees": 90.0}
        assert say("rotate it 45 degrees")[0]["edits"][-1]["axis"] == "z"
        assert say("move it 25 mm in x")[0]["edits"][-1]["x"] == 25.0
        assert say("mirror across yz")[0]["edits"][-1] == {"op": "mirror", "plane": "yz"}
        assert say("mirror it")[1] == []                         # which plane? the planner asks

    def test_undo_removes_the_last_edit(self):
        spec, _ = O.apply_import_edit_rules("add a 6mm hole at the center", DXF)
        spec, _ = O.apply_import_edit_rules("fillet 1 mm", spec)
        new, ch = O.apply_import_edit_rules("undo", spec)
        assert [e["op"] for e in new["parameters"]["edits"]] == ["hole"] and ch

    def test_undo_with_nothing_to_undo(self):
        assert say("undo")[1] == []

    def test_meshes_get_ground_and_center_but_no_holes(self):
        assert say("put it on the ground", MESH)[0]["edits"][-1] == {"op": "ground"}
        assert say("center it", MESH)[0]["edits"][-1] == {"op": "center"}
        assert say("add a 6mm hole at the center", MESH)[1] == []
        assert say("fillet 2 mm", MESH)[1] == []

    def test_nothing_changes_when_the_words_are_not_understood(self):
        p, ch = say("hello there")
        assert ch == [] and p["edits"] == [] and p["thickness_mm"] == 3.0

    def test_the_input_spec_is_not_modified(self):
        import copy
        before = copy.deepcopy(DXF)
        O.apply_import_edit_rules("scale by 2", DXF)
        assert DXF == before

    def test_edits_are_capped(self):
        spec = DXF
        for _ in range(45):
            spec, _ = O.apply_import_edit_rules("scale by 1.1", spec)
        assert len(spec["parameters"]["edits"]) == O.MAX_EDITS

    def test_whatever_the_rules_make_passes_the_checker(self):
        spec = DXF
        for t in ["add a 6mm hole at the center", "scale it to 150 mm long", "fillet 1 mm", "rotate it 90 degrees", "move it 5 mm in y", "make it 4 mm thick"]:
            spec, _ = O.apply_import_edit_rules(t, spec)
        assert O.clean_import_params(spec["parameters"])["thickness_mm"] == 4.0
