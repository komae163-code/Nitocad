"""Validators for the 15 part types added after the first nine.

Pure Python (validator.py imports no CAD), so these run anywhere."""
import copy
import re
from pathlib import Path

from validator import validate_parameters

ROOT = Path(__file__).resolve().parent.parent

# Every default the templates use must pass untouched. A validator that "fixes" a value
# nobody asked to change is a bug.
DEFAULTS = {
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


def run(part, **overrides):
    params = {**copy.deepcopy(DEFAULTS[part]), **overrides}
    fixed, res = validate_parameters(part, params)
    return fixed, res


def clean(part, **overrides):
    fixed, res = run(part, **overrides)
    assert res.is_valid and not res.corrections, (part, overrides, res.errors, res.corrections)
    return fixed


def corrected(part, key, **overrides):
    fixed, res = run(part, **overrides)
    assert res.is_valid, res.errors
    assert key in res.corrections, (part, key, res.corrections)
    # the corrected spec must itself be buildable: validating it again changes nothing
    again, res2 = validate_parameters(part, fixed)
    assert res2.is_valid and not res2.corrections, (part, res2.errors, res2.corrections)
    return fixed[key], res


def errors(part, **overrides):
    _, res = run(part, **overrides)
    assert not res.is_valid and res.errors, (part, overrides)
    return res.errors


# ---------------------------------------------------------------- coverage ---

def test_every_template_type_has_a_validator():
    src = (ROOT / "cad_templates" / "__init__.py").read_text()
    types = re.findall(r'^\s+"(\w+)":\s+generate_', src, re.M)
    assert len(types) >= 24
    reg = (ROOT / "validator.py").read_text()
    missing = [t for t in types if t != "freeform" and f"'{t}': validate_" not in reg]
    assert missing == [], missing


def test_template_defaults_pass_untouched():
    for part in DEFAULTS:
        clean(part)


def test_empty_params_use_template_defaults_without_complaint():
    for part in DEFAULTS:
        fixed, res = validate_parameters(part, {})
        assert res.is_valid and not res.corrections, (part, res.errors, res.corrections)


def test_non_numbers_are_errors_not_crashes():
    keys = {p: next(k for k, v in d.items() if isinstance(v, (int, float))) for p, d in DEFAULTS.items()}
    for part, key in keys.items():
        for bad in ("big", True, float("nan"), float("inf"), -3, 0):
            _, res = run(part, **{key: bad})
            assert not res.is_valid, (part, key, bad)


def test_input_dict_is_not_mutated():
    p = copy.deepcopy(DEFAULTS["pulley"]); p["groove_depth_mm"] = 99
    snapshot = copy.deepcopy(p)
    validate_parameters("pulley", p)
    assert p == snapshot


# ------------------------------------------------------------ per part type ---

def test_spacer_and_washer_keep_a_wall():
    v, _ = corrected("spacer", "inner_diameter_mm", inner_diameter_mm=9.8)
    assert v <= 9.0
    v, _ = corrected("washer", "inner_diameter_mm", inner_diameter_mm=12)  # bigger than the outer
    assert v < 10
    errors("spacer", inner_diameter_mm=0)  # the template drills the bore, 0 cannot be made


def test_washer_thicker_than_half_its_width_only_warns():
    fixed, res = run("washer", thickness_mm=8)
    assert res.is_valid and not res.corrections and res.warnings


def test_pulley_groove_must_fit_between_flanges_and_clear_the_hub():
    v, _ = corrected("pulley", "belt_width_mm", belt_width_mm=15, thickness_mm=15)
    assert v == 11
    v, _ = corrected("pulley", "groove_depth_mm", groove_depth_mm=19, outer_diameter_mm=40)
    assert v <= 20 - 2.5 - 0.001
    assert errors("pulley", thickness_mm=4)


def test_sprocket_teeth_and_bore():
    assert errors("sprocket", teeth=4)
    assert errors("sprocket", teeth=12.5)
    v, _ = corrected("sprocket", "bore_diameter_mm", teeth=9, bore_diameter_mm=30)
    assert 0 < v < 30
    _, res = run("sprocket", teeth=8)
    assert res.is_valid and res.warnings


def test_beam_and_angle_thickness_limits():
    v, _ = corrected("structural_beam", "thickness_mm", thickness_mm=40)
    assert v <= 20
    assert errors("structural_beam", beam_type="t_beam")
    v, _ = corrected("angle", "thickness_mm", leg1_mm=10, leg2_mm=12, thickness_mm=9)
    assert v == 5


def test_tube_wall_cannot_close_the_bore():
    v, _ = corrected("tube", "wall_thickness_mm", wall_thickness_mm=12)
    assert v == 9.5
    v, _ = corrected("tube", "wall_thickness_mm", shape="rectangular", outer_diameter_mm=40, width_mm=40, height_mm=10, wall_thickness_mm=4.8)
    assert v == 4.5
    assert errors("tube", shape="oval")
    assert errors("tube", outer_diameter_mm=1, wall_thickness_mm=0.5)


def test_pipe_fitting_elbow_rules():
    v, _ = corrected("pipe_fitting", "bend_radius_mm", bend_radius_mm=5)
    assert v == 30
    v, _ = corrected("pipe_fitting", "angle_deg", angle_deg=270)
    assert v == 180
    clean("pipe_fitting", fitting_type="tee")
    _, res = run("pipe_fitting", fitting_type="reducer")
    assert res.is_valid and res.warnings
    v, _ = corrected("pipe_fitting", "wall_thickness_mm", wall_thickness_mm=10)
    assert v == 9.5


def test_flange_bolt_circle_and_hole_count():
    v, _ = corrected("flange", "bolt_circle_mm", bolt_circle_mm=40)       # inside the bore
    assert 50 + 5 + 2 <= v <= 100 - 5 - 2
    v, _ = corrected("flange", "bolt_circle_mm", bolt_circle_mm=99)       # off the rim
    assert v <= 93
    v, _ = corrected("flange", "hole_count", hole_count=40, hole_diameter_mm=8)
    assert 1 <= v < 40
    clean("flange", hole_count=0)
    v, _ = corrected("flange", "inner_diameter_mm", inner_diameter_mm=60, outer_diameter_mm=62, hole_count=0)
    assert v == 31                                                        # no ring left, so the bore is reduced
    assert errors("flange", hole_diameter_mm=30)                          # no radial room for holes that size


def test_hinge_knuckles():
    v, _ = corrected("hinge", "knuckle_count", length_mm=20, knuckle_count=10, pin_diameter_mm=3)
    assert v == 6
    assert errors("hinge", knuckle_count=0)
    _, res = run("hinge", pin_diameter_mm=20)
    assert res.is_valid and res.warnings


def test_cam_bore_lift_and_profile():
    v, _ = corrected("cam", "bore_diameter_mm", bore_diameter_mm=40)
    assert v == 24
    _, res = run("cam", lift_mm=30)
    assert res.is_valid and res.warnings and not res.corrections
    _, res = run("cam", lift_profile="triangular")
    assert res.is_valid and res.warnings
    clean("cam", bore_diameter_mm=0)


def test_hex_standoff_bore():
    v, _ = corrected("hex_standoff", "inner_diameter_mm", inner_diameter_mm=5.8)
    assert v == 5.0
    clean("hex_standoff", inner_diameter_mm=0)


def test_t_bracket_holes_and_thickness():
    v, _ = corrected("t_bracket", "hole_count", length_mm=40, hole_count=5, hole_diameter_mm=5)
    assert 1 <= v < 5
    v, _ = corrected("t_bracket", "hole_count", length_mm=18, hole_count=2)
    assert v == 1
    v, _ = corrected("t_bracket", "hole_diameter_mm", cap_width_mm=10, hole_diameter_mm=9, hole_count=1)
    assert v <= 6
    v, _ = corrected("t_bracket", "thickness_mm", cap_width_mm=18, thickness_mm=10, fillet_radius_mm=0.5, hole_diameter_mm=2)
    assert v == 6
    clean("t_bracket", hole_count=0)


def test_channel_bracket_wall_and_holes():
    v, _ = corrected("channel_bracket", "wall_thickness_mm", height_mm=15, wall_thickness_mm=9)
    assert v == 7
    v, _ = corrected("channel_bracket", "mount_hole_diameter_mm", mount_hole_diameter_mm=14)
    assert v <= 11
    v, _ = corrected("channel_bracket", "mount_hole_count", length_mm=45, mount_hole_count=6, mount_hole_diameter_mm=4)
    assert 1 <= v < 6
    clean("channel_bracket", mount_hole_count=0)


def test_sphere_rejects_bad_sizes_and_warns_when_huge():
    errors("sphere", diameter_mm=-1)
    _, res = run("sphere", diameter_mm=9000)
    assert res.is_valid and res.warnings
