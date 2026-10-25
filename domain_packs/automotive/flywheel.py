"""
Flywheel (single-mass, clutch type).

Axis is Z. Clutch face is z = thickness_mm; the crank side is z = 0 at the
disc, with the hub boss protruding toward the crank (negative z):

    disc        z = 0 .. thickness_mm
    hub boss    z = thickness_mm - hub_thickness_mm .. thickness_mm
    crank face  z = thickness_mm - hub_thickness_mm   (centre recess + bolt holes)
    clutch face z = thickness_mm                       (spigot bore + tapped-hole drills)

Features: crank bolt circle (through hub), crank pilot recess, spigot-bearing
bore (blind, clutch side), clutch-cover bolt holes (blind, drilled to the
tap-drill diameter you give; threads are not modelled), optional lightening
holes, optional starter ring-gear teeth.

Ring-gear teeth are simple rectangular notches cut through the rim - NOT an
involute starter-ring profile (no pressure angle, no chamfer lead-in). They
exist to give the correct tooth COUNT and pitch-circle appearance. Default is
0 (off), because a notch per tooth is slow to build above ~100 teeth.

NOT modelled: friction-surface relief, balance drillings, dowel holes, trigger
wheel/reluctor teeth, dual-mass springs. Dimensional geometry only - not a
balanced, burst-rated or thermally rated component.
"""
from __future__ import annotations

import math

from validator import ValidationResult
from domain_packs.specs import P
from . import calc
from .common import fix, floor3, prepare, run_validation

SPEC = {
    "outer_diameter_mm":            P(300.0, 120.0, 600.0),
    "thickness_mm":                 P(25.0, 8.0, 80.0),
    "hub_diameter_mm":              P(100.0, 30.0, 300.0),
    "hub_thickness_mm":             P(None, 8.0, 120.0),
    "center_bore_mm":               P(46.0, 10.0, 200.0),
    "center_recess_depth_mm":       P(6.0, 1.0, 30.0),
    "crank_bolt_count":             P(6, 3, 12, kind="int"),
    "crank_bolt_pcd_mm":            P(70.0, 30.0, 250.0),
    "crank_bolt_hole_diameter_mm":  P(10.5, 4.0, 20.0),
    "pilot_bore_diameter_mm":       P(18.0, 0.0, 60.0),
    "pilot_bore_depth_mm":          P(12.0, 0.0, 40.0),
    "clutch_bolt_count":            P(6, 0, 12, kind="int"),
    "clutch_bolt_pcd_mm":           P(250.0, 60.0, 560.0),
    "clutch_bolt_hole_diameter_mm": P(6.8, 3.0, 12.0),
    "clutch_bolt_depth_mm":         P(14.0, 3.0, 40.0),
    "lightening_hole_count":        P(0, 0, 12, kind="int"),
    "lightening_hole_diameter_mm":  P(30.0, 8.0, 100.0),
    "lightening_pcd_mm":            P(None, 40.0, 500.0),
    "ring_gear_teeth":              P(0, 0, 160, kind="int"),
    "ring_gear_tooth_depth_mm":     P(4.0, 1.5, 12.0),
}


def _derive(v: dict) -> dict:
    if v["hub_thickness_mm"] is None:
        v["hub_thickness_mm"] = round(v["thickness_mm"] + 12.0, 3)
    if v["lightening_pcd_mm"] is None:
        hub_r = v["hub_diameter_mm"] / 2
        v["lightening_pcd_mm"] = round(hub_r + (v["outer_diameter_mm"] / 2 - 25.0), 3)
    return v


def _check(v: dict, res: ValidationResult) -> None:
    R = v["outer_diameter_mm"] / 2
    hub_r = v["hub_diameter_mm"] / 2
    T = v["thickness_mm"]
    hub_t = v["hub_thickness_mm"]
    bolt_r = v["crank_bolt_hole_diameter_mm"] / 2
    pcd_r = v["crank_bolt_pcd_mm"] / 2

    if R - hub_r < 40.0:
        res.add_error("outer_diameter_mm must exceed hub_diameter_mm by at least 80mm")
        return
    if hub_t < T:
        res.add_error("hub_thickness_mm must be at least thickness_mm")
        return
    if pcd_r + bolt_r + 3.0 > hub_r:
        res.add_error("crank bolt circle does not fit inside the hub; enlarge hub_diameter_mm or reduce crank_bolt_pcd_mm")
    if v["center_bore_mm"] / 2 + 3.0 > pcd_r - bolt_r:
        res.add_error("center_bore_mm leaves under 3mm web to the crank bolt holes")
    if calc.chord_gap(v["crank_bolt_count"], v["crank_bolt_pcd_mm"]) < v["crank_bolt_hole_diameter_mm"] + 2.0:
        res.add_error("crank bolt holes are closer than 2mm edge-to-edge")
    if v["center_recess_depth_mm"] + v["pilot_bore_depth_mm"] > hub_t - 3.0:
        res.add_error("centre recess + spigot bore depth leave under 3mm of web in the hub")

    if v["ring_gear_teeth"] and v["ring_gear_tooth_depth_mm"] > R * 0.1 + 1e-6:
        fix(res, v, "ring_gear_tooth_depth_mm", floor3(R * 0.1), "tooth depth over 10% of the radius")
    teeth_depth = v["ring_gear_tooth_depth_mm"] if v["ring_gear_teeth"] else 0.0
    if v["clutch_bolt_count"] > 0:
        c_r = v["clutch_bolt_pcd_mm"] / 2
        h_r = v["clutch_bolt_hole_diameter_mm"] / 2
        if c_r + h_r + 4.0 > R - teeth_depth:
            res.add_error("clutch bolt circle runs off the rim; reduce clutch_bolt_pcd_mm")
        if v["clutch_bolt_depth_mm"] > T - 3.0:
            fix(res, v, "clutch_bolt_depth_mm", T - 3.0, "tapped-hole drill would break through the disc")
        if calc.chord_gap(v["clutch_bolt_count"], v["clutch_bolt_pcd_mm"]) < v["clutch_bolt_hole_diameter_mm"] + 2.0:
            res.add_error("clutch bolt holes are closer than 2mm edge-to-edge")
    if v["pilot_bore_diameter_mm"] > 0 and v["pilot_bore_diameter_mm"] / 2 + 3.0 > pcd_r - bolt_r:
        res.add_error("pilot_bore_diameter_mm leaves under 3mm to the crank bolt holes")

    if v["lightening_hole_count"] > 0:
        l_r = v["lightening_hole_diameter_mm"] / 2
        mid = v["lightening_pcd_mm"] / 2
        if mid - l_r < hub_r + 4.0 or mid + l_r > R - teeth_depth - 6.0:
            res.add_error("lightening holes do not fit between the hub and the rim")
        if calc.chord_gap(v["lightening_hole_count"], v["lightening_pcd_mm"]) < v["lightening_hole_diameter_mm"] + 6.0:
            res.add_error("lightening holes are closer than 6mm to each other")

    if v["ring_gear_teeth"]:
        pitch = 2 * math.pi * R / v["ring_gear_teeth"]
        if pitch < 4.0:
            res.add_error("ring gear pitch under 4mm - too many teeth for this diameter")
        if v["ring_gear_teeth"] > 100:
            res.add_warning(f"{v['ring_gear_teeth']} ring-gear teeth will be slow to build")


def validate_flywheel(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def _teeth_cutter(R: float, n: int, depth: float, T: float):
    """All tooth notches as one extrusion of N disjoint wires; falls back to
    one cut per notch if the multi-wire extrude is rejected."""
    half = (2 * math.pi * R / n) * 0.25     # notch = 50% of the pitch
    def notch(i):
        a = 2 * math.pi * i / n
        ux, uy = math.cos(a), math.sin(a)
        nx, ny = -uy * half, ux * half
        r0, r1 = R - depth, R + 1.0
        return [(r0 * ux + nx, r0 * uy + ny), (r1 * ux + nx, r1 * uy + ny),
                (r1 * ux - nx, r1 * uy - ny), (r0 * ux - nx, r0 * uy - ny)]
    import cadquery as cq
    from . import geom
    try:
        wpl = cq.Workplane("XY").workplane(offset=-1.0)
        for i in range(n):
            wpl = wpl.polyline(notch(i)).close()
        return wpl.extrude(T + 2.0), True
    except Exception:  # noqa: BLE001
        return [geom.prism(notch(i), T + 2.0, -1.0) for i in range(n)], False


def generate_flywheel(params: dict):
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    R = v["outer_diameter_mm"] / 2
    T = v["thickness_mm"]
    hub_t = v["hub_thickness_mm"]
    hub_r = v["hub_diameter_mm"] / 2
    z_crank = T - hub_t                      # crank-side face of the hub boss

    body = geom.cyl(R, T).union(geom.cyl(hub_r, hub_t, z_crank))

    # crank side: pilot recess + bolt circle (through the hub)
    body = body.cut(geom.cyl(v["center_bore_mm"] / 2, v["center_recess_depth_mm"] + 1.0, z_crank - 1.0))
    body = geom.cut_z_holes(
        body, geom.polar_points(v["crank_bolt_count"], v["crank_bolt_pcd_mm"]),
        v["crank_bolt_hole_diameter_mm"], z_crank - 1.0, hub_t + 2.0)

    # clutch side: spigot bearing bore + blind clutch-cover bolt holes
    if v["pilot_bore_diameter_mm"] > 0 and v["pilot_bore_depth_mm"] > 0:
        body = body.cut(geom.cyl(v["pilot_bore_diameter_mm"] / 2, v["pilot_bore_depth_mm"] + 1.0,
                                 T - v["pilot_bore_depth_mm"]))
    if v["clutch_bolt_count"] > 0:
        body = geom.cut_z_holes(
            body, geom.polar_points(v["clutch_bolt_count"], v["clutch_bolt_pcd_mm"], 360.0 / v["clutch_bolt_count"] / 2),
            v["clutch_bolt_hole_diameter_mm"], T - v["clutch_bolt_depth_mm"], v["clutch_bolt_depth_mm"] + 1.0)

    if v["lightening_hole_count"] > 0:
        body = geom.cut_z_holes(
            body, geom.polar_points(v["lightening_hole_count"], v["lightening_pcd_mm"]),
            v["lightening_hole_diameter_mm"], -1.0, T + 2.0)

    if v["ring_gear_teeth"] > 0:
        cutter, single = _teeth_cutter(R, v["ring_gear_teeth"], v["ring_gear_tooth_depth_mm"], T)
        if single:
            body = body.cut(cutter)
        else:
            for c in cutter:
                body = body.cut(c)
    return body
