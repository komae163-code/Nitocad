"""The gear outline is plain geometry, so it is tested here without a CAD kernel.

The rest of the gear (bore, extrusion, validity of the solid) is covered where CadQuery is available, in
tests/test_template_fixes.py."""
import importlib.util
import math
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("gear_profile", Path(__file__).resolve().parents[1] / "cad_templates" / "_gear_profile.py")
gp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gp)

CASES = [(2, 20), (1, 12), (1.5, 17), (3, 24), (2, 40), (1, 60), (4, 10), (2.5, 32), (0.8, 100)]


def inside(pt, poly):
    x, y = pt
    c = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            c = not c
    return c


@pytest.mark.parametrize("m,z", CASES)
def test_every_point_lies_between_the_root_and_the_tip(m, z):
    r = gp.gear_radii(m, z)
    radii = [math.hypot(*p) for p in gp.involute_gear_outline(m, z)]
    assert min(radii) >= r["root"] - 1e-9 and max(radii) <= r["tip"] + 1e-9
    assert min(radii) == pytest.approx(r["root"], rel=1e-6) and max(radii) == pytest.approx(r["tip"], rel=1e-6)


@pytest.mark.parametrize("m,z", CASES)
def test_there_are_exactly_z_teeth(m, z):
    """Walk the pitch circle and count the times it goes from air into material."""
    poly = gp.involute_gear_outline(m, z)
    rp = gp.gear_radii(m, z)["pitch"]
    n = 40 * z
    state = [inside((rp * math.cos(2 * math.pi * i / n), rp * math.sin(2 * math.pi * i / n)), poly) for i in range(n)]
    rises = sum(1 for i in range(n) if state[i] and not state[i - 1])
    assert rises == z


@pytest.mark.parametrize("m,z", [(2, 20), (1, 12), (3, 24), (2, 40)])
def test_a_tooth_is_half_the_circular_pitch_thick_at_the_pitch_circle(m, z):
    poly = gp.involute_gear_outline(m, z)
    rp = gp.gear_radii(m, z)["pitch"]
    n = 20 * z * 10
    frac = sum(inside((rp * math.cos(2 * math.pi * i / n), rp * math.sin(2 * math.pi * i / n)), poly) for i in range(n)) / n
    assert frac == pytest.approx(0.5, abs=0.02)


@pytest.mark.parametrize("m,z", CASES)
def test_the_outline_is_counter_clockwise_and_has_the_area_gear_theory_predicts(m, z):
    r = gp.gear_radii(m, z)
    area = gp.outline_area(gp.involute_gear_outline(m, z))
    assert area > 0
    estimate = math.pi * (r["root"] ** 2 + 0.5 * (r["tip"] ** 2 - r["root"] ** 2))    # teeth fill about half of the ring between root and tip
    if z >= 16:
        assert area == pytest.approx(estimate, rel=0.03)
    assert math.pi * r["root"] ** 2 < area < math.pi * r["tip"] ** 2                   # never less than the root disc or more than the tip disc


def test_the_gear_repeats_every_tooth():
    """Turning the outline by one tooth angle lands every point on another point of the outline."""
    m, z = 2, 20
    poly = gp.involute_gear_outline(m, z)
    a = 2 * math.pi / z
    have = {(round(x, 6), round(y, 6)) for x, y in poly}
    for x, y in poly:
        xr, yr = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
        assert (round(xr, 6), round(yr, 6)) in have


def test_tooth_zero_is_centred_on_the_x_axis_and_the_outline_is_symmetric_about_it():
    poly = gp.involute_gear_outline(2, 20)
    have = {(round(x, 6), round(y, 6)) for x, y in poly}
    assert (round(gp.gear_radii(2, 20)["tip"], 6), 0.0) in have
    for x, y in poly:
        assert (round(x, 6), round(-y, 6) + 0.0) in have


def test_the_old_template_bug_would_have_been_caught():
    """The old template put notch i at angle 2*i*tooth_angle, so even tooth counts got half as many notches."""
    z = 20
    old_positions = {round((2 * (i * 360 / z) + 180 / z) % 360, 6) for i in range(z)}
    assert len(old_positions) == z // 2
    # The new outline puts z distinct tips on z distinct angles.
    poly = gp.involute_gear_outline(2, z)
    tip = gp.gear_radii(2, z)["tip"]
    angles = {round(math.degrees(math.atan2(y, x)) % 360, 3) for x, y in poly if abs(math.hypot(x, y) - tip) < 1e-9}
    assert len({round(a / (360 / z)) % z for a in angles}) == z


def test_a_pointed_tooth_is_cut_back_not_inverted():
    r = gp.gear_radii(1, 6, 25)                       # 25 degrees on 6 teeth: the flanks meet below the usual tip
    assert r["pitch"] < r["tip"] <= r["pitch"] + 1.0
    poly = gp.involute_gear_outline(1, 6, 25)
    assert gp.outline_area(poly) > 0 and all(math.hypot(*p) <= r["tip"] + 1e-9 for p in poly)


def test_overlapping_teeth_are_refused():
    with pytest.raises(ValueError, match="overlap"):
        gp.involute_gear_outline(2, 12, 60)


def test_too_few_teeth_or_a_bad_module_are_refused():
    with pytest.raises(ValueError):
        gp.involute_gear_outline(2, 2)
    with pytest.raises(ValueError):
        gp.involute_gear_outline(0, 20)


def test_no_duplicate_consecutive_points_and_it_closes_without_repeating_the_first():
    poly = gp.involute_gear_outline(2, 20)
    assert all(math.hypot(a[0] - b[0], a[1] - b[1]) > 1e-9 for a, b in zip(poly, poly[1:] + poly[:1]))
