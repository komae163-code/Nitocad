"""
Analytic envelopes (see envelope.py) for the parts that take part in
interface-driven assemblies. Each function takes the part's parameters and
returns an envelope in the same part-local coordinates as the CadQuery
generator, so the two can be compared directly.

Simplifications (all conservative unless noted): vented rotors are solid rings;
tapered studs/seats are cylinders of the mean diameter in the knuckle and of the
BASE diameter on the stud (conservative); lobed hub flanges are full discs
out to the lobe tips; bolt/stud holes and the vane gaps are omitted; the arm's
link hole is omitted.
"""
from __future__ import annotations

import envelope as E
from mating import Transform

from . import ball_joint, brake_rotor, control_arm, steering_knuckle, subframe, wheel_hub
from .calc import link_polygon
from .common import prepare


def env_brake_rotor(params: dict) -> E.Shape:
    v = prepare(params, brake_rotor.SPEC, brake_rotor._derive, brake_rotor._check)
    R, hat_r, T = v["outer_diameter_mm"] / 2, v["hat_diameter_mm"] / 2, v["thickness_mm"]
    ring_in = hat_r - brake_rotor._RIM_OVERLAP
    hat_h, mt = v["hat_offset_mm"], v["mounting_thickness_mm"]
    bore_r = v["center_bore_mm"] / 2
    ring = E.annulus_z(R, ring_in, T)
    shell = E.annulus_z(hat_r, hat_r - v["hat_wall_thickness_mm"], hat_h - mt)
    plate = E.annulus_z(hat_r, bore_r, mt, hat_h - mt)
    return E.Union([ring, shell, plate])


def env_wheel_hub(params: dict) -> E.Shape:
    v = prepare(params, wheel_hub.SPEC, wheel_hub._derive, wheel_hub._check)
    t, blen, ph = v["flange_thickness_mm"], v["barrel_length_mm"], v["pilot_height_mm"]
    bore_r = v["bore_diameter_mm"] / 2
    parts = [E.annulus_z(wheel_hub._flange_outer_radius(v), bore_r, t)]
    if blen > 0:
        parts.append(E.annulus_z(v["barrel_diameter_mm"] / 2, bore_r, blen, -blen))
    if ph > 0:
        parts.append(E.annulus_z(v["pilot_diameter_mm"] / 2, bore_r, ph, t))
    return E.Union(parts)


def env_steering_knuckle(params: dict) -> E.Shape:
    v = prepare(params, steering_knuckle.SPEC, steering_knuckle._derive, steering_knuckle._check)
    R, Lh = v["housing_diameter_mm"] / 2, v["housing_length_mm"]
    drop, bx, bz = v["ball_joint_drop_mm"], v["ball_joint_x_mm"], v["ball_joint_z_mm"]
    w, t = v["leg_width_mm"], v["leg_thickness_mm"]
    bore_r = v["bearing_bore_diameter_mm"] / 2
    housing = E.annulus_z(R, bore_r, Lh, -Lh)
    d0 = v["seat_diameter_mm"]
    d1 = d0 - v["seat_taper_ratio"] * v["seat_depth_mm"]
    seat = E.cyl_axis((d0 + d1) / 4, v["seat_depth_mm"], (bx, -drop, bz), (0.0, 1.0, 0.0))
    leg = E.Subtract(E.box_ranges(bx - w / 2, bx + w / 2, -drop, -R * 0.4, bz - t / 2, bz + t / 2),
                     [E.cyl_z(bore_r, Lh + 2.0, -Lh - 1.0), seat])
    return E.Union([housing, leg])


def env_ball_joint(params: dict) -> E.Shape:
    v = prepare(params, ball_joint.SPEC, ball_joint._derive, ball_joint._check)
    hl, fl = v["housing_length_mm"], v["flange_thickness_mm"]
    return E.Union([E.cyl_z(v["housing_diameter_mm"] / 2, hl, -hl),
                    E.cyl_z(v["flange_diameter_mm"] / 2, fl),
                    E.cyl_z((v["stud_base_diameter_mm"] - v["stud_taper_ratio"] * v["stud_length_mm"] / 2) / 2,
                            v["stud_length_mm"], fl)])


def env_control_arm(params: dict) -> E.Shape:
    v = prepare(params, control_arm.SPEC, control_arm._derive, control_arm._check)
    S, L, t = v["pivot_spacing_mm"], v["arm_length_mm"], v["thickness_mm"]
    sx, blen = S / 2, v["bushing_length_mm"]
    bj_r, bj_h = v["ball_joint_boss_diameter_mm"] / 2, v["ball_joint_boss_height_mm"]
    bore_r = v["bushing_bore_diameter_mm"] / 2
    bj_hole = E.cyl_z(v["ball_joint_hole_diameter_mm"] / 2, bj_h + 2.0, -bj_h / 2 - 1.0, 0.0, L)
    solids = []
    for x in (-sx, sx):
        solids.append(E.tube_axis(v["bushing_outer_diameter_mm"] / 2, bore_r, blen, (x - blen / 2, 0.0, 0.0), (1.0, 0.0, 0.0)))
        leg = E.prism_xy(link_polygon((x, 0.0), (0.0, L), v["arm_width_mm"], v["arm_tip_width_mm"]), -t / 2, t / 2)
        # the real generator cuts the sleeve bore AND the ball-joint hole through
        # everything, legs included
        solids.append(E.Subtract(leg, [E.cyl_axis(bore_r, blen + 2.0, (x - blen / 2 - 1.0, 0.0, 0.0), (1.0, 0.0, 0.0)), bj_hole]))
    solids.append(E.annulus_z(bj_r, v["ball_joint_hole_diameter_mm"] / 2, bj_h, -bj_h / 2).__class__(
        bj_r, v["ball_joint_hole_diameter_mm"] / 2, bj_h, E.Transform(E.M.IDENTITY, (0.0, L, -bj_h / 2))))
    if v["cross_brace"]:
        bw = v["brace_width_mm"]
        solids.append(E.Subtract(E.box_ranges(-sx, sx, -bw / 2, bw / 2, -t / 2, t / 2),
                                 [E.cyl_axis(bore_r, 2 * sx + blen + 4.0, (-sx - blen / 2 - 2.0, 0.0, 0.0), (1.0, 0.0, 0.0))]))
    return E.Union(solids)


def env_subframe(params: dict) -> E.Shape:
    v = prepare(params, subframe.SPEC, subframe._derive, subframe._check)
    L, S, gap, e = v["length_mm"], v["pivot_spacing_mm"], v["clevis_gap_mm"], v["ear_thickness_mm"]
    py, eh, ew = v["pivot_y_mm"], v["ear_height_mm"], v["ear_width_mm"]
    lay = subframe._layout(v)
    solids, cuts = [], []
    for side in (1.0, -1.0):
        y = side * py
        solids.append(E.box_ranges(-L / 2, L / 2, y - v["rail_width_mm"] / 2, y + v["rail_width_mm"] / 2, lay["rail_z0"], lay["rail_z1"]))
        for xc in (S / 2, -S / 2):
            for ex in (xc - gap / 2 - e / 2, xc + gap / 2 + e / 2):
                solids.append(E.box_ranges(ex - e / 2, ex + e / 2, y - ew / 2, y + ew / 2, -eh / 2, eh / 2 - subframe._EAR_OVERLAP + 1.0))
            cuts.append(E.cyl_axis(v["ear_bore_diameter_mm"] / 2, gap + 4 * e, (xc - gap / 2 - 2 * e, y, 0.0), (1.0, 0.0, 0.0)))
    cw = v["crossmember_width_mm"]
    for cx in (lay["cross_x"], -lay["cross_x"]):
        solids.append(E.box_ranges(cx - cw / 2, cx + cw / 2, -py, py, lay["rail_z0"], lay["rail_z1"]))
    return E.Subtract(E.Union(solids), cuts)


ENVELOPES = {
    "brake_rotor": env_brake_rotor, "wheel_hub": env_wheel_hub, "steering_knuckle": env_steering_knuckle,
    "ball_joint": env_ball_joint, "control_arm": env_control_arm, "subframe": env_subframe,
}
