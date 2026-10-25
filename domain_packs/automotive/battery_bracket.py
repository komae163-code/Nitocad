"""
Battery-enclosure mounting bracket (EV battery pack / enclosure to body or
sill): a formed L-section with a true inside bend radius, bolt holes (round or
slotted) in each leg, and triangular gussets in the bend.

Axes: the bracket is extruded along X (width_mm, centred on x = 0). In the
YZ profile the base lies on z = 0 .. thickness_mm and extends to
y = base_length_mm; the upright rises along z from the bend to
upright_height_mm at y = 0 .. thickness_mm.

  * base holes: axis Z, one row, at y = base_length_mm - hole_edge_distance_mm
  * upright holes: axis Y, one row, at z = upright_height_mm - hole_edge_distance_mm
  * slotted=true elongates every hole along X by slot_length_mm for fitment
    tolerance

Gussets are flat triangular webs, each gusset_thickness_mm thick, evenly spaced
across the width and fused into both legs.

NOT modelled: weld preps, nutserts/captive nuts, bolt threads, crush beads,
corrosion coatings, material grain/spring-back or flat-pattern development.
Holes are clearance holes sized by you. Dimensional geometry only - not a
crash-rated, fatigue-rated or sealed-enclosure-rated part: no load path,
crash-energy or ingress-protection claim is made or implied.
"""
from __future__ import annotations

import math

from validator import ValidationResult
from domain_packs.specs import P
from . import calc
from .common import fix, floor3, prepare, run_validation

_EPS = 1e-6

SPEC = {
    "base_length_mm":           P(120.0, 30.0, 400.0),
    "width_mm":                 P(60.0, 20.0, 300.0),
    "upright_height_mm":        P(80.0, 20.0, 400.0),
    "thickness_mm":             P(6.0, 2.0, 20.0),
    "bend_radius_mm":           P(6.0, 0.0, 30.0),
    "base_hole_count":          P(2, 0, 6, kind="int"),
    "base_hole_diameter_mm":    P(13.0, 4.0, 30.0),
    "upright_hole_count":       P(2, 0, 6, kind="int"),
    "upright_hole_diameter_mm": P(13.0, 4.0, 30.0),
    "hole_edge_distance_mm":    P(25.0, 6.0, 100.0),
    "slotted":                  P(False, kind="bool"),
    "slot_length_mm":           P(10.0, 0.0, 60.0),
    "gusset_count":             P(2, 0, 4, kind="int"),
    "gusset_thickness_mm":      P(None, 2.0, 20.0),
    "gusset_leg_mm":            P(None, 5.0, 200.0),
}


def _derive(v: dict) -> dict:
    if v["gusset_thickness_mm"] is None:
        v["gusset_thickness_mm"] = v["thickness_mm"]
    if v["gusset_leg_mm"] is None:
        v["gusset_leg_mm"] = round(min(v["base_length_mm"], v["upright_height_mm"]) * 0.45, 3)
    return v


def _check(v: dict, res: ValidationResult) -> None:
    L, H, W, t = v["base_length_mm"], v["upright_height_mm"], v["width_mm"], v["thickness_mm"]
    r_o = v["bend_radius_mm"] + t
    if r_o >= min(L, H) * 0.6:
        res.add_error("bend radius + thickness is too large for the leg lengths (must be under 60% of the shorter leg)")
        return

    slot = v["slot_length_mm"] if v["slotted"] else 0.0
    rows = [(k, n, d, leg) for k, n, d, leg in (
        ("base", v["base_hole_count"], v["base_hole_diameter_mm"], L),
        ("upright", v["upright_hole_count"], v["upright_hole_diameter_mm"], H)) if n > 0]
    for kind, count, dia, _leg in rows:
        if W / count < dia + slot + 6.0:
            res.add_error(f"{count} {kind} holes of {dia:g}mm (+{slot:g}mm slot) do not fit across the {W:g}mm width")
            return
    if rows:
        # One shared hole_edge_distance_mm serves both rows, so solve it
        # jointly: it must clear each hole's free edge (>= d/2 + 3) and keep
        # each hole off the bend radius (<= leg - r_o - 3 - d/2).
        lo = max(d / 2 + 3.0 for _, _, d, _ in rows)
        hi = min(leg - r_o - 3.0 - d / 2 for _, _, d, leg in rows)
        edge = v["hole_edge_distance_mm"]
        if hi < lo - _EPS:
            res.add_error("the legs are too short to fit these bolt holes between the bend and the free edge")
            return
        if edge < lo - _EPS:
            res.add_error(f"hole_edge_distance_mm is under {lo:g}mm: holes would break the free edge")
            return
        if edge > hi + _EPS:
            new_edge = floor3(hi)
            if new_edge < lo - _EPS:
                res.add_error("the legs are too short to fit these bolt holes between the bend and the free edge")
                return
            fix(res, v, "hole_edge_distance_mm", new_edge, "holes would run into the bend radius")
    if v["slotted"] and v["slot_length_mm"] <= 0:
        res.add_error("slotted=true requires slot_length_mm above 0")

    gc = v["gusset_count"]
    if gc > 0:
        gt, gl = v["gusset_thickness_mm"], v["gusset_leg_mm"]
        if gt >= W / (gc + 1) - 2.0:
            res.add_error("gussets are too thick to space evenly across the width")
            return
        limits = []
        if v["base_hole_count"] > 0:
            limits.append(L - v["hole_edge_distance_mm"] - v["base_hole_diameter_mm"] / 2 - 3.0 - t)
        if v["upright_hole_count"] > 0:
            limits.append(H - v["hole_edge_distance_mm"] - v["upright_hole_diameter_mm"] / 2 - 3.0 - t)
        limits.append(min(L, H) - t - 2.0)
        cap = min(limits)
        if cap < 5.0:
            res.add_error("no room for a gusset between the bend and the bolt holes; set gusset_count to 0")
            return
        if gl > cap + _EPS:
            fix(res, v, "gusset_leg_mm", floor3(cap), "gusset would cover a bolt hole or reach the free edge")


def validate_battery_bracket(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def _slot_or_hole_z(x: float, y: float, dia: float, slot: float, z0: float, h: float):
    """Round hole or X-direction slot as a Z-axis cutter."""
    from . import geom
    if slot <= 0:
        return geom.cyl(dia / 2, h, z0, x, y)
    a = geom.cyl(dia / 2, h, z0, x - slot / 2, y)
    b = geom.cyl(dia / 2, h, z0, x + slot / 2, y)
    mid = geom.prism([(x - slot / 2, y - dia / 2), (x + slot / 2, y - dia / 2),
                      (x + slot / 2, y + dia / 2), (x - slot / 2, y + dia / 2)], h, z0)
    return a.union(b).union(mid)


def _slot_or_hole_y(x: float, z: float, dia: float, slot: float, y0: float, h: float):
    """Round hole or X-direction slot as a Y-axis cutter (axis +Y from y0)."""
    import cadquery as cq
    from . import geom
    def tube(cx):
        return geom.axis_cyl(dia / 2, h, (cx, y0, z), (0.0, 1.0, 0.0))
    if slot <= 0:
        return tube(x)
    box = cq.Workplane("XY").box(slot, h, dia, centered=(True, False, True)).translate((x, y0, z))
    return tube(x - slot / 2).union(tube(x + slot / 2)).union(box)


def generate_battery_bracket(params: dict):
    import cadquery as cq
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    L, H, W, t = v["base_length_mm"], v["upright_height_mm"], v["width_mm"], v["thickness_mm"]
    r_i = v["bend_radius_mm"]
    r_o = r_i + t
    c = math.sqrt(2.0) / 2.0

    # --- L profile in (Y, Z), extruded along +X then centred ----------------
    prof = cq.Workplane("YZ")
    if r_i > 0:
        prof = (
            prof.moveTo(r_o, 0).lineTo(L, 0).lineTo(L, t).lineTo(r_o, t)
            .threePointArc((r_o - r_i * c, r_o - r_i * c), (t, r_o))
            .lineTo(t, H).lineTo(0, H).lineTo(0, r_o)
            .threePointArc((r_o - r_o * c, r_o - r_o * c), (r_o, 0))
            .close()
        )
    else:
        prof = prof.polyline([(0, 0), (L, 0), (L, t), (t, t), (t, H), (0, H)]).close()
    body = prof.extrude(W).translate((-W / 2, 0, 0))

    # --- gussets ----------------------------------------------------------------
    gc = v["gusset_count"]
    if gc > 0:
        gt, gl = v["gusset_thickness_mm"], v["gusset_leg_mm"]
        tri = [(t / 2, t / 2), (t + gl, t / 2), (t + gl, t), (t, t + gl), (t / 2, t + gl)]
        for k in range(gc):
            x_mid = -W / 2 + W * (k + 1) / (gc + 1)
            g = cq.Workplane("YZ").workplane(offset=x_mid - gt / 2).polyline(tri).close().extrude(gt)
            body = body.union(g)

    # --- holes ------------------------------------------------------------------
    slot = v["slot_length_mm"] if v["slotted"] else 0.0
    edge = v["hole_edge_distance_mm"]
    n = v["base_hole_count"]
    for k in range(n):
        x = -W / 2 + W * (k + 0.5) / n
        body = body.cut(_slot_or_hole_z(x, L - edge, v["base_hole_diameter_mm"], slot, -1.0, t + 2.0))
    n = v["upright_hole_count"]
    for k in range(n):
        x = -W / 2 + W * (k + 0.5) / n
        body = body.cut(_slot_or_hole_y(x, H - edge, v["upright_hole_diameter_mm"], slot, -1.0, t + 2.0))
    return body
