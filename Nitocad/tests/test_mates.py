"""Mate solver and validation against exact boxes. Pure Python, no CadQuery."""
import pytest

from assembly_planner import plan_assembly, validate_spec
from mates import MateError, is_quarter_turn, resolve_mates, rot_matrix, world_aabb

# exact local boxes (xmin, ymin, zmin, xmax, ymax, zmax) by part_type
BOXES = {
    "plate": (-50, -30, 0, 50, 30, 8),
    "block": (0, 0, 0, 20, 20, 10),
    "shaft": (-12.5, -12.5, 0, 12.5, 12.5, 60),
    "bearing": (-26, -26, 0, 26, 26, 15),
}


def bbox(part):
    return BOXES[part["part_type"]]


def part(name, ptype, **kw):
    p = {"name": name, "part_type": ptype, "parameters": {}, "position": (0.0, 0.0, 0.0), "rotation": (0.0, 0.0, 0.0)}
    p.update(kw)
    return p


def pos(parts, name):
    return next(p for p in parts if p["name"] == name)["position"]


class TestMath:
    def test_quarter_turns(self):
        assert is_quarter_turn((0, 90, -180)) and not is_quarter_turn((0, 45, 0))

    def test_rotation_matrix_is_exact_for_quarter_turns(self):
        assert rot_matrix(0, 0, 90) == [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
        assert rot_matrix(90, 0, 0) == [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]

    def test_convention_matches_assembly_rx_then_ry_then_rz(self):
        # a point on local +X, rotated rz=90 then rx=90: Rz sends x->y, Rx sends y->z
        r = rot_matrix(90, 0, 90)
        x_axis = [r[i][0] for i in range(3)]
        assert x_axis == [0.0, 0.0, 1.0]

    def test_world_aabb_rotates_and_translates(self):
        b = world_aabb((0, 0, 0, 10, 20, 30), (90, 0, 0))
        assert b == (0, -30, 0, 10, 0, 20)  # y and z swap, y flips
        assert world_aabb((0, 0, 0, 10, 20, 30), (90, 0, 0), (1, 2, 3)) == (1, -28, 3, 11, 2, 23)


class TestFaceMate:
    def test_block_on_plate_top_centered(self):
        parts = [part("base", "plate", position=(10.0, 20.0, 0.0)),
                 part("blk", "block", mate={"type": "face", "to": "base", "face": "+Z", "this_face": "-Z",
                                            "gap": 0.0, "align": {"x": "center", "y": "center", "z": "none"},
                                            "offset": [0.0, 0.0, 0.0]})]
        out = resolve_mates(parts, bbox)
        # block box center x=10 -> position x = 10 - 10; same for y; z sits on the plate top (8)
        assert pos(out, "blk") == (0.0, 10.0, 8.0)

    def test_gap_and_alignment_modes_and_offset(self):
        base = part("base", "plate")
        mate = {"type": "face", "to": "base", "face": "+Z", "this_face": "-Z", "gap": 2.0,
                "align": {"x": "min", "y": "max", "z": "none"}, "offset": [5.0, -3.0, 0.0]}
        out = resolve_mates([base, part("blk", "block", mate=mate)], bbox)
        # min x: plate xmin -50 - block xmin 0 = -50, +5 offset; max y: 30 - 20 = 10, -3 offset; z = 8 + gap 2
        assert pos(out, "blk") == (-45.0, 7.0, 10.0)

    def test_flush_top_faces_with_same_sign_this_face(self):
        mate = {"type": "face", "to": "base", "face": "+Z", "this_face": "+Z", "gap": 0.0,
                "align": {"x": "center", "y": "center", "z": "none"}, "offset": [0.0, 0.0, 0.0]}
        out = resolve_mates([part("base", "plate"), part("blk", "block", mate=mate)], bbox)
        assert pos(out, "blk")[2] == 8 - 10  # block top flush with plate top

    def test_negative_face_hangs_below(self):
        mate = {"type": "face", "to": "base", "face": "-Z", "this_face": "+Z", "gap": 1.0,
                "align": {"x": "center", "y": "center", "z": "none"}, "offset": [0.0, 0.0, 0.0]}
        out = resolve_mates([part("base", "plate"), part("blk", "block", mate=mate)], bbox)
        assert pos(out, "blk")[2] == (0 - 1.0) - 10  # block top 1 mm under the plate bottom

    def test_rotated_part_uses_its_rotated_box(self):
        mate = {"type": "face", "to": "base", "face": "+Z", "this_face": "-Z", "gap": 0.0,
                "align": {"x": "center", "y": "center", "z": "none"}, "offset": [0.0, 0.0, 0.0]}
        blk = part("blk", "block", rotation=(90.0, 0.0, 0.0), mate=mate)  # rotated: z extent 0..20 after swap
        out = resolve_mates([part("base", "plate"), blk], bbox)
        final = world_aabb(BOXES["block"], (90, 0, 0), pos(out, "blk"))
        assert final[2] == 8.0  # bottom face rests exactly on the plate top


class TestCoaxial:
    def test_bearing_on_shaft_at_20_from_the_end(self):
        shaft = part("shaft", "shaft", position=(5.0, 6.0, 0.0))
        mate = {"type": "coaxial", "to": "shaft", "axis": "Z", "along": "min", "offset": 20.0}
        out = resolve_mates([shaft, part("brg", "bearing", mate=mate)], bbox)
        assert pos(out, "brg") == (5.0, 6.0, 20.0)

    def test_along_max_and_center(self):
        shaft = part("shaft", "shaft")
        out = resolve_mates([shaft, part("b1", "bearing", mate={"type": "coaxial", "to": "shaft", "axis": "Z",
                                                                 "along": "max", "offset": 0.0})], bbox)
        assert pos(out, "b1")[2] == 60 - 15
        out = resolve_mates([shaft, part("b2", "bearing", mate={"type": "coaxial", "to": "shaft", "axis": "Z",
                                                                 "along": "center", "offset": 0.0})], bbox)
        assert pos(out, "b2")[2] == 30 - 7.5

    def test_chain_mates_follow_solved_positions(self):
        parts = [part("base", "plate", position=(100.0, 0.0, 0.0)),
                 part("shaft", "shaft", mate={"type": "face", "to": "base", "face": "+Z", "this_face": "-Z", "gap": 0.0,
                                              "align": {"x": "center", "y": "center", "z": "none"}, "offset": [0.0, 0.0, 0.0]}),
                 part("brg", "bearing", mate={"type": "coaxial", "to": "shaft", "axis": "Z", "along": "min", "offset": 10.0})]
        out = resolve_mates(parts, bbox)
        assert pos(out, "shaft") == (100.0, 0.0, 8.0) and pos(out, "brg") == (100.0, 0.0, 18.0)

    def test_unplaced_target_raises(self):
        with pytest.raises(MateError):
            resolve_mates([part("brg", "bearing", mate={"type": "coaxial", "to": "ghost", "axis": "Z",
                                                        "along": "min", "offset": 0.0})], bbox)


def spec(*parts):
    return {"parts": list(parts)}


def raw(name, ptype="plate", **kw):
    p = {"name": name, "part_type": ptype, "parameters": {}}
    p.update(kw)
    return p


class TestValidation:
    def ok(self, **mate):
        base = {"type": "face", "to": "base", "face": "+Z"}
        base.update(mate)
        return validate_spec(spec(raw("base", "flat_plate"), raw("top", "flat_plate", mate=base)))

    def test_valid_mate_is_normalized_with_defaults(self):
        parts, errors = self.ok()
        assert errors == []
        m = parts[1]["mate"]
        assert m["this_face"] == "-Z" and m["gap"] == 0.0 and m["align"] == {"x": "center", "y": "center", "z": "none"}

    def test_valid_coaxial(self):
        parts, errors = validate_spec(spec(raw("shaft", "shaft"),
                                           raw("brg", "std_bearing", mate={"type": "coaxial", "to": "shaft", "axis": "Z",
                                                                           "along": "min", "offset": 20})))
        assert errors == [] and parts[1]["mate"]["offset"] == 20.0

    @pytest.mark.parametrize("bad", [
        {"to": "ghost"}, {"face": "+W"}, {"this_face": "+X"}, {"gap": float("nan")}, {"gap": "2"},
        {"align": {"z": "min"}}, {"align": {"x": "left"}}, {"offset": [0, 0, 5]}, {"type": "glue"}, {"color": "red"},
    ])
    def test_rejects_bad_mates(self, bad):
        assert self.ok(**bad)[1]

    def test_forward_and_self_references_are_rejected(self):
        a = raw("a", "flat_plate", mate={"type": "face", "to": "b", "face": "+Z"})
        assert validate_spec(spec(a, raw("b", "flat_plate")))[1]
        assert validate_spec(spec(raw("a", "flat_plate", mate={"type": "face", "to": "a", "face": "+Z"})))[1]

    def test_position_and_mate_together_rejected(self):
        p = raw("top", "flat_plate", position=[0, 0, 0], mate={"type": "face", "to": "base", "face": "+Z"})
        assert any("either position or mate" in e for e in validate_spec(spec(raw("base", "flat_plate"), p))[1])

    def test_mates_require_quarter_turn_rotations(self):
        p = raw("top", "flat_plate", rotation=[0, 0, 45], mate={"type": "face", "to": "base", "face": "+Z"})
        assert any("multiples of 90" in e for e in validate_spec(spec(raw("base", "flat_plate"), p))[1])
        base = raw("base", "flat_plate", rotation=[0, 30, 0])
        q = raw("top", "flat_plate", mate={"type": "face", "to": "base", "face": "+Z"})
        assert any("multiples of 90" in e for e in validate_spec(spec(base, q))[1])

    def test_retry_loop_feeds_back_mate_errors(self):
        seen = []

        def llm(system, user):
            seen.append(user)
            face = "+W" if len(seen) == 1 else "+Z"
            return spec(raw("base", "flat_plate"), raw("top", "flat_plate", mate={"type": "face", "to": "base", "face": face}))

        assert plan_assembly("stack two plates", call_llm=llm) and "face must be one of" in seen[1]
