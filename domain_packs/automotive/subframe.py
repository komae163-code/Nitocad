"""
Front subframe / suspension cradle: the chassis part the control arms hang from.

Frame = the vehicle frame. X fore, Y outboard-left, Z up. The origin is on the
vehicle centreline at pivot-axis height, fore-aft centred.

    rails        two box rails along X at y = +/- pivot_y, above the pivot axis
    crossmembers two boxes along Y near each end joining the rails
    clevises     four (left front/rear, right front/rear): a pair of ears per
                 pivot, each ear a plate with a bore along X on the pivot axis;
                 the control-arm bushing sleeve sits BETWEEN the ears
    body holes   four vertical through-holes for the cradle-to-body bolts

Interfaces: all four clevises (the left pair mates with the control arm),
plus the four body mounts (open until a body part exists).

NOT modelled: the pivot bolts, bushing rubber, weld seams, rail cross-section
detail (boxes, not hydroformed tube), steering rack mount, engine/gearbox
mounts, crash-structure tuning. Dimensional geometry only - not a load-rated or
crash-rated structure.
"""
from __future__ import annotations

from validator import ValidationResult
from domain_packs.specs import P
from .common import prepare, run_validation

SPEC = {
    "length_mm":            P(700.0, 300.0, 2000.0),
    "pivot_spacing_mm":     P(240.0, 60.0, 600.0),
    "pivot_y_mm":           P(350.0, 100.0, 900.0),
    "clevis_gap_mm":        P(47.0, 15.0, 130.0),
    "ear_thickness_mm":     P(8.0, 4.0, 25.0),
    "ear_width_mm":         P(36.0, 20.0, 120.0),
    "ear_height_mm":        P(70.0, 30.0, 200.0),
    "ear_bore_diameter_mm": P(22.0, 8.0, 80.0),
    "rail_width_mm":        P(60.0, 30.0, 200.0),
    "rail_height_mm":       P(50.0, 25.0, 200.0),
    "crossmember_width_mm": P(70.0, 30.0, 250.0),
    "body_hole_diameter_mm": P(14.0, 6.0, 30.0),
}

_EAR_OVERLAP = 10.0   # mm the rail overlaps the top of the ears so they fuse


def _derive(v: dict) -> dict:
    return v


def _check(v: dict, res: ValidationResult) -> None:
    S, gap, e = v["pivot_spacing_mm"], v["clevis_gap_mm"], v["ear_thickness_mm"]
    reach = S / 2 + gap / 2 + e
    if v["length_mm"] / 2 < reach + 10.0:
        res.add_error("length_mm is too short: the rail must extend 10mm past the outer clevis ears")
        return
    if gap / 2 + e + 5.0 > S / 2:
        res.add_error("the front and rear clevises overlap: increase pivot_spacing_mm or reduce clevis_gap_mm/ear_thickness_mm")
        return
    if v["ear_bore_diameter_mm"] + 8.0 > v["ear_width_mm"]:
        res.add_error("ear_width_mm must exceed ear_bore_diameter_mm by at least 8mm")
        return
    if v["ear_bore_diameter_mm"] + 8.0 > v["ear_height_mm"] - _EAR_OVERLAP:
        res.add_error("ear_height_mm leaves no material around the ear bore")
        return
    if v["pivot_y_mm"] - v["rail_width_mm"] / 2 < 20.0:
        res.add_error("the left and right rails would collide at the centreline: increase pivot_y_mm")
        return
    xm = v["length_mm"] / 2 - v["crossmember_width_mm"] / 2 - 5.0
    if v["body_hole_diameter_mm"] + 10.0 > min(v["rail_width_mm"], v["crossmember_width_mm"]):
        res.add_error("body_hole_diameter_mm is too large for the rail/crossmember width")
    if xm < reach:
        res.add_warning("the crossmember sits inside the clevis zone; check the geometry")


def validate_subframe(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def _layout(v: dict) -> dict:
    top = v["ear_height_mm"] / 2
    return {
        "rail_z0": top - _EAR_OVERLAP,
        "rail_z1": top - _EAR_OVERLAP + v["rail_height_mm"],
        "cross_x": v["length_mm"] / 2 - v["crossmember_width_mm"] / 2 - 5.0,
    }


def generate_subframe(params: dict):
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    L, S, gap, e = v["length_mm"], v["pivot_spacing_mm"], v["clevis_gap_mm"], v["ear_thickness_mm"]
    py, eh, ew = v["pivot_y_mm"], v["ear_height_mm"], v["ear_width_mm"]
    lay = _layout(v)
    parts = []
    cuts = []
    for side in (1.0, -1.0):
        y = side * py
        parts.append(geom.box(-L / 2, L / 2, y - v["rail_width_mm"] / 2, y + v["rail_width_mm"] / 2, lay["rail_z0"], lay["rail_z1"]))
        for xc in (S / 2, -S / 2):
            for ex in (xc - gap / 2 - e / 2, xc + gap / 2 + e / 2):
                parts.append(geom.box(ex - e / 2, ex + e / 2, y - ew / 2, y + ew / 2, -eh / 2, eh / 2 - _EAR_OVERLAP + 1.0))
            cuts.append(geom.axis_cyl(v["ear_bore_diameter_mm"] / 2, gap + 4 * e,
                                      (xc - gap / 2 - 2 * e, y, 0.0), (1.0, 0.0, 0.0)))
    cw = v["crossmember_width_mm"]
    for cx in (lay["cross_x"], -lay["cross_x"]):
        parts.append(geom.box(cx - cw / 2, cx + cw / 2, -py, py, lay["rail_z0"], lay["rail_z1"]))
    frame = geom.fuse(parts)
    for c in cuts:
        frame = frame.cut(c)
    for cx in (lay["cross_x"], -lay["cross_x"]):
        for side in (1.0, -1.0):
            frame = frame.cut(geom.cyl(v["body_hole_diameter_mm"] / 2, v["rail_height_mm"] + 2.0,
                                       lay["rail_z0"] - 1.0, cx, side * py))
    return frame


def interfaces_subframe(params: dict) -> list:
    from mating import Frame, Interface
    v = prepare(params, SPEC, _derive, _check)
    S, py = v["pivot_spacing_mm"], v["pivot_y_mm"]
    lay = _layout(v)
    out = []
    for side, sname in ((1.0, "left"), (-1.0, "right")):
        for xs, fname, axis in ((1.0, "front", (-1.0, 0.0, 0.0)), (-1.0, "rear", (1.0, 0.0, 0.0))):
            out.append(Interface(
                f"{sname}_pivot_{fname}", "clevis", Frame((xs * S / 2, side * py, 0.0), axis, (0.0, 0.0, 1.0)), {
                    "gap_mm": v["clevis_gap_mm"], "bore_diameter_mm": v["ear_bore_diameter_mm"]},
                {"gap_mm": "clevis_gap_mm", "bore_diameter_mm": "ear_bore_diameter_mm"},
                ("pivot_spacing_mm", "pivot_y_mm"),
                f"{sname.title()} {fname} clevis; the control-arm bushing sleeve sits between its ears"))
    for xs, fname in ((1.0, "front"), (-1.0, "rear")):
        for side, sname in ((1.0, "left"), (-1.0, "right")):
            out.append(Interface(
                f"body_mount_{fname}_{sname}", "body_mount",
                Frame((xs * lay["cross_x"], side * py, lay["rail_z0"]), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)), {
                    "hole_diameter_mm": v["body_hole_diameter_mm"]},
                {"hole_diameter_mm": "body_hole_diameter_mm"}, (),
                f"Cradle-to-body bolt hole ({fname} {sname})"))
    return out
