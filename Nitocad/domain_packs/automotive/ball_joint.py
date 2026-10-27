"""
Ball joint (lower, press-in type), modelled as three stacked features on the
Z axis (stud axis), Z up:

    housing   cylinder, z = -housing_length .. 0   (presses into the control-arm bore)
    flange    cylinder, z = 0 .. flange_thickness  (seats on the arm boss top)
    stud      TAPERED cone, z = flange_thickness .. + stud_length, base diameter
              stud_base_diameter_mm narrowing by stud_taper_ratio per mm
              (diametral taper, e.g. 0.1 = 1:10) - seats in the knuckle's tapered bore

This replaces the earlier straight-pin simplification: the housing is a press fit
in the arm (fit-checked against the arm bore) and the stud is a TAPER fit in the
knuckle (cone angle and base diameter checked against the knuckle seat).

The stud is sized to sit entirely INSIDE the knuckle's blind tapered seat (the
nut/thread/through-bore is not modelled), so stud_length_mm must not exceed the
seat depth.

NOT modelled: the ball/socket interior, boot, grease nipple, castellated nut or
thread, cotter pin, retaining circlip, load rating, articulation range. This is
a fit-and-clearance model of the joint's outside, not a joint model. Dimensional
geometry only - no strength, wear or articulation claim is made.
"""
from __future__ import annotations

from validator import ValidationResult
from domain_packs.specs import P
from .common import prepare, run_validation

SPEC = {
    "housing_diameter_mm":   P(30.0, 10.0, 80.0),
    "housing_length_mm":     P(24.0, 8.0, 80.0),
    "flange_diameter_mm":    P(None, 14.0, 120.0),
    "flange_thickness_mm":   P(8.0, 3.0, 25.0),
    "stud_base_diameter_mm": P(22.0, 8.0, 60.0),
    "stud_taper_ratio":      P(0.1, 0.05, 0.2),
    "stud_length_mm":        P(30.0, 10.0, 100.0),
}


def _derive(v: dict) -> dict:
    if v["flange_diameter_mm"] is None:
        v["flange_diameter_mm"] = round(v["housing_diameter_mm"] + 12.0, 3)
    return v


def _check(v: dict, res: ValidationResult) -> None:
    if v["flange_diameter_mm"] < v["housing_diameter_mm"] + 4.0:
        res.add_error("flange_diameter_mm must exceed housing_diameter_mm by at least 4mm (it must overhang the arm bore)")
        return
    if v["stud_base_diameter_mm"] > v["housing_diameter_mm"] - 4.0:
        res.add_error("stud_base_diameter_mm must be at least 4mm smaller than housing_diameter_mm")
        return
    top = v["stud_base_diameter_mm"] - v["stud_taper_ratio"] * v["stud_length_mm"]
    if top < 6.0:
        res.add_error(f"the stud tapers to {top:.1f}mm at its tip (under 6mm): shorten stud_length_mm or reduce stud_taper_ratio")


def validate_ball_joint(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def generate_ball_joint(params: dict):
    import cadquery as cq
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    hl, fl = v["housing_length_mm"], v["flange_thickness_mm"]
    base_r = v["stud_base_diameter_mm"] / 2
    top_r = (v["stud_base_diameter_mm"] - v["stud_taper_ratio"] * v["stud_length_mm"]) / 2
    body = geom.cyl(v["housing_diameter_mm"] / 2, hl, -hl).union(geom.cyl(v["flange_diameter_mm"] / 2, fl))
    stud = geom.wp(cq.Solid.makeCone(base_r, top_r, v["stud_length_mm"], cq.Vector(0, 0, fl - 0.5), cq.Vector(0, 0, 1)))
    return body.union(stud)


def interfaces_ball_joint(params: dict) -> list:
    from mating import Frame, Interface
    v = prepare(params, SPEC, _derive, _check)
    return [
        Interface("housing", "pin", Frame((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)), {
            "diameter_mm": v["housing_diameter_mm"], "length_mm": v["housing_length_mm"], "press_fit": True},
            {"diameter_mm": "housing_diameter_mm", "length_mm": "housing_length_mm"}, (),
            "Press-in housing; seats in the control-arm ball-joint bore"),
        Interface("stud", "taper_stud", Frame((0.0, 0.0, v["flange_thickness_mm"]), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)), {
            "base_diameter_mm": v["stud_base_diameter_mm"], "taper_ratio": v["stud_taper_ratio"],
            "length_mm": v["stud_length_mm"]},
            {"base_diameter_mm": "stud_base_diameter_mm", "taper_ratio": "stud_taper_ratio",
             "length_mm": "stud_length_mm"}, ("flange_thickness_mm",),
            "Tapered stud; seats in the knuckle's tapered bore"),
    ]
