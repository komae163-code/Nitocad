"""
Wheel hub: flange + stud holes + inboard barrel + through bore + pilot boss.

Axis is Z. Outboard face (wheel side) is +Z:
    barrel   z = -barrel_length_mm .. 0   (inboard, carries the bearing)
    flange   z = 0 .. flange_thickness_mm
    pilot    z = flange_thickness_mm .. + pilot_height_mm  (wheel-centring spigot;
             the default 12mm stands proud of a typical 8mm rotor mounting plate)

The flange is a round disc, or (lobed=true) a smaller central disc plus one
lobe per stud, the common cast/forged hub shape.

Stud holes are plain clearance/press-fit holes (stud_hole_diameter_mm); studs,
threads, bearing races, ABS tone-ring and splines are NOT modelled.
"""
from __future__ import annotations

from validator import ValidationResult
from domain_packs.specs import P
from . import calc
from .common import fix, prepare, run_validation

SPEC = {
    "flange_diameter_mm":       P(140.0, 60.0, 400.0),
    "flange_thickness_mm":      P(12.0, 4.0, 40.0),
    "barrel_diameter_mm":       P(80.0, 30.0, 250.0),
    "barrel_length_mm":         P(40.0, 0.0, 200.0),
    "bore_diameter_mm":         P(62.0, 10.0, 200.0),
    "pilot_diameter_mm":        P(66.1, 10.0, 300.0),
    "pilot_height_mm":          P(12.0, 0.0, 30.0),
    "bolt_count":               P(5, 3, 10, kind="int"),
    "bolt_pcd_mm":              P(114.3, 40.0, 250.0),
    "stud_hole_diameter_mm":    P(12.0, 4.0, 25.0),
    "lobed":                    P(False, kind="bool"),
    "lobe_diameter_mm":         P(None, 8.0, 80.0),
}


def _derive(v: dict) -> dict:
    if v["lobe_diameter_mm"] is None:
        v["lobe_diameter_mm"] = round(v["stud_hole_diameter_mm"] * 2.6, 3)
    return v


def _check(v: dict, res: ValidationResult) -> None:
    bore_r = v["bore_diameter_mm"] / 2
    stud_r = v["stud_hole_diameter_mm"] / 2
    pcd_r = v["bolt_pcd_mm"] / 2

    if (v["barrel_diameter_mm"] - v["bore_diameter_mm"]) / 2 < 4.0:
        res.add_error("barrel wall under 4mm: barrel_diameter_mm must exceed bore_diameter_mm by 8mm or more")
    if v["pilot_height_mm"] > 0 and v["pilot_diameter_mm"] < v["bore_diameter_mm"] + 4.0:
        res.add_error("pilot_diameter_mm must exceed bore_diameter_mm by at least 4mm")
    if v["pilot_diameter_mm"] / 2 > pcd_r - stud_r - 2.0:
        res.add_error("pilot boss runs into the stud holes; reduce pilot_diameter_mm or enlarge bolt_pcd_mm")
    if v["barrel_length_mm"] > 0 and v["barrel_diameter_mm"] / 2 > pcd_r - stud_r - 1.0:
        res.add_error("barrel would block the stud holes from underneath; reduce barrel_diameter_mm")
    if v["flange_diameter_mm"] < v["barrel_diameter_mm"] + 10.0:
        res.add_error("flange_diameter_mm must exceed barrel_diameter_mm by at least 10mm")
    if not v["lobed"] and v["flange_diameter_mm"] / 2 < pcd_r + stud_r + 6.0:
        res.add_error("round flange leaves under 6mm edge distance at the studs; enlarge flange_diameter_mm or use lobed")
    if v["lobed"] and v["lobe_diameter_mm"] < v["stud_hole_diameter_mm"] + 8.0:
        fix(res, v, "lobe_diameter_mm", v["stud_hole_diameter_mm"] + 8.0,
            "lobes need 4mm of material around each stud hole")
    if calc.chord_gap(v["bolt_count"], v["bolt_pcd_mm"]) < v["stud_hole_diameter_mm"] + 2.0:
        res.add_error("stud holes are closer than 2mm edge-to-edge for this count/PCD")
    if bore_r + 3.0 > pcd_r - stud_r:
        res.add_error("bore_diameter_mm leaves less than 3mm web to the stud holes")


def validate_wheel_hub(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def generate_wheel_hub(params: dict):
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    t = v["flange_thickness_mm"]
    blen = v["barrel_length_mm"]
    studs = geom.polar_points(v["bolt_count"], v["bolt_pcd_mm"])

    flange = geom.cyl(v["flange_diameter_mm"] / 2, t)
    if v["lobed"]:
        # central disc + a lobe and a spoke per stud, instead of a full disc
        disc_r = max(v["barrel_diameter_mm"] / 2 + 8.0, v["bolt_pcd_mm"] / 2 - v["lobe_diameter_mm"])
        parts = [geom.cyl(disc_r, t)]
        spoke_w = v["lobe_diameter_mm"] * 0.6
        for (x, y) in studs:
            parts.append(geom.cyl(v["lobe_diameter_mm"] / 2, t, 0.0, x, y))
            parts.append(geom.prism(geom.link_polygon((0.0, 0.0), (x, y), spoke_w), t))
        flange = geom.fuse(parts)

    hub = flange
    if blen > 0:
        hub = hub.union(geom.cyl(v["barrel_diameter_mm"] / 2, blen, -blen))
    if v["pilot_height_mm"] > 0:
        hub = hub.union(geom.cyl(v["pilot_diameter_mm"] / 2, v["pilot_height_mm"], t))

    total = blen + t + v["pilot_height_mm"]
    hub = hub.cut(geom.cyl(v["bore_diameter_mm"] / 2, total + 2.0, -blen - 1.0))
    hub = geom.cut_z_holes(hub, studs, v["stud_hole_diameter_mm"], -0.5, t + 1.0)
    return hub


def _flange_outer_radius(v: dict) -> float:
    """Largest radius of the flange (round disc, or the lobe tips)."""
    if v["lobed"]:
        disc_r = max(v["barrel_diameter_mm"] / 2 + 8.0, v["bolt_pcd_mm"] / 2 - v["lobe_diameter_mm"])
        return max(disc_r, v["bolt_pcd_mm"] / 2 + v["lobe_diameter_mm"] / 2)
    return v["flange_diameter_mm"] / 2


def interfaces_wheel_hub(params: dict) -> list:
    """Mating features, hub-local (Z = outboard). Rotor + wheel seat on the
    outboard flange face (z = flange_thickness); the barrel is the bearing
    journal and points inboard (-Z)."""
    from mating import Frame, Interface
    v = prepare(params, SPEC, _derive, _check)
    t = v["flange_thickness_mm"]
    outer = _flange_outer_radius(v)
    inner = (v["pilot_diameter_mm"] if v["pilot_height_mm"] > 0 else v["bore_diameter_mm"]) / 2
    fr_out = Frame((0.0, 0.0, t), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    out = [
        Interface("rotor_face", "face", fr_out, {
            "inner_radius_mm": inner, "outer_radius_mm": outer, "body_radius_mm": outer,
            "bolt_pcd_mm": v["bolt_pcd_mm"], "bolt_hole_dia_mm": v["stud_hole_diameter_mm"]},
            {"inner_radius_mm": "pilot_diameter_mm,bore_diameter_mm",
             "outer_radius_mm": "flange_diameter_mm,bolt_pcd_mm,lobe_diameter_mm",
             "body_radius_mm": "flange_diameter_mm,bolt_pcd_mm,lobe_diameter_mm",
             "bolt_pcd_mm": "bolt_pcd_mm", "bolt_hole_dia_mm": "stud_hole_diameter_mm"},
            ("flange_thickness_mm",), "Outboard flange face; the rotor (then the wheel) seats here"),
        Interface("stud_pattern", "bolt_pattern", fr_out, {
            "count": v["bolt_count"], "pcd_mm": v["bolt_pcd_mm"],
            "hole_dia_mm": v["stud_hole_diameter_mm"], "role": "press", "phase_deg": 0.0},
            {"count": "bolt_count", "pcd_mm": "bolt_pcd_mm", "hole_dia_mm": "stud_hole_diameter_mm"},
            ("flange_thickness_mm",), "Stud holes (press-fit)"),
    ]
    if v["pilot_height_mm"] > 0:
        out.append(Interface("rotor_pilot", "pilot_boss", fr_out, {
            "diameter_mm": v["pilot_diameter_mm"], "height_mm": v["pilot_height_mm"], "tol_class": "g6"},
            {"diameter_mm": "pilot_diameter_mm", "height_mm": "pilot_height_mm"},
            ("flange_thickness_mm",), "Wheel-centring pilot the rotor and wheel locate on"))
    if v["barrel_length_mm"] > 0:
        out.append(Interface("bearing_journal", "journal",
                             Frame((0.0, 0.0, 0.0), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0)), {
            "diameter_mm": v["barrel_diameter_mm"], "length_mm": v["barrel_length_mm"], "tol_class": "h6"},
            {"diameter_mm": "barrel_diameter_mm", "length_mm": "barrel_length_mm"}, (),
            "Inboard barrel; seats in the knuckle bearing bore"))
    return out
