"""
Brake rotor (disc): friction ring + mounting hat + centre bore + stud holes.

Axis is Z. The friction ring spans z = 0 .. thickness_mm. The hat (cup) rises
from z = 0 to hat_offset_mm, where its mounting plate (the face that seats
against the hub) is the top mounting_thickness_mm of that cylinder.

Vented rotors are built as two friction plates joined by radial vanes (straight
vanes - pillar/curved-vane patterns are not modelled). Vane outer ends are
placed so their corners lie exactly on the rim radius, so nothing protrudes
past the friction diameter.

NOT modelled: cross-drilling, slots, chamfered rim, balancing features, wear
limits, thermal coning. This is dimensional geometry, not a thermally or
structurally rated component.
"""
from __future__ import annotations

import math

from validator import ValidationResult
from domain_packs.specs import P
from . import calc
from .common import fix, floor3, prepare, run_validation

SPEC = {
    "outer_diameter_mm":        P(300.0, 100.0, 600.0),
    "hat_diameter_mm":          P(160.0, 40.0, 500.0),
    "thickness_mm":             P(28.0, 4.0, 80.0),
    "vented":                   P(True, kind="bool"),
    "plate_thickness_mm":       P(8.0, 2.0, 30.0),
    "vane_count":               P(36, 6, 72, kind="int"),
    "vane_thickness_mm":        P(4.0, 1.0, 12.0),
    "hat_offset_mm":            P(40.0, 5.0, 120.0),
    "mounting_thickness_mm":    P(8.0, 3.0, 25.0),
    "hat_wall_thickness_mm":    P(5.0, 2.0, 20.0),
    "center_bore_mm":           P(66.1, 10.0, 200.0),
    "bolt_count":               P(5, 3, 10, kind="int"),
    "bolt_pcd_mm":              P(114.3, 40.0, 250.0),
    "bolt_hole_diameter_mm":    P(13.0, 4.0, 25.0),
}

_RIM_OVERLAP = 1.5   # mm the friction ring overlaps the hat wall so they fuse


def _derive(v: dict) -> dict:
    return v


def _check(v: dict, res: ValidationResult) -> None:
    R = v["outer_diameter_mm"] / 2
    hat_r = v["hat_diameter_mm"] / 2
    T = v["thickness_mm"]

    if R - hat_r < 8.0:
        res.add_error(
            f"outer_diameter_mm ({v['outer_diameter_mm']:g}) leaves under 8mm of friction "
            f"ring beyond hat_diameter_mm ({v['hat_diameter_mm']:g})")
        return

    if v["vented"]:
        if T < 10.0:
            res.add_error(f"thickness_mm {T:g} is too thin for a vented rotor (two 2mm plates + 6mm vent gap = 10mm); use vented=false or a thicker rotor")
            return
        if T < 2 * v["plate_thickness_mm"] + 4.0:
            fix(res, v, "plate_thickness_mm", floor3(max(2.0, (T - 6.0) / 2)),
                f"thickness_mm {T:g} cannot hold two plates plus a 6mm vent gap")
        ring_in = hat_r - _RIM_OVERLAP
        max_vanes = int(2 * math.pi * ring_in / (3.0 * v["vane_thickness_mm"]))
        if v["vane_count"] > max_vanes:
            fix(res, v, "vane_count", max(6, max_vanes),
                "vanes would be closer than 2 vane-thicknesses apart at the inner radius")

    if v["hat_offset_mm"] < v["mounting_thickness_mm"] + 1.0:
        res.add_error("hat_offset_mm must exceed mounting_thickness_mm by at least 1mm")
    inner_hat_r = hat_r - v["hat_wall_thickness_mm"]
    bolt_extent = v["bolt_pcd_mm"] / 2 + v["bolt_hole_diameter_mm"] / 2
    if inner_hat_r < bolt_extent + 2.0:
        res.add_error(
            f"bolt circle ({v['bolt_count']} x {v['bolt_hole_diameter_mm']:g}mm on "
            f"{v['bolt_pcd_mm']:g}mm PCD) does not fit inside the hat; increase "
            "hat_diameter_mm or reduce hat_wall_thickness_mm")
    if v["center_bore_mm"] / 2 + 3.0 > v["bolt_pcd_mm"] / 2 - v["bolt_hole_diameter_mm"] / 2:
        res.add_error("center_bore_mm leaves less than 3mm web to the bolt holes")
    if calc.chord_gap(v["bolt_count"], v["bolt_pcd_mm"]) < v["bolt_hole_diameter_mm"] + 2.0:
        res.add_error("bolt holes are closer than 2mm edge-to-edge for this count/PCD")


def validate_brake_rotor(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def generate_brake_rotor(params: dict):
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    R = v["outer_diameter_mm"] / 2
    hat_r = v["hat_diameter_mm"] / 2
    T = v["thickness_mm"]
    ring_in = hat_r - _RIM_OVERLAP

    # --- friction ring ------------------------------------------------------
    if not v["vented"]:
        ring = geom.annulus(R, ring_in, T)
    else:
        pt = v["plate_thickness_mm"]
        plates = geom.annulus(R, ring_in, pt).union(geom.annulus(R, ring_in, pt, T - pt))
        half_t = v["vane_thickness_mm"] / 2
        d_out = math.sqrt(R * R - half_t * half_t)   # corners land exactly on R
        # vanes overlap half a plate thickness into each plate so they fuse
        vz0, vh = pt / 2, T - pt
        vanes = []
        for i in range(v["vane_count"]):
            a = 2 * math.pi * i / v["vane_count"]
            ux, uy = math.cos(a), math.sin(a)
            nx, ny = -uy * half_t, ux * half_t
            pts = [
                (ring_in * ux + nx, ring_in * uy + ny),
                (d_out * ux + nx, d_out * uy + ny),
                (d_out * ux - nx, d_out * uy - ny),
                (ring_in * ux - nx, ring_in * uy - ny),
            ]
            vanes.append(geom.prism(pts, vh, vz0))
        ring = plates.union(geom.fuse(vanes))

    # --- hat ------------------------------------------------------------------
    hat_h = v["hat_offset_mm"]
    mt = v["mounting_thickness_mm"]
    hat = geom.cyl(hat_r, hat_h)
    hat = hat.cut(geom.cyl(hat_r - v["hat_wall_thickness_mm"], hat_h - mt + 1.0, -1.0))

    rotor = ring.union(hat)

    # --- bore + stud holes (mounting plate only) -----------------------------
    rotor = rotor.cut(geom.cyl(v["center_bore_mm"] / 2, max(hat_h, T) + 2.0, -1.0))
    rotor = geom.cut_z_holes(
        rotor, geom.polar_points(v["bolt_count"], v["bolt_pcd_mm"]),
        v["bolt_hole_diameter_mm"], hat_h - mt - 0.5, mt + 1.0)
    return rotor


def interfaces_brake_rotor(params: dict) -> list:
    """Mating features, in rotor-local coordinates (Z = outboard). The
    mounting plate's INBOARD face (z = hat_offset - mounting_thickness) is
    what seats against the hub flange; it faces -Z."""
    from mating import Frame, Interface
    v = prepare(params, SPEC, _derive, _check)
    cavity = v["hat_diameter_mm"] / 2 - v["hat_wall_thickness_mm"]
    z_face = v["hat_offset_mm"] - v["mounting_thickness_mm"]
    fr = Frame((0.0, 0.0, z_face), (0.0, 0.0, -1.0), (1.0, 0.0, 0.0))
    fs = ("hat_offset_mm", "mounting_thickness_mm")
    return [
        Interface("hub_face", "face", fr, {
            "inner_radius_mm": v["center_bore_mm"] / 2, "outer_radius_mm": cavity,
            "cavity_radius_mm": cavity, "bolt_pcd_mm": v["bolt_pcd_mm"],
            "bolt_hole_dia_mm": v["bolt_hole_diameter_mm"]},
            {"inner_radius_mm": "center_bore_mm", "outer_radius_mm": "hat_diameter_mm,hat_wall_thickness_mm",
             "cavity_radius_mm": "hat_diameter_mm,hat_wall_thickness_mm", "bolt_pcd_mm": "bolt_pcd_mm",
             "bolt_hole_dia_mm": "bolt_hole_diameter_mm"}, fs,
            "Mounting plate inboard face; seats on the hub flange"),
        Interface("hub_bore", "pilot_bore", fr, {
            "diameter_mm": v["center_bore_mm"], "depth_mm": v["mounting_thickness_mm"], "tol_class": "H7"},
            {"diameter_mm": "center_bore_mm", "depth_mm": "mounting_thickness_mm"}, fs,
            "Centre bore; locates on the hub pilot"),
        Interface("bolt_pattern", "bolt_pattern", fr, {
            "count": v["bolt_count"], "pcd_mm": v["bolt_pcd_mm"],
            "hole_dia_mm": v["bolt_hole_diameter_mm"], "role": "clearance", "phase_deg": 0.0},
            {"count": "bolt_count", "pcd_mm": "bolt_pcd_mm", "hole_dia_mm": "bolt_hole_diameter_mm"}, fs,
            "Stud/bolt clearance holes"),
    ]
