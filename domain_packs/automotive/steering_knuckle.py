"""
Steering knuckle (wheel upright / hub carrier): the part that physically ties
the wheel end (hub + bearing) to the suspension (control-arm ball joint).

Knuckle-local axes (the same convention as the hub): Z = wheel axis, pointing
OUTBOARD; Y = up; X = rearward on the left-hand side.

    housing   cylinder, axis Z, z = -housing_length .. 0, with the bearing
              bore through it (the hub barrel seats in this bore)
    leg       rectangular block hanging below the housing, centred on the
              ball-joint seat and overlapping the housing in z so it fuses
    seat      blind TAPERED bore in the leg's lower face (axis +Y, opening
              downward) that the ball-joint stud seats in

Bearing bore opening plane is z = 0 (outboard face); flange_gap_mm is the
clearance left between that plane and the hub flange's inboard face.

The ball joint is a separate part (ball_joint.py): its housing presses into the
control arm and its tapered stud seats in this knuckle's tapered bore. The
bore is blind (seat_depth_mm); the through-hole, nut and cotter pin are not
modelled.

NOT modelled: caliper mounting ears, ABS sensor boss, steering arm / tie-rod
boss, brake dust shield, bearing retaining features, casting draft/ribs.
Dimensional geometry only; no strength, fatigue or kinematic claim is made.
"""
from __future__ import annotations

from validator import ValidationResult
from domain_packs.specs import P
from .common import prepare, run_validation

SPEC = {
    "housing_diameter_mm":      P(120.0, 60.0, 300.0),
    "housing_length_mm":        P(36.0, 15.0, 150.0),
    "bearing_bore_diameter_mm": P(80.0, 20.0, 200.0),
    "flange_gap_mm":            P(3.0, 0.0, 20.0),
    "leg_width_mm":             P(50.0, 15.0, 150.0),
    "leg_thickness_mm":         P(30.0, 10.0, 100.0),
    "ball_joint_drop_mm":       P(110.0, 40.0, 400.0),
    "ball_joint_x_mm":          P(0.0, -100.0, 100.0),
    "ball_joint_z_mm":          P(None, -300.0, 50.0),
    "seat_diameter_mm":         P(22.0, 8.0, 60.0),
    "seat_taper_ratio":         P(0.1, 0.05, 0.2),
    "seat_depth_mm":            P(30.0, 10.0, 100.0),
}

_LEG_OVERLAP = 4.0   # mm the leg overlaps the housing along z so the union fuses


def _derive(v: dict) -> dict:
    if v["ball_joint_z_mm"] is None:
        v["ball_joint_z_mm"] = -round(v["housing_length_mm"] + v["leg_thickness_mm"] / 2 - _LEG_OVERLAP, 3)
    return v


def _check(v: dict, res: ValidationResult) -> None:
    R = v["housing_diameter_mm"] / 2
    if (v["housing_diameter_mm"] - v["bearing_bore_diameter_mm"]) / 2 < 6.0:
        res.add_error("housing wall under 6mm: housing_diameter_mm must exceed bearing_bore_diameter_mm by 12mm or more")
        return
    if v["ball_joint_drop_mm"] < R + 10.0:
        res.add_error("ball_joint_drop_mm must put the pin shoulder at least 10mm below the housing")
        return
    if v["leg_width_mm"] < v["seat_diameter_mm"] + 8.0:
        res.add_error("leg_width_mm must exceed seat_diameter_mm by at least 8mm")
        return
    if v["seat_diameter_mm"] - v["seat_taper_ratio"] * v["seat_depth_mm"] < 6.0:
        res.add_error("the tapered seat narrows below 6mm at its blind end: reduce seat_depth_mm or seat_taper_ratio")
        return
    if v["ball_joint_drop_mm"] - R * 0.4 < v["seat_depth_mm"] + 8.0:
        res.add_error("the leg is too short for the tapered seat depth: increase ball_joint_drop_mm or reduce seat_depth_mm")
        return
    z_top = v["ball_joint_z_mm"] + v["leg_thickness_mm"] / 2
    if z_top < -v["housing_length_mm"] + 2.0:
        res.add_error("the leg does not overlap the housing along the wheel axis (it would be a separate body)")
    if z_top > 0.0:
        res.add_error("the leg reaches past the housing's outboard face (z > 0)")
    if abs(v["ball_joint_x_mm"]) + v["leg_width_mm"] / 2 > R + 40.0:
        res.add_error("ball_joint_x_mm puts the leg too far off the housing")


def validate_steering_knuckle(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def generate_steering_knuckle(params: dict):
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    R = v["housing_diameter_mm"] / 2
    Lh = v["housing_length_mm"]
    drop, bx, bz = v["ball_joint_drop_mm"], v["ball_joint_x_mm"], v["ball_joint_z_mm"]
    w, t = v["leg_width_mm"], v["leg_thickness_mm"]

    body = geom.cyl(R, Lh, -Lh)
    leg = geom.prism([(bx - w / 2, -drop), (bx + w / 2, -drop), (bx + w / 2, -R * 0.4), (bx - w / 2, -R * 0.4)],
                     t, bz - t / 2)
    body = body.union(leg)
    import cadquery as cq
    d0 = v["seat_diameter_mm"]
    d1 = d0 - v["seat_taper_ratio"] * v["seat_depth_mm"]
    seat = geom.wp(cq.Solid.makeCone(d0 / 2, d1 / 2, v["seat_depth_mm"], cq.Vector(bx, -drop, bz), cq.Vector(0, 1, 0)))
    body = body.cut(seat)
    return body.cut(geom.cyl(v["bearing_bore_diameter_mm"] / 2, Lh + 2.0, -Lh - 1.0))


def interfaces_steering_knuckle(params: dict) -> list:
    from mating import Frame, Interface
    v = prepare(params, SPEC, _derive, _check)
    return [
        Interface("bearing_bore", "bearing_bore", Frame((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)), {
            "diameter_mm": v["bearing_bore_diameter_mm"], "depth_mm": v["housing_length_mm"],
            "standoff_mm": v["flange_gap_mm"], "tol_class": "H7"},
            {"diameter_mm": "bearing_bore_diameter_mm", "depth_mm": "housing_length_mm",
             "standoff_mm": "flange_gap_mm"}, (),
            "Hub bearing bore; the hub barrel seats here (outboard opening)"),
        Interface("ball_joint_seat", "taper_bore", Frame(
            (v["ball_joint_x_mm"], -v["ball_joint_drop_mm"], v["ball_joint_z_mm"]), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)), {
            "base_diameter_mm": v["seat_diameter_mm"], "taper_ratio": v["seat_taper_ratio"],
            "depth_mm": v["seat_depth_mm"]},
            {"base_diameter_mm": "seat_diameter_mm", "taper_ratio": "seat_taper_ratio",
             "depth_mm": "seat_depth_mm"},
            ("ball_joint_drop_mm", "ball_joint_x_mm", "ball_joint_z_mm", "housing_length_mm", "leg_thickness_mm"),
            "Tapered blind bore in the leg's lower face; the ball-joint stud seats here"),
    ]
