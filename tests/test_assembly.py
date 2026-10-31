"""
Pure-Python tests (no CadQuery) for interface-driven assemblies: the mate
solver, every compatibility rule, placement of the wheel-corner module,
dependency graph / change impact, and the assembly parser (LLM mocked).
Real-solid overlap is covered by test_assembly_geometry.py (requires_cadquery).
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assembly_parser as AP
import assembly_spec as AS
import assembly_templates as AT
import domain_packs
import mating as M
from exceptions import ParseError


def corner(**per_part):
    spec = AT.corner_module()
    for p in spec["parts"]:
        p["parameters"].update(per_part.get(p["id"], {}))
    return spec


def codes(res, severity=None):
    return {f.code for f in res.findings if severity is None or f.severity == severity}


# ---------------------------------------------------------------- mating maths

class TestSolver:
    def test_frame_orthogonalises_ref_and_normalises_axis(self):
        f = M.Frame((0, 0, 0), (0, 0, 5), (1, 0, 3))
        assert f.axis == (0.0, 0.0, 1.0)
        assert M.dot(f.ref, f.axis) == pytest.approx(0.0, abs=1e-12)

    def test_frame_with_parallel_ref_gets_a_valid_one(self):
        f = M.Frame((0, 0, 0), (0, 0, 1), (0, 0, 1))
        assert M.dot(f.ref, f.axis) == pytest.approx(0.0, abs=1e-12)
        assert M.norm(f.ref) == pytest.approx(1.0)

    def test_solved_transform_is_a_proper_rotation(self):
        a = M.Frame((10, 20, 30), (0.3, -0.5, 0.8), (1, 0, 0))
        b = M.Frame((-4, 7, 1), (0, -1, 0), (0, 0, 1))
        t = M.solve_mate(a, b, 2.5)
        det = M.dot(M.col(t.R, 0), M.cross(M.col(t.R, 1), M.col(t.R, 2)))
        assert det == pytest.approx(1.0, abs=1e-9)
        for j in range(3):
            assert M.norm(M.col(t.R, j)) == pytest.approx(1.0, abs=1e-9)

    @pytest.mark.parametrize("gap", [0.0, 3.0, 12.5])
    def test_mated_frames_coincide_face_each_other_and_are_clocked(self, gap):
        a = M.Frame((10, 20, 30), (0.3, -0.5, 0.8), (1, 0, 0))
        b = M.Frame((-4, 7, 1), (0, -1, 0), (0, 0, 1))
        t = M.solve_mate(a, b, gap)
        bw = b.transformed(t)
        want = M.add(a.origin, M.scale(a.axis, gap))
        assert M.norm(M.sub(bw.origin, want)) == pytest.approx(0.0, abs=1e-9)
        assert M.dot(a.axis, bw.axis) == pytest.approx(-1.0, abs=1e-9)
        assert M.dot(a.ref, bw.ref) == pytest.approx(1.0, abs=1e-9)
        assert M.verify_mate_world(a, bw, gap) == []

    def test_verify_flags_a_disagreeing_loop(self):
        a = M.Frame((0, 0, 0), (0, 0, 1))
        b = M.Frame((0, 0, 0), (0, 0, -1))
        assert M.verify_mate_world(a, b, 0.0) == []
        assert {f.code for f in M.verify_mate_world(a, b, 5.0)} == {"mate_position_mismatch"}
        assert "mate_axis_mismatch" in {f.code for f in M.verify_mate_world(a, M.Frame((0, 0, 0), (0, 0, 1)), 0.0)}

    def test_explicit_pose_composition_matches_legacy_order(self):
        t = M.transform_from_pose((1, 2, 3), (90, 0, 0))
        assert t.point((0, 1, 0)) == pytest.approx((1, 2, 4), abs=1e-9)


# ---------------------------------------------------------------- rules

def itf(kind, **data):
    return M.Interface("x", kind, M.Frame((0, 0, 0), (0, 0, 1)), data)


class TestRules:
    def test_unknown_pair_is_an_error_not_a_pass(self):
        f, _ = M.check_pair(itf("pin", diameter_mm=10), itf("face", inner_radius_mm=1, outer_radius_mm=20), "a", "b")
        assert [x.code for x in f] == ["incompatible_interfaces"]

    @pytest.mark.parametrize("male,female,expected", [
        (66.1, 66.1, set()),                      # line-to-line: fine
        (66.1, 66.4, set()),                      # 0.3mm clearance: locating band
        (66.1, 66.8, {"sloppy_fit"}),             # 0.7mm: warn
        (66.1, 67.5, {"loose_fit"}),              # 1.4mm: error
        (66.1, 65.9, {"interference_fit"}),       # male bigger: cannot assemble
    ])
    def test_pilot_fit_bands(self, male, female, expected):
        f, _ = M.check_pair(itf("pilot_boss", diameter_mm=male, height_mm=12),
                            itf("pilot_bore", diameter_mm=female, depth_mm=8), "a", "b")
        assert {x.code for x in f} == expected

    def test_rule_is_order_independent_and_flips_couplings(self):
        boss, bore = itf("pilot_boss", diameter_mm=66.1), itf("pilot_bore", diameter_mm=70.0)
        f1, c1 = M.check_pair(boss, bore, "a", "b")
        f2, c2 = M.check_pair(bore, boss, "b", "a")
        assert {x.code for x in f1} == {x.code for x in f2} == {"loose_fit"}
        assert c1 and c2

    def test_pilot_shorter_than_plate_warns(self):
        f, _ = M.check_pair(itf("pilot_boss", diameter_mm=66, height_mm=6),
                            itf("pilot_bore", diameter_mm=66, depth_mm=8), "a", "b")
        assert {x.code for x in f} == {"pilot_too_short"}

    def test_bolt_patterns(self):
        base = dict(count=5, pcd_mm=114.3, hole_dia_mm=12, role="press", phase_deg=0)
        other = dict(count=5, pcd_mm=114.3, hole_dia_mm=13, role="clearance", phase_deg=0)
        ok = lambda **kw: {x.code for x in M.check_pair(itf("bolt_pattern", **base), itf("bolt_pattern", **{**other, **kw}), "a", "b")[0]}
        assert ok() == set()
        assert ok(count=4) == {"bolt_count_mismatch"}
        assert ok(pcd_mm=114.0) == {"bolt_pcd_tolerance"}
        assert ok(pcd_mm=120.0) == {"bolt_pcd_mismatch"}
        assert ok(hole_dia_mm=12.0) == {"stud_clearance"}       # clearance hole not bigger than the stud

    def test_bolt_half_pitch_phase_is_rejected(self):
        a = dict(count=5, pcd_mm=114.3, hole_dia_mm=12, role="press", phase_deg=0)
        b = dict(count=5, pcd_mm=114.3, hole_dia_mm=13, role="clearance", phase_deg=36.0)
        f, _ = M.check_pair(itf("bolt_pattern", **a), itf("bolt_pattern", **b), "a", "b")
        assert "bolt_phase_mismatch" in {x.code for x in f}

    def test_bolt_full_pitch_phase_is_accepted(self):
        a = dict(count=5, pcd_mm=114.3, hole_dia_mm=12, role="press", phase_deg=0)
        b = dict(count=5, pcd_mm=114.3, hole_dia_mm=13, role="clearance", phase_deg=72.0)
        f, _ = M.check_pair(itf("bolt_pattern", **a), itf("bolt_pattern", **b), "a", "b")
        assert "bolt_phase_mismatch" not in {x.code for x in f}

    def test_face_contact_and_bolts_on_face(self):
        mk = lambda **kw: itf("face", inner_radius_mm=33, outer_radius_mm=70, bolt_pcd_mm=114.3, bolt_hole_dia_mm=12, **kw)
        ok = lambda a, b: {x.code for x in M.check_pair(a, b, "a", "b")[0]}
        assert ok(mk(), mk()) == set()
        thin = itf("face", inner_radius_mm=33, outer_radius_mm=36)
        assert "contact_area" in ok(mk(), thin)
        narrow = itf("face", inner_radius_mm=33, outer_radius_mm=50)
        assert "bolts_off_contact" in ok(mk(), narrow)

    def test_body_must_fit_in_cavity(self):
        body = itf("face", inner_radius_mm=33, outer_radius_mm=70, body_radius_mm=70)
        tight = itf("face", inner_radius_mm=33, outer_radius_mm=75, cavity_radius_mm=70)
        roomy = itf("face", inner_radius_mm=33, outer_radius_mm=75, cavity_radius_mm=75)
        assert "body_in_cavity" in {x.code for x in M.check_pair(body, tight, "a", "b")[0]}
        assert "body_in_cavity" not in {x.code for x in M.check_pair(body, roomy, "a", "b")[0]}


# ---------------------------------------------------------------- the corner module

class TestCornerModule:
    def test_defaults_resolve_clean(self):
        res = AS.resolve_assembly(AT.corner_module())
        assert res.ok, [f.message for f in res.errors]
        assert not res.warnings, [f.message for f in res.warnings]
        assert res.order == ["subframe", "arm", "ball_joint", "knuckle", "hub", "rotor"]

    def test_only_open_interfaces_are_the_unmated_right_side_and_body_mounts(self):
        res = AS.resolve_assembly(AT.corner_module())
        opens = {f.a for f in res.findings if f.code == "open_interface"}
        assert opens == {"subframe.right_pivot_front", "subframe.right_pivot_rear",
                         "subframe.body_mount_front_left", "subframe.body_mount_front_right",
                         "subframe.body_mount_rear_left", "subframe.body_mount_rear_right"}

    def test_placements_match_the_hand_derivation(self):
        res = AS.resolve_assembly(AT.corner_module())
        t = {pid: p.placement for pid, p in res.parts.items()}
        py, arm_len, boss_h, flange, drop, bore_gap = 350.0, 380.0, 22.0, 8.0, 110.0, 3.0
        # subframe is the root (vehicle frame); the arm hangs between its left clevises
        assert t["subframe"].t == (0.0, 0.0, 0.0)
        assert t["arm"].t == pytest.approx((0.0, py, 0.0), abs=1e-9)
        # ball joint: flange underside on the arm boss top
        assert t["ball_joint"].t == pytest.approx((0.0, py + arm_len, boss_h / 2), abs=1e-9)
        # knuckle: seat face on the ball-joint flange top (+8); wheel axis runs outboard (+Y)
        assert t["knuckle"].t == pytest.approx((0.0, py + arm_len + 47.0, boss_h / 2 + flange + drop), abs=1e-6)
        assert M.col(t["knuckle"].R, 2) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)    # local Z -> world +Y (outboard)
        assert M.col(t["knuckle"].R, 1) == pytest.approx((0.0, 0.0, 1.0), abs=1e-9)    # local Y (up) -> world +Z
        # hub: flange face is `flange_gap` outboard of the knuckle's bore plane
        assert t["hub"].t[1] == pytest.approx(t["knuckle"].t[1] + bore_gap, abs=1e-6)
        # rotor plate seats on the hub flange: rotor origin = hub origin + flange - (hat_offset - plate)
        assert t["rotor"].t[1] == pytest.approx(t["hub"].t[1] + (12.0 - (40.0 - 8.0)), abs=1e-6)
        # wheel axis is shared and horizontal
        for pid in ("hub", "rotor"):
            assert M.col(t[pid].R, 2) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)

    def test_wheel_centre_is_the_same_for_hub_and_rotor(self):
        res = AS.resolve_assembly(AT.corner_module())
        hub, rot = res.parts["hub"].placement, res.parts["rotor"].placement
        assert hub.t[0] == pytest.approx(rot.t[0]) and hub.t[2] == pytest.approx(rot.t[2])

    def test_closing_mates_are_verified_not_used_for_placement(self):
        res = AS.resolve_assembly(AT.corner_module())
        assert res.parts["rotor"].placed_by == "mate:5"
        assert "mate_position_mismatch" not in codes(res)

    @pytest.mark.parametrize("variant", [
        dict(hub={"lobed": True}),
        dict(rotor={"vented": False, "outer_diameter_mm": 330}),
        dict(arm={"arm_length_mm": 450}),
        dict(hub={"bolt_count": 6, "bolt_pcd_mm": 139.7, "flange_diameter_mm": 170}, rotor={"bolt_count": 6, "bolt_pcd_mm": 139.7, "hat_diameter_mm": 190}),
    ])
    def test_valid_variants_stay_clean(self, variant):
        res = AS.resolve_assembly(corner(**variant))
        assert res.ok, [f.message for f in res.errors]

    @pytest.mark.parametrize("name,per_part,expected", [
        ("hub PCD differs", dict(hub={"bolt_pcd_mm": 120.0, "flange_diameter_mm": 170.0}), "bolt_pcd_mismatch"),
        ("stud count differs", dict(rotor={"bolt_count": 4}), "bolt_count_mismatch"),
        ("rotor bore too loose", dict(rotor={"center_bore_mm": 70.0}), "loose_fit"),
        ("rotor bore too small", dict(rotor={"center_bore_mm": 64.0}), "interference_fit"),
        ("barrel bigger than bearing bore", dict(hub={"barrel_diameter_mm": 90.0}), "interference_fit"),
        ("ball joint hole too big", dict(arm={"ball_joint_hole_diameter_mm": 34.0}), "loose_fit"),
        ("flange won't fit in the hat", dict(hub={"flange_diameter_mm": 170.0, "bolt_pcd_mm": 114.3}), "body_in_cavity"),
    ])
    def test_incompatible_combinations_are_blocked(self, name, per_part, expected):
        res = AS.resolve_assembly(corner(**per_part))
        assert not res.ok, name
        assert expected in codes(res, "error"), (name, [f.code for f in res.errors])

    def test_short_pilot_is_a_warning_not_an_error(self):
        res = AS.resolve_assembly(corner(hub={"pilot_height_mm": 6.0}))
        assert res.ok and "pilot_too_short" in codes(res, "warning")

    def test_invalid_part_reports_the_cause_once_without_knock_on_noise(self):
        res = AS.resolve_assembly(corner(rotor={"hat_diameter_mm": 140.0}))
        assert [f.code for f in res.errors] == ["part_invalid"]

    def test_a_loop_of_mates_that_disagrees_is_caught(self):
        spec = AT.corner_module()
        spec["mates"][6]["gap_mm"] = 5.0          # hub.rotor_pilot <-> rotor.hub_bore (a closing mate)
        assert "mate_position_mismatch" in codes(AS.resolve_assembly(spec), "error")

    def test_corrections_are_reported_and_stored_in_the_resolved_spec(self):
        spec = corner(rotor={"vane_thickness_mm": 12.0, "vane_count": 72})
        res = AS.resolve_assembly(spec)
        assert res.parts["rotor"].corrections            # vane count was auto-reduced
        stored = res.resolved_spec()
        assert next(p for p in stored["parts"] if p["id"] == "rotor")["parameters"]["vane_count"] < 72
        again = AS.resolve_assembly(stored)
        assert again.ok and not again.parts["rotor"].corrections       # stable


class TestStructure:
    @pytest.mark.parametrize("bad", [
        None, [], {}, {"parts": []}, {"parts": "x"},
        {"parts": [{"id": "A", "part_type": "piston"}]},
        {"parts": [{"id": "a", "part_type": "piston"}, {"id": "a", "part_type": "piston"}]},
        {"parts": [{"id": "a", "part_type": "piston"}], "root": "zzz"},
        {"parts": [{"id": "a", "part_type": "piston"}], "mates": [{"a": 1}]},
        {"parts": [{"id": "a", "part_type": "piston", "placement": {"position": [1, 2]}}]},
    ])
    def test_malformed_specs_raise(self, bad):
        with pytest.raises(AS.AssemblySpecError):
            AS.resolve_assembly(bad)

    def test_no_part_or_mate_cap_by_default(self):
        assert AS.MAX_PARTS is None and AS.MAX_MATES is None
        spec = {"parts": [{"id": "p%d" % i, "part_type": "piston"} for i in range(300)]}
        assert AS.resolve_assembly(spec) is not None

    def test_part_cap_can_still_be_switched_on(self, monkeypatch):
        monkeypatch.setattr(AS, "MAX_PARTS", 3)
        spec = {"parts": [{"id": "p%d" % i, "part_type": "piston"} for i in range(4)]}
        with pytest.raises(AS.AssemblySpecError):
            AS.resolve_assembly(spec)

    def test_unknown_part_type_and_interface_and_part_are_findings(self):
        spec = {"parts": [{"id": "a", "part_type": "warp_drive"}, {"id": "arm", "part_type": "control_arm"}],
                "mates": [{"a": "arm.nope", "b": "ghost.x"}, {"a": "arm", "b": "arm.ball_joint_socket"}]}
        c = codes(AS.resolve_assembly(spec), "error")
        assert {"unknown_part_type", "unknown_interface", "unknown_part", "bad_mate_reference"} <= c

    def test_part_without_interfaces_needs_an_explicit_placement(self):
        spec = {"parts": [{"id": "arm", "part_type": "control_arm"}, {"id": "p", "part_type": "piston"}],
                "mates": [{"a": "arm.ball_joint_socket", "b": "p.x"}]}
        assert "no_interfaces" in codes(AS.resolve_assembly(spec), "error")

    def test_explicit_placement_is_honoured_and_unconnected_part_is_flagged(self):
        spec = {"parts": [{"id": "arm", "part_type": "control_arm"},
                          {"id": "p", "part_type": "piston", "placement": {"position": [10, 20, 30], "rotation": [0, 0, 90]}},
                          {"id": "q", "part_type": "flywheel"}]}
        res = AS.resolve_assembly(spec)
        assert res.parts["p"].placement.t == (10.0, 20.0, 30.0) and res.parts["p"].placed_by == "explicit"
        assert [f.a for f in res.errors] == ["q"] and res.errors[0].code == "unplaced_part"

    def test_self_mate_is_rejected(self):
        spec = {"parts": [{"id": "hub", "part_type": "wheel_hub"}], "mates": [{"a": "hub.rotor_face", "b": "hub.stud_pattern"}]}
        assert "self_mate" in codes(AS.resolve_assembly(spec), "error")

    def test_single_part_assembly_is_valid(self):
        res = AS.resolve_assembly({"parts": [{"id": "hub", "part_type": "wheel_hub"}]})
        assert res.ok and res.order == ["hub"]


# ---------------------------------------------------------------- dependency graph + impact

class TestDependencies:
    def test_shared_parameters_are_coupled_in_the_graph(self):
        g = AS.resolve_assembly(AT.corner_module()).graph
        assert "rotor.bolt_pcd_mm" in g.affected_by(["hub.bolt_pcd_mm"])
        assert "hub.pilot_diameter_mm" in g.affected_by(["rotor.center_bore_mm"])
        assert "knuckle.bearing_bore_diameter_mm" in g.affected_by(["hub.barrel_diameter_mm"])
        assert "ball_joint.housing_diameter_mm" in g.affected_by(["arm.ball_joint_hole_diameter_mm"])
        assert "knuckle.seat_diameter_mm" in g.affected_by(["ball_joint.stud_base_diameter_mm"])
        assert "subframe.clevis_gap_mm" in g.affected_by(["arm.bushing_length_mm"])

    def test_placement_chain_runs_root_to_leaf(self):
        g = AS.resolve_assembly(AT.corner_module()).graph
        down = g.affected_by(["arm.arm_length_mm"])
        assert {"ball_joint.@placement", "knuckle.@placement", "hub.@placement", "rotor.@placement"} <= set(down)
        assert "arm.@placement" not in down

    def test_graph_serialises(self):
        d = AS.resolve_assembly(AT.corner_module()).graph.to_dict()
        json.dumps(d)
        assert d["edges"] and all({"from", "to", "relation", "mate", "directed"} <= set(e) for e in d["edges"])


class TestImpact:
    def test_arm_length_moves_everything_downstream_and_breaks_nothing(self):
        r = AS.analyze_change(AT.corner_module(), "arm", {"arm_length_mm": 420.0})
        assert r["ok_before"] and r["ok_after"]
        assert {m["part"] for m in r["moved_parts"]} == {"ball_joint", "knuckle", "hub", "rotor"}
        assert all(m["translation_mm"] == pytest.approx([0.0, 40.0, 0.0], abs=1e-6) for m in r["moved_parts"])
        assert r["new_findings"] == []

    def test_hub_flange_thickness_moves_only_the_rotor(self):
        r = AS.analyze_change(AT.corner_module(), "hub", {"flange_thickness_mm": 15.0})
        assert [m["part"] for m in r["moved_parts"]] == ["rotor"]
        assert r["moved_parts"][0]["translation_mm"] == pytest.approx([0.0, 3.0, 0.0], abs=1e-6)

    def test_changing_one_side_of_a_mate_flags_the_other(self):
        r = AS.analyze_change(AT.corner_module(), "hub", {"bolt_pcd_mm": 120.0, "flange_diameter_mm": 170.0})
        assert not r["ok_after"]
        assert "rotor.bolt_pcd_mm" in r["affected_nodes"]
        assert "bolt_pcd_mismatch" in {f["code"] for f in r["new_findings"]}
        assert "rotor" in r["affected_parts"]

    def test_a_fix_shows_up_as_resolved(self):
        broken = corner(rotor={"center_bore_mm": 70.0})
        r = AS.analyze_change(broken, "rotor", {"center_bore_mm": 66.1})
        assert not r["ok_before"] and r["ok_after"]
        assert "loose_fit" in {f["code"] for f in r["resolved_findings"]}

    def test_bad_inputs(self):
        with pytest.raises(AS.AssemblySpecError):
            AS.analyze_change(AT.corner_module(), "nope", {"x": 1})
        with pytest.raises(AS.AssemblySpecError):
            AS.analyze_change(AT.corner_module(), "hub", {})


# ---------------------------------------------------------------- catalog + parser

class TestCatalog:
    def test_interface_catalog_lists_the_mating_parts(self):
        cat = domain_packs.interface_catalog()
        assert set(cat) == {"brake_rotor", "wheel_hub", "control_arm", "steering_knuckle", "ball_joint", "subframe"}

    def test_every_interface_kind_in_the_catalog_has_a_rule_or_is_open_only(self):
        kinds = {i["kind"] for items in domain_packs.interface_catalog().values() for i in items}
        assert kinds <= (M.known_kinds() | M.OPEN_ONLY_KINDS)

    def test_every_interface_frame_is_well_formed_at_defaults(self):
        for part in domain_packs.interface_catalog():
            for i in domain_packs.part_interfaces(part, {}):
                assert M.norm(i.frame.axis) == pytest.approx(1.0)
                assert abs(M.dot(i.frame.axis, i.frame.ref)) < 1e-9

    def test_interface_sources_name_real_parameters(self):
        specs = {}
        for pk in domain_packs.get_packs():
            specs.update(pk.param_specs)
        for part in domain_packs.interface_catalog():
            for i in domain_packs.part_interfaces(part, {}):
                for key, src in {**i.sources, **{f: f for f in i.frame_sources}}.items():
                    for name in src.split(","):
                        assert name.strip() in specs[part], f"{part}.{i.name}: {name!r} is not a parameter"


class TestParser:
    @pytest.mark.parametrize("text,expected", [
        ("wheel corner module with 5x114.3 rotor", True),
        ("suspension corner assembly", True),
        ("assemble the hub and rotor together", True),
        ("brake rotor 300mm", False),
    ])
    def test_is_assembly_request(self, text, expected):
        assert AP.is_assembly_request(text) is expected

    def test_named_corner_needs_no_llm_and_applies_shared_overrides(self):
        r = AP.parse_assembly_request("front wheel corner module, 330mm solid rotor, 6x139.7, lobed hub, 420mm arm")
        assert r.parser == "template" and r.model is None
        by = {p["id"]: p["parameters"] for p in r.spec["parts"]}
        for pid in ("hub", "rotor"):                       # one pattern, applied to BOTH sides of the mate
            assert (by[pid]["bolt_count"], by[pid]["bolt_pcd_mm"]) == (6, 139.7)
        assert by["rotor"]["outer_diameter_mm"] == 330.0 and by["rotor"]["vented"] is False
        assert by["hub"]["lobed"] is True and by["arm"]["arm_length_mm"] == 420.0

    def test_template_parse_is_deterministic(self):
        a = AP.parse_assembly_request("wheel corner module 5x114.3")
        b = AP.parse_assembly_request("wheel corner module 5x114.3")
        assert a.parse_hash == b.parse_hash

    def test_custom_assembly_without_llm_is_an_honest_error(self):
        with pytest.raises(ParseError) as e:
            AP.parse_assembly_request("assemble a custom gearbox from a shaft and two gears")
        assert "AI parser" in str(e.value)

    def test_system_prompt_lists_every_interface_and_the_compatibility_table(self):
        p = AP.build_system_prompt()
        for part, items in domain_packs.interface_catalog().items():
            assert f"- {part}:" in p
            for i in items:
                assert i["name"] in p
        for a, b in M.rule_pairs():
            assert f"{a} <-> {b}" in p
        assert "bolt_pattern <-> bolt_pattern" in p

    def _llm(self, *answers):
        seen = []
        it = iter(answers)
        def call(system, user):
            seen.append(user)
            return next(it)
        call.seen = seen
        return call

    def test_valid_llm_answer_is_accepted(self):
        good = json.dumps(AT.corner_module())
        r = AP.parse_assembly_request("custom hub and rotor assembly", llm_call=self._llm(good))
        assert r.parser == "deepseek" and r.retry_count == 0

    def test_invalid_answer_is_retried_with_the_exact_failures_then_accepted(self):
        bad = AT.corner_module()
        bad["mates"][0] = {"a": "arm.ball_joint_socket", "b": "knuckle.warp_core"}
        call = self._llm(json.dumps(bad), json.dumps(AT.corner_module()))
        r = AP.parse_assembly_request("custom hub and rotor assembly", llm_call=call)
        assert r.retry_count == 1 and any("warp_core" in u or "unknown_interface" in u for u in call.seen[1:])

    def test_two_bad_answers_raise_parse_error_with_reasons(self):
        bad = AT.corner_module()
        next(p for p in bad["parts"] if p["id"] == "rotor")["parameters"]["center_bore_mm"] = 70.0
        call = self._llm(json.dumps(bad), json.dumps(bad))
        with pytest.raises(ParseError) as e:
            AP.parse_assembly_request("custom hub and rotor assembly", llm_call=call)
        assert any("loose_fit" in x for x in e.value.details["assembly_errors"])

    def test_non_json_answer_counts_as_a_failed_attempt(self):
        call = self._llm("not json at all", json.dumps(AT.corner_module()))
        r = AP.parse_assembly_request("custom hub and rotor assembly", llm_call=call)
        assert r.retry_count == 1

    def test_llm_cannot_smuggle_an_invented_part_type(self):
        spec = {"parts": [{"id": "x", "part_type": "flux_capacitor"}]}
        call = self._llm(json.dumps(spec), json.dumps(spec))
        with pytest.raises(ParseError):
            AP.parse_assembly_request("custom assembly of something", llm_call=call)
