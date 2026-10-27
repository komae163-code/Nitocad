"""
Pure-Python tests for the automotive domain pack (no CadQuery needed): the
registry, parameter coercion, every part's validator, the deterministic
parser hooks, and the guarantee that the pack never changes how existing
part types are routed. Geometry is covered separately in
test_automotive_geometry.py (requires_cadquery).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import domain_packs
from domain_packs.automotive.standards import COMMON_BOLT_PATTERNS, parse_bolt_pattern
from smart_parser import infer_part_type, parse_description
from validator import validate_parameters

PARTS = ["brake_rotor", "wheel_hub", "piston", "flywheel", "control_arm", "steering_knuckle", "ball_joint",
         "subframe", "battery_bracket"]


def _specs():
    out = {}
    for pack in domain_packs.get_packs():
        out.update(pack.param_specs)
    return out


class TestRegistry:
    def test_all_parts_registered_with_a_validator(self):
        assert sorted(domain_packs.part_type_names()) == sorted(PARTS)
        validators = domain_packs.pack_validators()
        assert set(PARTS) <= set(validators)

    def test_pack_declares_what_it_does_not_establish(self):
        (pack,) = [p for p in domain_packs.get_packs() if p.name == "automotive"]
        note = pack.scope_note.lower()
        assert "does not establish" in note
        assert "compliance" in note

    def test_describe_packs_lists_every_part(self):
        info = {p["name"]: p for p in domain_packs.describe_packs()}
        assert {x["part_type"] for x in info["automotive"]["parts"]} == set(PARTS)

    def test_duplicate_registration_is_rejected(self):
        (pack,) = [p for p in domain_packs.get_packs() if p.name == "automotive"]
        with pytest.raises(ValueError):
            domain_packs.register_pack(pack)

    def test_prompt_section_mentions_every_parameter_of_every_part(self):
        section = domain_packs.prompt_section()
        for part, spec in _specs().items():
            assert f"- {part}:" in section
            for name in spec:
                assert name in section, f"{part}.{name} missing from LLM prompt"


@pytest.mark.parametrize("part", PARTS)
class TestEveryPart:
    def test_defaults_are_valid_and_need_no_correction(self, part):
        _, result = validate_parameters(part, {})
        assert result.is_valid, result.errors
        assert result.corrections == {}

    def test_non_numeric_value_is_a_clean_error_not_a_crash(self, part):
        first_float = next(k for k, p in _specs()[part].items() if p.kind == "float")
        _, result = validate_parameters(part, {first_float: "lots"})
        assert not result.is_valid
        assert any("must be a number" in e for e in result.errors)

    def test_hard_bound_violation_is_an_error(self, part):
        first_float = next(k for k, p in _specs()[part].items() if p.kind == "float" and p.lo is not None)
        _, result = validate_parameters(part, {first_float: -5})
        assert not result.is_valid

    def test_non_integer_count_is_an_error(self, part):
        ints = [k for k, p in _specs()[part].items() if p.kind == "int"]
        if not ints:
            pytest.skip("no int parameter")
        _, result = validate_parameters(part, {ints[0]: 3.5})
        assert not result.is_valid

    def test_corrections_are_stable(self, part):
        """A corrected parameter set must re-validate with no errors and no
        further corrections, and must never be silently invalid."""
        rng = random.Random(1234)
        spec = _specs()[part]

        def rnd(p):
            if p.kind == "bool":
                return rng.random() < 0.5
            if p.kind == "choice":
                return rng.choice(p.choices)
            lo = p.lo if p.lo is not None else 0.0
            hi = p.hi if p.hi is not None else (p.default * 3 if p.default else 100.0)
            v = p.default * rng.uniform(0.7, 1.4) if (p.default is not None and rng.random() < 0.7) else rng.uniform(lo, hi)
            v = min(max(v, lo), hi)
            return int(round(v)) if p.kind == "int" else round(v, 2)

        checked = 0
        for _ in range(400):
            params = {k: rnd(p) for k, p in spec.items() if rng.random() < 0.6}
            fixed, res = validate_parameters(part, params)
            if not res.is_valid:
                continue
            checked += 1
            again, res2 = validate_parameters(part, fixed)
            assert res2.is_valid, (params, fixed, res2.errors)
            assert res2.corrections == {}, (params, fixed, res2.corrections)
        assert checked > 20, "fuzz produced too few valid cases to be meaningful"


class TestSpecificRules:
    def test_vented_rotor_too_thin_is_rejected(self):
        _, r = validate_parameters("brake_rotor", {"thickness_mm": 8, "vented": True})
        assert not r.is_valid

    def test_same_thin_rotor_is_fine_when_solid(self):
        _, r = validate_parameters("brake_rotor", {"thickness_mm": 8, "vented": False})
        assert r.is_valid, r.errors

    def test_bolt_circle_that_does_not_fit_inside_hat_is_rejected(self):
        _, r = validate_parameters("brake_rotor", {"bolt_pcd_mm": 200, "hat_diameter_mm": 160})
        assert not r.is_valid

    def test_piston_ring_pack_into_pin_bore_is_rejected(self):
        _, r = validate_parameters("piston", {"compression_height_mm": 20})
        assert not r.is_valid

    def test_piston_dish_deeper_than_crown_is_corrected(self):
        fixed, r = validate_parameters("piston", {"crown_style": "dish", "bowl_depth_mm": 20, "crown_thickness_mm": 8})
        assert r.is_valid
        assert fixed["bowl_depth_mm"] == pytest.approx(6.0)

    def test_hub_barrel_blocking_studs_is_rejected(self):
        _, r = validate_parameters("wheel_hub", {"barrel_diameter_mm": 120})
        assert not r.is_valid

    def test_flywheel_bolt_circle_outside_hub_is_rejected(self):
        _, r = validate_parameters("flywheel", {"crank_bolt_pcd_mm": 120})
        assert not r.is_valid

    def test_control_arm_undersized_sleeve_is_corrected_up(self):
        fixed, r = validate_parameters("control_arm", {"bushing_outer_diameter_mm": 14, "thickness_mm": 12})
        assert r.is_valid
        assert fixed["bushing_outer_diameter_mm"] >= 12 + 4

    def test_bracket_hole_in_bend_radius_is_pulled_clear(self):
        fixed, r = validate_parameters("battery_bracket", {"hole_edge_distance_mm": 100, "base_length_mm": 120})
        assert r.is_valid, r.errors
        assert fixed["hole_edge_distance_mm"] < 100

    def test_knuckle_bore_wall_and_leg_overlap_rules(self):
        _, r = validate_parameters("steering_knuckle", {"bearing_bore_diameter_mm": 112})
        assert not r.is_valid
        _, r = validate_parameters("steering_knuckle", {"leg_thickness_mm": 10, "ball_joint_z_mm": -200})
        assert not r.is_valid          # leg would float free of the housing

    def test_ball_joint_stud_must_not_taper_to_nothing_and_flange_must_overhang(self):
        _, r = validate_parameters("ball_joint", {"stud_length_mm": 100, "stud_taper_ratio": 0.2, "stud_base_diameter_mm": 22})
        assert not r.is_valid
        _, r = validate_parameters("ball_joint", {"flange_diameter_mm": 31})
        assert not r.is_valid

    def test_subframe_geometry_rules(self):
        assert not validate_parameters("subframe", {"length_mm": 320})[1].is_valid          # rail too short for the ears
        assert not validate_parameters("subframe", {"pivot_y_mm": 50})[1].is_valid          # rails would collide at the centreline
        assert not validate_parameters("subframe", {"ear_width_mm": 28})[1].is_valid        # no material around the bore

    def test_knuckle_seat_rules(self):
        assert not validate_parameters("steering_knuckle", {"seat_depth_mm": 90})[1].is_valid   # leg too short for the seat
        assert not validate_parameters("steering_knuckle", {"seat_taper_ratio": 0.2, "seat_depth_mm": 90, "ball_joint_drop_mm": 300})[1].is_valid

    def test_default_hub_pilot_stands_proud_of_the_default_rotor_plate(self):
        from domain_packs.automotive import brake_rotor, wheel_hub
        assert wheel_hub.SPEC["pilot_height_mm"].default >= brake_rotor.SPEC["mounting_thickness_mm"].default

    def test_slotted_bracket_needs_room_across_the_width(self):
        _, r = validate_parameters("battery_bracket",
                                   {"slotted": True, "slot_length_mm": 40, "width_mm": 60, "base_hole_count": 3})
        assert not r.is_valid


class TestBoltPatterns:
    @pytest.mark.parametrize("text,expected", [
        ("rotor 5x114.3", (5, 114.3)),
        ("hub 4 x 100", (4, 100.0)),
        ("pattern 6×139.7 studs", (6, 139.7)),
        ("5*120", (5, 120.0)),
    ])
    def test_recognised(self, text, expected):
        assert parse_bolt_pattern(text) == expected

    @pytest.mark.parametrize("text", ["100x50x25 box", "2x4 lumber", "no pattern here", "12x114.3"])
    def test_not_mistaken_for_dimensions(self, text):
        assert parse_bolt_pattern(text) is None

    def test_table_entries_round_trip(self):
        for key, (n, pcd) in COMMON_BOLT_PATTERNS.items():
            if 3 <= n <= 10:
                assert parse_bolt_pattern(key) == (n, pcd)


class TestParserRouting:
    @pytest.mark.parametrize("text,expected", [
        ("300mm vented brake rotor, 28mm thick, 5x114.3", "brake_rotor"),
        ("solid brake disc 260mm", "brake_rotor"),
        ("wheel hub with 5x114.3 studs", "wheel_hub"),
        ("86mm dished piston with 2 rings and 22mm wrist pin", "piston"),
        ("piston with 22mm piston pin bore", "piston"),
        ("flywheel 300mm 132 teeth", "flywheel"),
        ("lower control arm 380mm long", "control_arm"),
        ("steering knuckle with 80mm bearing bore", "steering_knuckle"),
        ("wheel carrier for a front hub", "steering_knuckle"),
        ("lower ball joint with 22mm tapered stud", "ball_joint"),
        ("front subframe 700mm long", "subframe"),
        ("suspension cradle", "subframe"),
        ("front wishbone", "control_arm"),
        ("EV battery enclosure bracket", "battery_bracket"),
        ("battery mounting bracket 6mm thick", "battery_bracket"),
    ])
    def test_new_parts_route_correctly(self, text, expected):
        assert infer_part_type(text, [], []) == expected

    @pytest.mark.parametrize("text,expected", [
        ("connecting rod, 120mm center distance", "connecting_rod"),
        ("crankshaft with 4 throws, 80mm stroke", "crankshaft"),
        ("piston rod 200mm long 20mm diameter", "connecting_rod"),
        ("piston pin 22mm x 70mm", "shaft"),
        ("motor mount for NEMA 17", "motor_mount"),
        ("steel shaft 10mm diameter 50mm long", "shaft"),
        ("spur gear module 2, 20 teeth", "gear"),
        ("bearing 10mm inner, 20mm outer", "bearing"),
        ("enclosure box 100x80x50", "simple_box"),
        ("l-bracket 50mm wide", "l_bracket"),
        ("pulley 40mm diameter", "pulley"),
        ("assemble a piston and connecting rod together", "assembly"),
    ])
    def test_existing_routing_is_unchanged(self, text, expected):
        assert infer_part_type(text, [], []) == expected

    @pytest.mark.parametrize("text", ["rotor", "hub", "arm", "mount", "disc", "wheel", "battery", "knuckle", "joint",
                                      "frame", "cradle", "ball",
                                      "hinge with 5 knuckles"])
    def test_bare_generic_words_never_trigger_a_pack(self, text):
        assert domain_packs.infer_part_type(text) is None


class TestParserExtraction:
    def test_rotor_values(self):
        p = parse_description("300mm vented brake rotor, 28mm thick, 5x114.3, 66.1mm center bore").parameters
        assert p["outer_diameter_mm"] == 300.0
        assert p["thickness_mm"] == 28.0
        assert p["center_bore_mm"] == 66.1
        assert (p["bolt_count"], p["bolt_pcd_mm"]) == (5, 114.3)
        assert p["vented"] is True

    def test_solid_rotor(self):
        assert parse_description("solid brake disc 260mm 4x100").parameters["vented"] is False

    def test_piston_values(self):
        p = parse_description("86mm dished piston with 2 rings and 22mm wrist pin").parameters
        assert p["diameter_mm"] == 86.0
        assert p["pin_bore_diameter_mm"] == 22.0
        assert p["ring_groove_count"] == 2
        assert p["crown_style"] == "dish"

    def test_control_arm_width_is_not_the_first_unlabeled_number(self):
        # Regression: the legacy extractor assigns the first unlabeled
        # dimension to 'width'; the pack must not inherit that.
        p = parse_description("lower control arm 380mm long, 10mm thick").parameters
        assert p["arm_length_mm"] == 380.0
        assert "arm_width_mm" not in p

    def test_bracket_values(self):
        p = parse_description(
            "EV battery enclosure bracket, 120mm base length, 80mm tall, 60mm wide, "
            "6mm thick, 13mm holes, 3 gussets, slotted").parameters
        assert (p["base_length_mm"], p["upright_height_mm"], p["width_mm"], p["thickness_mm"]) == (120.0, 80.0, 60.0, 6.0)
        assert p["gusset_count"] == 3 and p["slotted"] is True

    @pytest.mark.parametrize("text", [
        "300mm vented brake rotor, 28mm thick, 5x114.3, 66.1mm center bore",
        "solid brake disc 260mm diameter 12mm thick 4x100",
        "wheel hub with 5x114.3 stud pattern, 140mm flange diameter, lobed",
        "86mm dished piston with 2 rings and 22mm wrist pin",
        "flywheel 300mm diameter, 25mm thick, 132 teeth",
        "lower control arm 380mm long, 10mm thick, 30mm wide, with cross brace",
        "EV battery enclosure bracket, 120mm base length, 80mm tall, 60mm wide, 6mm thick, 13mm holes, 3 gussets, slotted",
    ])
    def test_parsed_descriptions_pass_validation_uncorrected(self, text):
        parsed = parse_description(text)
        _, result = validate_parameters(parsed.part_type, parsed.parameters)
        assert result.is_valid, result.errors
        assert result.corrections == {}
