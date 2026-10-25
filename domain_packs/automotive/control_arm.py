"""
Suspension control arm (wishbone / A-arm).

Vehicle-style axes: X = fore-aft, Y = lateral (inboard -> outboard), Z = up.

  * Two inboard bushing sleeves lie along the pivot axis (the X axis at
    y = 0, z = 0), centred at x = +/- pivot_spacing_mm / 2. Each is a tube
    (bushing_outer_diameter_mm OD, bushing_bore_diameter_mm ID,
    bushing_length_mm long) - the bore is where the rubber/poly bush and
    its sleeve would sit.
  * Two tapered legs (flat plate, thickness_mm) run from the sleeves to a
    single outboard ball-joint boss at (0, arm_length_mm), whose axis is Z.
  * Optional cross brace along the pivot axis between the sleeves (closes the
    "A").
  * Optional anti-roll-bar link hole through one leg.

Ball-joint bore is a straight cylinder: the tapered seat, stud slot and clamp
bolt are NOT modelled; neither are stiffening flanges, stamping beads,
casting draft or weld preps. Plate-style arm only. Dimensional geometry
- not a load-rated or fatigue-rated suspension component; do not infer
strength from it.
"""
from __future__ import annotations

import math

from validator import ValidationResult
from domain_packs.specs import P
from . import calc
from .common import fix, prepare, run_validation

SPEC = {
    "pivot_spacing_mm":             P(240.0, 60.0, 600.0),
    "arm_length_mm":                P(380.0, 80.0, 900.0),
    "thickness_mm":                 P(10.0, 3.0, 40.0),
    "arm_width_mm":                 P(32.0, 10.0, 120.0),
    "arm_tip_width_mm":             P(None, 8.0, 120.0),
    "bushing_bore_diameter_mm":     P(22.0, 8.0, 80.0),
    "bushing_outer_diameter_mm":    P(None, 12.0, 120.0),
    "bushing_length_mm":            P(45.0, 15.0, 120.0),
    "ball_joint_hole_diameter_mm":  P(30.0, 10.0, 80.0),
    "ball_joint_boss_diameter_mm":  P(None, 20.0, 140.0),
    "ball_joint_boss_height_mm":    P(None, 8.0, 80.0),
    "cross_brace":                  P(False, kind="bool"),
    "brace_width_mm":               P(None, 8.0, 100.0),
    "link_hole_diameter_mm":        P(12.0, 0.0, 30.0),
    "link_hole_position":           P(0.55, 0.2, 0.85),
}


def _derive(v: dict) -> dict:
    if v["arm_tip_width_mm"] is None:
        v["arm_tip_width_mm"] = round(v["arm_width_mm"] * 0.75, 3)
    if v["bushing_outer_diameter_mm"] is None:
        v["bushing_outer_diameter_mm"] = round(v["bushing_bore_diameter_mm"] + 16.0, 3)
    if v["ball_joint_boss_diameter_mm"] is None:
        v["ball_joint_boss_diameter_mm"] = round(v["ball_joint_hole_diameter_mm"] + 26.0, 3)
    if v["ball_joint_boss_height_mm"] is None:
        v["ball_joint_boss_height_mm"] = round(v["thickness_mm"] + 12.0, 3)
    if v["brace_width_mm"] is None:
        v["brace_width_mm"] = round(v["arm_width_mm"] * 0.8, 3)
    return v


def _check(v: dict, res: ValidationResult) -> None:
    S, L, t = v["pivot_spacing_mm"], v["arm_length_mm"], v["thickness_mm"]
    blen = v["bushing_length_mm"]
    sx = S / 2

    if v["bushing_outer_diameter_mm"] < v["bushing_bore_diameter_mm"] + 4.0:
        fix(res, v, "bushing_outer_diameter_mm", v["bushing_bore_diameter_mm"] + 4.0,
            "bushing sleeve needs at least 2mm of wall")
    if v["bushing_outer_diameter_mm"] < t + 4.0:
        fix(res, v, "bushing_outer_diameter_mm", t + 4.0,
            "bushing sleeve must be larger than the plate thickness to capture the leg")
    # leg start corners must sit inside the sleeve so the union fuses
    d = math.hypot(sx, L)
    y_off = (sx / d) * v["arm_width_mm"] / 2
    need_r = math.hypot(t / 2, y_off) + 1.0
    if v["bushing_outer_diameter_mm"] / 2 < need_r:
        fix(res, v, "bushing_outer_diameter_mm", 2 * need_r, "sleeve too small to fully capture the arm leg")
    if S < blen + 4.0:
        res.add_error("pivot_spacing_mm must exceed bushing_length_mm by at least 4mm (sleeves would touch)")
    if v["arm_width_mm"] > blen:
        res.add_warning("arm_width_mm is larger than bushing_length_mm; the leg will overhang the sleeve ends")
    if v["ball_joint_boss_diameter_mm"] < v["ball_joint_hole_diameter_mm"] + 4.0:
        fix(res, v, "ball_joint_boss_diameter_mm", v["ball_joint_hole_diameter_mm"] + 4.0,
            "ball-joint boss needs at least 2mm of wall")
    if v["ball_joint_boss_height_mm"] < t:
        fix(res, v, "ball_joint_boss_height_mm", t, "ball-joint boss must be at least as thick as the plate")
    if L < v["ball_joint_boss_diameter_mm"] / 2 + v["bushing_outer_diameter_mm"] / 2 + 20.0:
        res.add_error("arm_length_mm is too short for the bushing and ball-joint sizes")
    if v["arm_tip_width_mm"] > v["ball_joint_boss_diameter_mm"]:
        res.add_warning("arm_tip_width_mm exceeds the ball-joint boss diameter")

    if v["link_hole_diameter_mm"] > 0:
        f = v["link_hole_position"]
        local_w = v["arm_width_mm"] + (v["arm_tip_width_mm"] - v["arm_width_mm"]) * f
        if v["link_hole_diameter_mm"] > local_w - 6.0:
            res.add_error("link_hole_diameter_mm leaves under 3mm of material each side of the leg at that position")


def validate_control_arm(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def generate_control_arm(params: dict):
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    S, L, t = v["pivot_spacing_mm"], v["arm_length_mm"], v["thickness_mm"]
    sx = S / 2
    blen = v["bushing_length_mm"]
    bush_r = v["bushing_outer_diameter_mm"] / 2
    bj_r = v["ball_joint_boss_diameter_mm"] / 2
    bj_h = v["ball_joint_boss_height_mm"]

    parts = []
    for x in (-sx, sx):
        parts.append(geom.axis_cyl(bush_r, blen, (x - blen / 2, 0.0, 0.0), (1.0, 0.0, 0.0)))
        parts.append(geom.prism(
            geom.link_polygon((x, 0.0), (0.0, L), v["arm_width_mm"], v["arm_tip_width_mm"]), t, -t / 2))
    parts.append(geom.cyl(bj_r, bj_h, -bj_h / 2, 0.0, L))
    if v["cross_brace"]:
        bw = v["brace_width_mm"]
        parts.append(geom.prism([(-sx, -bw / 2), (sx, -bw / 2), (sx, bw / 2), (-sx, bw / 2)], t, -t / 2))
    arm = geom.fuse(parts)

    for x in (-sx, sx):
        arm = arm.cut(geom.axis_cyl(v["bushing_bore_diameter_mm"] / 2, blen + 2.0,
                                    (x - blen / 2 - 1.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
    arm = arm.cut(geom.cyl(v["ball_joint_hole_diameter_mm"] / 2, bj_h + 2.0, -bj_h / 2 - 1.0, 0.0, L))
    if v["link_hole_diameter_mm"] > 0:
        f = v["link_hole_position"]
        arm = arm.cut(geom.cyl(v["link_hole_diameter_mm"] / 2, t + 2.0, -t / 2 - 1.0, sx * (1 - f), L * f))
    return arm


def interfaces_control_arm(params: dict) -> list:
    """Mating features, arm-local (vehicle axes: X fore, Y outboard, Z up).
    The ball-joint socket opens upward from the TOP of the boss; the two
    inboard bushings are open interfaces until a chassis/subframe part
    exists to mate with them."""
    from mating import Frame, Interface
    v = prepare(params, SPEC, _derive, _check)
    sx = v["pivot_spacing_mm"] / 2
    out = [Interface(
        "ball_joint_socket", "socket",
        Frame((0.0, v["arm_length_mm"], v["ball_joint_boss_height_mm"] / 2), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
        {"diameter_mm": v["ball_joint_hole_diameter_mm"], "depth_mm": v["ball_joint_boss_height_mm"]},
        {"diameter_mm": "ball_joint_hole_diameter_mm", "depth_mm": "ball_joint_boss_height_mm"},
        ("arm_length_mm", "ball_joint_boss_height_mm", "thickness_mm"),
        "Ball-joint bore; the knuckle pin passes through it from above")]
    for name, x, ax in (("pivot_front", sx, (1.0, 0.0, 0.0)), ("pivot_rear", -sx, (-1.0, 0.0, 0.0))):
        out.append(Interface(
            name, "bushing_bore", Frame((x, 0.0, 0.0), ax, (0.0, 0.0, 1.0)),
            {"diameter_mm": v["bushing_bore_diameter_mm"], "length_mm": v["bushing_length_mm"]},
            {"diameter_mm": "bushing_bore_diameter_mm", "length_mm": "bushing_length_mm"},
            ("pivot_spacing_mm",), "Inboard bushing sleeve (pivot axis along X); needs a chassis part"))
    return out
