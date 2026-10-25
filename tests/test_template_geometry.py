"""Real-geometry checks for every part template, and the validator-to-template contract.

Needs CadQuery/OCCT, so it is marked requires_cadquery like test_cad_templates.py:

    pytest -m requires_cadquery tests/test_template_geometry.py

What this adds on top of test_cad_templates.py (which only checks each default builds):

  1. Closed-form volumes for the simple parts. If a template's geometry drifts from
     its documented parameters (a bore that is not through, a diameter used as a radius),
     the volume is wrong and these fail.
  2. Invariants for all templates: valid, non-empty, sane size.
  3. The contract the validators exist for: whatever validate_parameters returns for a
     request, the template must be able to build it. Hand-picked edge cases first, then a
     seeded random sweep around each template's defaults.

The sweep is meant to find holes in the validators. A failure prints the exact params,
which is the bug report: add a rule to validator.py and a case to EDGE_CASES.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("cadquery", reason="requires a real CadQuery/OCCT install")

from cad_templates import TEMPLATES  # noqa: E402
from validator import validate_parameters  # noqa: E402

pytestmark = pytest.mark.requires_cadquery

PARTS = sorted(t for t in TEMPLATES if t != "freeform")


def solids(wp):
    return wp.solids().vals()


def volume(wp):
    return sum(s.Volume() for s in solids(wp))


def dims(wp):
    b = wp.val().BoundingBox()
    return sorted([b.xlen, b.ylen, b.zlen])


def assert_buildable(part, params, label=""):
    wp = TEMPLATES[part](dict(params))
    ss = solids(wp)
    assert ss, f"{part} {label}: no solids from {params}"
    assert all(s.isValid() for s in ss), f"{part} {label}: invalid solid from {params}"
    assert volume(wp) > 1e-6, f"{part} {label}: zero volume from {params}"
    return wp


# ------------------------------------------------------- 1. closed-form volumes ---

def approx(x, rel=5e-3):
    return pytest.approx(x, rel=rel)


def test_spacer_volume_and_size():
    wp = TEMPLATES["spacer"]({"outer_diameter_mm": 12, "inner_diameter_mm": 6, "length_mm": 25})
    assert volume(wp) == approx(math.pi / 4 * (12**2 - 6**2) * 25)
    assert dims(wp) == [approx(12), approx(12), approx(25)]


def test_washer_volume():
    wp = TEMPLATES["washer"]({"outer_diameter_mm": 20, "inner_diameter_mm": 8.4, "thickness_mm": 1.6})
    assert volume(wp) == approx(math.pi / 4 * (20**2 - 8.4**2) * 1.6)


def test_sphere_volume():
    wp = TEMPLATES["sphere"]({"diameter_mm": 30})
    assert volume(wp) == approx(4 / 3 * math.pi * 15**3)


def test_round_tube_volume():
    wp = TEMPLATES["tube"]({"outer_diameter_mm": 30, "wall_thickness_mm": 3, "length_mm": 80, "shape": "round"})
    assert volume(wp) == approx(math.pi / 4 * (30**2 - 24**2) * 80)


def test_square_tube_volume():
    wp = TEMPLATES["tube"]({"outer_diameter_mm": 30, "wall_thickness_mm": 3, "length_mm": 80, "shape": "square"})
    assert volume(wp) == approx((30**2 - 24**2) * 80)


def test_flange_volume_with_bolt_holes():
    p = {"outer_diameter_mm": 120, "inner_diameter_mm": 50, "thickness_mm": 12,
         "hole_count": 6, "hole_diameter_mm": 9, "bolt_circle_mm": 90}
    expected = math.pi / 4 * (120**2 - 50**2) * 12 - 6 * math.pi / 4 * 9**2 * 12
    assert volume(TEMPLATES["flange"](p)) == approx(expected)


def test_hex_standoff_uses_across_flats_not_across_corners():
    wp = TEMPLATES["hex_standoff"]({"across_flats_mm": 8, "inner_diameter_mm": 3, "length_mm": 15})
    hex_area = math.sqrt(3) / 2 * 8**2
    assert volume(wp) == approx((hex_area - math.pi / 4 * 3**2) * 15)


def test_angle_is_a_single_connected_l():
    wp = TEMPLATES["angle"]({"leg1_mm": 40, "leg2_mm": 60, "thickness_mm": 5, "length_mm": 100})
    assert len(solids(wp)) == 1
    upper = (40 + 60) * 5 * 100
    assert 0.8 * upper < volume(wp) <= upper * 1.001


def test_hex_nut_volume_and_bore():
    import standards
    n = standards.hex_nut("M10")
    wp = TEMPLATES["hex_nut"]({"size": "M10"})
    hex_area = math.sqrt(3) / 2 * n["across_flats_mm"] ** 2
    assert volume(wp) == approx((hex_area - math.pi / 4 * 10**2) * n["height_mm"])
    assert len(solids(wp)) == 1


def test_hex_bolt_volume_and_axis():
    import standards
    b = standards.hex_bolt("M8", 30)
    wp = TEMPLATES["hex_bolt"]({"size": "M8", "length_mm": 30})
    expected = math.sqrt(3) / 2 * b["head_across_flats_mm"] ** 2 * b["head_height_mm"] + math.pi / 4 * 8**2 * 30
    assert volume(wp) == approx(expected) and len(solids(wp)) == 1
    bb = wp.val().BoundingBox()
    assert bb.zmin == pytest.approx(-b["head_height_mm"], abs=1e-3) and bb.zmax == pytest.approx(30, abs=1e-3)   # head below z=0, shank above


def test_socket_cap_screw_has_a_socket_and_one_solid():
    import standards
    b = standards.socket_cap_screw("M6", 20)
    wp = TEMPLATES["socket_cap_screw"]({"size": "M6", "length_mm": 20})
    solid_head_and_shank = math.pi / 4 * b["head_diameter_mm"] ** 2 * b["head_height_mm"] + math.pi / 4 * 6**2 * 20
    assert volume(wp) < solid_head_and_shank and len(solids(wp)) == 1


def test_a_nut_fits_on_the_bolt_of_the_same_size_without_overlap():
    from assembly import assembly_checks_report, generate_assembly
    parts = [
        {"name": "bolt", "part_type": "hex_bolt", "parameters": {"size": "M8", "length_mm": 30}, "position": (0, 0, 0)},
        {"name": "nut", "part_type": "hex_nut", "parameters": {"size": "M8"}, "position": (0, 0, 40)}]
    report = assembly_checks_report(generate_assembly(parts), parts)
    assert report["interferences"] == []
    mp = report["mass_properties"]
    assert mp["parts_counted"] == 0 and sorted(mp["parts_missing"]) == ["bolt", "nut"] and not mp["complete"]   # no material set, so no mass guessed


# ------------------------------------------------------------- 2. invariants ---

@pytest.mark.parametrize("part", PARTS)
def test_defaults_are_valid_nonempty_and_a_sane_size(part):
    wp = assert_buildable(part, {})
    d = dims(wp)
    assert d[0] > 0.1, f"{part} is flat or degenerate: {d}"
    assert d[2] < 2000, f"{part} is implausibly large for its defaults: {d}"


@pytest.mark.parametrize("part", ["spacer", "washer", "sphere", "tube", "flange", "hex_standoff", "angle", "structural_beam"])
def test_simple_parts_are_one_solid(part):
    assert len(solids(assert_buildable(part, {}))) == 1


# ------------------- 3. validator -> template: corrected values must still build ---

EDGE_CASES = [
    ("spacer", {"inner_diameter_mm": 9.9}),
    ("spacer", {"outer_diameter_mm": 3, "inner_diameter_mm": 3}),
    ("washer", {"inner_diameter_mm": 14}),
    ("washer", {"thickness_mm": 9}),
    ("pulley", {"belt_width_mm": 40}),
    ("pulley", {"groove_depth_mm": 30}),
    ("pulley", {"outer_diameter_mm": 14, "bore_diameter_mm": 12}),
    ("sprocket", {"teeth": 6}),
    ("sprocket", {"teeth": 9, "bore_diameter_mm": 40}),
    ("sprocket", {"teeth": 60, "chain_pitch_mm": 25.4}),
    ("structural_beam", {"thickness_mm": 60}),
    ("structural_beam", {"beam_type": "channel", "thickness_mm": 60}),
    ("angle", {"leg1_mm": 10, "leg2_mm": 90, "thickness_mm": 9}),
    ("tube", {"wall_thickness_mm": 15}),
    ("tube", {"shape": "square", "wall_thickness_mm": 15}),
    ("tube", {"shape": "rectangular", "width_mm": 40, "height_mm": 10, "wall_thickness_mm": 4.9}),
    ("pipe_fitting", {"fitting_type": "elbow", "bend_radius_mm": 3}),
    ("pipe_fitting", {"fitting_type": "elbow", "angle_deg": 175}),
    ("pipe_fitting", {"fitting_type": "tee", "wall_thickness_mm": 9}),
    ("flange", {"bolt_circle_mm": 40}),
    ("flange", {"bolt_circle_mm": 99}),
    ("flange", {"hole_count": 40, "hole_diameter_mm": 8}),
    ("flange", {"inner_diameter_mm": 96}),
    ("hinge", {"length_mm": 20, "knuckle_count": 12}),
    ("hinge", {"pin_diameter_mm": 14}),
    ("cam", {"bore_diameter_mm": 40}),
    ("cam", {"lift_mm": 30}),
    ("cam", {"lift_profile": "harmonic", "lift_mm": 15}),
    ("hex_standoff", {"inner_diameter_mm": 5.9}),
    ("t_bracket", {"length_mm": 40, "hole_count": 6, "hole_diameter_mm": 5}),
    ("t_bracket", {"length_mm": 18, "hole_count": 3}),
    ("t_bracket", {"cap_width_mm": 12, "hole_diameter_mm": 9, "hole_count": 1}),
    ("t_bracket", {"cap_width_mm": 18, "thickness_mm": 10}),
    ("channel_bracket", {"wall_thickness_mm": 9}),
    ("channel_bracket", {"mount_hole_diameter_mm": 14}),
    ("channel_bracket", {"length_mm": 45, "mount_hole_count": 8}),
]


@pytest.mark.parametrize("part,overrides", EDGE_CASES, ids=[f"{p}-{i}" for i, (p, _) in enumerate(EDGE_CASES)])
def test_validated_edge_cases_build(part, overrides):
    fixed, result = validate_parameters(part, overrides)
    assert result.is_valid, f"edge case should be correctable, got errors {result.errors}"
    assert_buildable(part, fixed, "after validation")


def _random_params(rng: random.Random, defaults: dict) -> dict:
    out = {}
    for key, value in defaults.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            out[key] = value
        elif key.endswith("count") or key == "teeth":
            out[key] = rng.randint(0, 10) if key.endswith("count") else rng.randint(3, 60)
        elif key.endswith("deg"):
            out[key] = rng.choice([30, 45, 90, 120, 180])
        else:
            out[key] = round(value * rng.uniform(0.2, 3.0), 2)
    return out


# The defaults the validator tests use, which mirror the templates' own.
SWEEP_DEFAULTS = {
    "spacer": {"outer_diameter_mm": 10, "inner_diameter_mm": 5, "length_mm": 10},
    "washer": {"outer_diameter_mm": 10, "inner_diameter_mm": 5, "thickness_mm": 1.5},
    "sphere": {"diameter_mm": 20},
    "pulley": {"outer_diameter_mm": 40, "belt_width_mm": 10, "bore_diameter_mm": 5, "thickness_mm": 15, "groove_depth_mm": 3},
    "sprocket": {"teeth": 18, "chain_pitch_mm": 12.7, "thickness_mm": 8, "bore_diameter_mm": 8},
    "structural_beam": {"beam_type": "i_beam", "height_mm": 100, "width_mm": 50, "length_mm": 200, "thickness_mm": 5},
    "angle": {"leg1_mm": 50, "leg2_mm": 50, "thickness_mm": 5, "length_mm": 100},
    "tube": {"outer_diameter_mm": 20, "wall_thickness_mm": 2, "length_mm": 100, "shape": "round"},
    "pipe_fitting": {"fitting_type": "elbow", "outer_diameter_mm": 20, "wall_thickness_mm": 2, "length_mm": 50, "angle_deg": 90},
    "flange": {"outer_diameter_mm": 100, "inner_diameter_mm": 50, "thickness_mm": 10, "hole_count": 4, "hole_diameter_mm": 5, "bolt_circle_mm": 75},
    "hinge": {"length_mm": 100, "width_mm": 30, "thickness_mm": 2, "knuckle_count": 5, "pin_diameter_mm": 3},
    "cam": {"base_radius_mm": 20, "lift_mm": 10, "thickness_mm": 5, "bore_diameter_mm": 5, "lift_profile": "simple"},
    "hex_standoff": {"across_flats_mm": 6, "inner_diameter_mm": 3.2, "length_mm": 10},
    "t_bracket": {"length_mm": 60, "cap_width_mm": 40, "stem_height_mm": 30, "thickness_mm": 4, "hole_count": 2, "hole_diameter_mm": 3.2, "fillet_radius_mm": 1.5},
    "channel_bracket": {"length_mm": 60, "width_mm": 20, "height_mm": 15, "wall_thickness_mm": 2, "mount_hole_count": 2, "mount_hole_diameter_mm": 4},
}


@pytest.mark.parametrize("part", sorted(SWEEP_DEFAULTS))
def test_random_requests_that_pass_validation_always_build(part):
    rng = random.Random(f"nitocad-{part}")          # same sweep every run, per part
    built = refused = 0
    for _ in range(40):
        request = _random_params(rng, SWEEP_DEFAULTS[part])
        fixed, result = validate_parameters(part, request)
        if not result.is_valid:
            refused += 1                              # a clean refusal is an acceptable outcome
            continue
        assert_buildable(part, fixed, f"(request {request})")
        built += 1
    assert built >= 10, f"{part}: only {built} of 40 random requests were buildable ({refused} refused). The validator may be too strict."
