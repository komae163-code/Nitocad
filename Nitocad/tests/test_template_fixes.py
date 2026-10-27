"""Real-geometry checks for the templates that were fixed: gear, sprocket, pulley, L-bracket, angle,
shaft chamfer, the plate with no holes, the hinge and the T-bracket. Needs CadQuery/OCCT:

    pytest -m requires_cadquery tests/test_template_fixes.py

Each expected number comes from the part's own geometry (a closed-form volume, a count of teeth read off the
pitch circle, a point that must be inside or outside), not from reading the template back. If one of these
fails, the printed numbers are the bug report.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
cq = pytest.importorskip("cadquery", reason="requires a real CadQuery/OCCT install")

from cad_templates import TEMPLATES  # noqa: E402
from validator import validate_parameters  # noqa: E402

pytestmark = pytest.mark.requires_cadquery


def solids(wp):
    return wp.solids().vals()


def volume(wp):
    return sum(s.Volume() for s in solids(wp))


def dims(wp):
    b = wp.val().BoundingBox()
    return sorted([b.xlen, b.ylen, b.zlen])


def inside(wp, x, y, z):
    return wp.val().isInside(cq.Vector(x, y, z))


def one_valid_solid(wp):
    ss = solids(wp)
    assert len(ss) == 1 and ss[0].isValid()


def teeth_on_circle(wp, radius, z, samples):
    """How many separate runs of material the circle of this radius passes through."""
    state = [inside(wp, radius * math.cos(2 * math.pi * i / samples), radius * math.sin(2 * math.pi * i / samples), z) for i in range(samples)]
    return sum(1 for i in range(samples) if state[i] and not state[i - 1])


# ---------------------------------------------------------------------------------- gear
GEAR = {"module": 2.0, "teeth": 20, "thickness_mm": 10.0, "bore_diameter_mm": 8.0}


def test_gear_is_one_valid_solid_of_the_right_size():
    wp = TEMPLATES["gear"](dict(GEAR))
    one_valid_solid(wp)
    tip_diameter = 2.0 * (20 + 2)                                     # module x (teeth + 2)
    assert dims(wp) == [pytest.approx(10, rel=5e-3), pytest.approx(tip_diameter, rel=5e-3), pytest.approx(tip_diameter, rel=5e-3)]


@pytest.mark.parametrize("teeth", [12, 17, 20, 24, 31, 40])
def test_gear_has_exactly_as_many_teeth_as_asked(teeth):
    """Walk the pitch circle. The old template gave half as many for an even tooth count."""
    wp = TEMPLATES["gear"]({**GEAR, "teeth": teeth})
    assert teeth_on_circle(wp, GEAR["module"] * teeth / 2, 5.0, 40 * teeth) == teeth


def test_gear_volume_matches_gear_theory():
    wp = TEMPLATES["gear"](dict(GEAR))
    rp = 20.0
    rf, ra = rp - 2.5, rp + 2.0                                       # root and tip radii
    area = math.pi * (rf ** 2 + 0.5 * (ra ** 2 - rf ** 2))            # teeth fill about half the ring between root and tip
    expected = (area - math.pi / 4 * 8 ** 2) * 10
    assert volume(wp) == pytest.approx(expected, rel=0.03)


def test_gear_is_solid_between_the_bore_and_the_root_and_open_between_the_teeth():
    wp = TEMPLATES["gear"](dict(GEAR))
    assert not inside(wp, 2.0, 0, 5)                                  # inside the 8 mm bore
    assert inside(wp, 10.0, 0, 5)                                     # the hub
    assert inside(wp, 21.0, 0, 5)                                     # on the tip of tooth 0, which stands on the +X axis
    gap = 21.0 * math.cos(math.pi / 20), 21.0 * math.sin(math.pi / 20)  # halfway between tooth 0 and tooth 1, outside the pitch circle
    assert not inside(wp, gap[0], gap[1], 5)


def test_a_validated_gear_always_builds():
    for teeth in (3, 5, 8, 12, 60, 100):
        for module in (0.5, 1.0, 2.0, 4.0):
            fixed, result = validate_parameters("gear", {"module": module, "teeth": teeth, "thickness_mm": 5, "bore_diameter_mm": 5})
            if not result.is_valid:
                continue
            one_valid_solid(TEMPLATES["gear"](fixed))


# ---------------------------------------------------------------------------------- sprocket
def test_sprocket_has_one_space_per_tooth():
    for teeth in (12, 18, 24):
        wp = TEMPLATES["sprocket"]({"teeth": teeth, "chain_pitch_mm": 12.7, "thickness_mm": 8, "bore_diameter_mm": 8})
        one_valid_solid(wp)
        pitch_radius = 12.7 / (2 * math.sin(math.pi / teeth))
        assert teeth_on_circle(wp, pitch_radius + 12.7 / 4 - 0.2, 4.0, 40 * teeth) == teeth, teeth


# ---------------------------------------------------------------------------------- pulley
def test_pulley_has_a_hub_and_a_groove_and_not_the_other_way_round():
    p = {"outer_diameter_mm": 40, "belt_width_mm": 10, "bore_diameter_mm": 5, "thickness_mm": 15, "groove_depth_mm": 3}
    wp = TEMPLATES["pulley"](dict(p))
    one_valid_solid(wp)
    assert inside(wp, 10.0, 0, 7.5)                                   # the hub, mid-width. The old template had a void here.
    assert not inside(wp, 18.5, 0, 7.5)                               # in the groove, between the groove floor (r 17) and the rim (r 20)
    assert inside(wp, 16.0, 0, 7.5)                                   # just under the groove floor
    assert inside(wp, 21.0, 0, 1.0)                                   # on a flange
    assert not inside(wp, 21.0, 0, 7.5)                               # beside the groove, past the rim


def test_pulley_volume():
    p = {"outer_diameter_mm": 40, "belt_width_mm": 10, "bore_diameter_mm": 5, "thickness_mm": 15, "groove_depth_mm": 3}
    R, T, b = 20.0, 15.0, 5.0
    floor = R - 3.0
    body = math.pi * R ** 2 * T
    flanges = 2 * 2.0 * math.pi * ((R + 2) ** 2 - R ** 2)             # two 2 mm flanges, 2 mm proud of the rim
    groove = math.pi * (R ** 2 - floor ** 2) * 10.0                   # z 2.5 to 12.5
    bore = math.pi / 4 * b ** 2 * T
    assert volume(TEMPLATES["pulley"](p)) == pytest.approx(body + flanges - groove - bore, rel=5e-3)


def test_pulley_with_a_belt_wider_than_the_space_between_the_flanges_keeps_its_flanges():
    wp = TEMPLATES["pulley"]({"outer_diameter_mm": 40, "belt_width_mm": 40, "bore_diameter_mm": 5, "thickness_mm": 15, "groove_depth_mm": 3})
    one_valid_solid(wp)
    assert inside(wp, 21.0, 0, 1.0) and inside(wp, 21.0, 0, 14.0)


# ---------------------------------------------------------------------------------- L-bracket
LB = {"width_mm": 50, "height_mm": 60, "depth_mm": 40, "thickness_mm": 3, "hole_count": 0, "fillet_radius_mm": 0}


def test_l_bracket_is_an_l_and_not_a_t():
    wp = TEMPLATES["l_bracket"](dict(LB))
    one_valid_solid(wp)
    b = wp.val().BoundingBox()
    assert (b.xmin, b.xmax) == (pytest.approx(-25), pytest.approx(25))
    assert (b.ymin, b.ymax) == (pytest.approx(0), pytest.approx(40))   # nothing on the -Y side of the vertical leg
    assert (b.zmin, b.zmax) == (pytest.approx(0), pytest.approx(60))
    assert inside(wp, 0, 1.5, 30)                                      # the vertical leg
    assert inside(wp, 0, 30, 1.5)                                      # the horizontal leg, far from the bend
    assert not inside(wp, 0, 20, 30)                                   # the air inside the L
    assert not inside(wp, 0, -1, 30)                                   # behind the vertical leg


def test_l_bracket_volume_without_and_with_holes():
    base = 50 * 3 * (60 + 40 - 3)                                      # width x thickness x (height + depth - thickness)
    assert volume(TEMPLATES["l_bracket"](dict(LB))) == pytest.approx(base, rel=5e-3)
    wp = TEMPLATES["l_bracket"]({**LB, "hole_count": 2, "hole_diameter_mm": 3.2})
    one_valid_solid(wp)
    assert volume(wp) == pytest.approx(base - 4 * math.pi / 4 * 3.2 ** 2 * 3, rel=5e-3)     # two holes in each leg


def test_l_bracket_holes_go_through_each_leg_where_they_should():
    wp = TEMPLATES["l_bracket"]({**LB, "hole_count": 2, "hole_diameter_mm": 3.2})
    assert not inside(wp, 0, 1.5, 10)                                  # a hole in the vertical leg (z 10 and 50)
    assert inside(wp, 0, 1.5, 30)                                      # solid between the holes
    assert not inside(wp, 0, 13, 1.5)                                  # a hole in the horizontal leg (y 13 and 30)
    assert inside(wp, 0, 20, 1.5)


def test_l_bracket_default_fillet_still_builds_one_valid_solid():
    one_valid_solid(TEMPLATES["l_bracket"]({"width_mm": 50, "height_mm": 60, "depth_mm": 40, "thickness_mm": 6, "fillet_radius_mm": 2.0}))


# ---------------------------------------------------------------------------------- angle
def test_angle_is_an_l_shaped_section_extruded_along_its_length():
    wp = TEMPLATES["angle"]({"leg1_mm": 40, "leg2_mm": 60, "thickness_mm": 5, "length_mm": 100})
    one_valid_solid(wp)
    assert volume(wp) == pytest.approx((40 + 60 - 5) * 5 * 100, rel=5e-3)
    assert dims(wp) == [pytest.approx(40), pytest.approx(60), pytest.approx(100)]
    assert inside(wp, 20, 2.5, 50) and inside(wp, 2.5, 30, 50)
    assert not inside(wp, 20, 30, 50)                                  # the air inside the L


# ---------------------------------------------------------------------------------- shaft chamfer
def test_shaft_chamfer_is_on_the_ends():
    plain = TEMPLATES["shaft"]({"diameter_mm": 10, "length_mm": 50})
    chamfered = TEMPLATES["shaft"]({"diameter_mm": 10, "length_mm": 50, "chamfer_mm": 1.0})
    one_valid_solid(chamfered)
    r, c = 5.0, 1.0
    assert volume(plain) - volume(chamfered) == pytest.approx(2 * math.pi * c ** 2 * (r - c / 3), rel=0.02)   # a triangular ring off each end
    assert inside(plain, 4.9, 0, 0.05) and not inside(chamfered, 4.9, 0, 0.05)
    assert inside(chamfered, 4.9, 0, 25) and dims(chamfered) == [pytest.approx(10), pytest.approx(10), pytest.approx(50)]


# ---------------------------------------------------------------------------------- plate
def test_plate_with_no_holes_has_none():
    p = {"length_mm": 100, "width_mm": 60, "thickness_mm": 5, "hole_count_x": 0, "hole_count_y": 0, "corner_fillet_mm": 0}
    fixed, result = validate_parameters("flat_plate", dict(p))
    assert result.is_valid and fixed["hole_count_x"] == 0
    assert volume(TEMPLATES["flat_plate"](fixed)) == pytest.approx(100 * 60 * 5, rel=1e-6)


# ---------------------------------------------------------------------------------- hinge
HINGE = {"length_mm": 100, "width_mm": 30, "thickness_mm": 2, "knuckle_count": 5, "pin_diameter_mm": 3}


def test_hinge_is_one_valid_solid_with_a_leaf_each_side_of_the_barrel():
    wp = TEMPLATES["hinge"](dict(HINGE))
    one_valid_solid(wp)                                               # the old one had a leaf touching nothing: two solids
    barrel_diameter = 2 * max(3 / 2 + 1.0, 2 / 2 + 0.5)               # 1 mm of wall round the pin
    assert dims(wp) == [pytest.approx(barrel_diameter), pytest.approx(barrel_diameter + 60), pytest.approx(100)]
    assert inside(wp, -20, 0, 50) and inside(wp, 20, 0, 50)           # a leaf each side
    assert not inside(wp, -20, 5, 50)                                 # and they are thin


def test_hinge_knuckles_are_separated_by_gaps_the_pin_runs_through():
    wp = TEMPLATES["hinge"](dict(HINGE))
    assert inside(wp, 0, 2.0, 10)                                     # in the middle of a knuckle
    assert not inside(wp, 0, 2.0, 20)                                 # in the gap between knuckles 1 and 2, outside the pin
    assert inside(wp, 0, 0, 20)                                       # the pin goes through the gap


def test_hinge_stays_one_solid_with_many_knuckles_and_a_fat_pin():
    one_valid_solid(TEMPLATES["hinge"]({**HINGE, "length_mm": 20, "knuckle_count": 12}))
    one_valid_solid(TEMPLATES["hinge"]({**HINGE, "pin_diameter_mm": 14}))
    one_valid_solid(TEMPLATES["hinge"]({**HINGE, "thickness_mm": 8, "knuckle_count": 1}))


# ---------------------------------------------------------------------------------- T-bracket
TB = {"length_mm": 60, "cap_width_mm": 40, "stem_height_mm": 30, "thickness_mm": 4, "hole_count": 2, "hole_diameter_mm": 3.2, "fillet_radius_mm": 0}


def test_t_bracket_holes_are_in_the_flat_each_side_of_the_stem():
    wp = TEMPLATES["t_bracket"](dict(TB))
    one_valid_solid(wp)
    cap_z = 30 + 2                                                    # middle of the cap
    for x in (10, 50):                                                # two in a row, 40 mm apart, centred on the 60 mm length
        for y in (11, -11):                                           # the middle of each 18 mm flat beside the 4 mm stem
            assert not inside(wp, x, y, cap_z), (x, y)
    assert inside(wp, 30, 11, cap_z) and inside(wp, 10, 5, cap_z)     # solid between the holes and beside them


def test_t_bracket_stem_is_not_drilled():
    """The old holes were on the centre line, straight over the stem, and went down the middle of it."""
    wp = TEMPLATES["t_bracket"](dict(TB))
    assert inside(wp, 10, 0, 15) and inside(wp, 50, 0, 15) and inside(wp, 10, 0, 32)


def test_t_bracket_volume():
    expected = 60 * (40 * 4 + 4 * 30) - 4 * math.pi / 4 * 3.2 ** 2 * 4        # cap + stem below it, less two holes in each flat
    assert volume(TEMPLATES["t_bracket"](dict(TB))) == pytest.approx(expected, rel=5e-3)


def test_t_bracket_holes_stay_on_the_part_for_any_count():
    for n in (1, 2, 3, 4):
        wp = TEMPLATES["t_bracket"]({**TB, "hole_count": n})
        spacing = 40 / (n - 1) if n > 1 else 0
        xs = [30 - (n - 1) * spacing / 2 + i * spacing for i in range(n)]
        assert all(0 < x < 60 for x in xs)
        assert all(not inside(wp, x, 11, 32) and not inside(wp, x, -11, 32) for x in xs), n
        assert volume(wp) == pytest.approx(60 * (40 * 4 + 4 * 30) - 2 * n * math.pi / 4 * 3.2 ** 2 * 4, rel=5e-3)


def test_t_bracket_default_fillet_still_builds_one_valid_solid():
    one_valid_solid(TEMPLATES["t_bracket"]({"length_mm": 60, "cap_width_mm": 40, "stem_height_mm": 30, "thickness_mm": 4}))


def test_a_plate_with_no_hole_counts_is_a_plain_plate():
    """The template used to default to a 4 x 3 grid, so a plate that never mentioned holes came out with twelve."""
    wp = TEMPLATES["flat_plate"]({"length_mm": 100, "width_mm": 60, "thickness_mm": 5, "corner_fillet_mm": 0})
    assert volume(wp) == pytest.approx(100 * 60 * 5, rel=1e-6)
    holes = TEMPLATES["flat_plate"]({"length_mm": 100, "width_mm": 60, "thickness_mm": 5, "corner_fillet_mm": 0, "hole_count_x": 2, "hole_count_y": 2, "hole_diameter_mm": 4})
    assert volume(holes) == pytest.approx(100 * 60 * 5 - 4 * math.pi / 4 * 16 * 5, rel=1e-6)


# ---------------------------------------------------------------------------------- box
def test_box_is_solid_by_default_and_hollow_when_asked():
    solid = TEMPLATES["simple_box"]({"length_mm": 100, "width_mm": 80, "height_mm": 50})
    assert volume(solid) == pytest.approx(100 * 80 * 50, rel=1e-6)
    shell = TEMPLATES["simple_box"]({"length_mm": 100, "width_mm": 80, "height_mm": 50, "wall_thickness_mm": 3, "hollow": True})
    one_valid_solid(shell)
    assert volume(shell) == pytest.approx(100 * 80 * 50 - 94 * 74 * 47, rel=1e-3)         # open top: the cavity is 47 high
    assert inside(shell, 0, 0, -24) and not inside(shell, 0, 0, 0) and not inside(shell, 0, 0, 24)   # floor, cavity, and open at the top


def test_box_still_accepts_the_old_name_for_hollow():
    old = TEMPLATES["simple_box"]({"length_mm": 100, "width_mm": 80, "height_mm": 50, "wall_thickness_mm": 3, "has_lid": True})
    new = TEMPLATES["simple_box"]({"length_mm": 100, "width_mm": 80, "height_mm": 50, "wall_thickness_mm": 3, "hollow": True})
    assert volume(old) == pytest.approx(volume(new), rel=1e-9)
