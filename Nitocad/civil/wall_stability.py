"""
Cantilever retaining wall stability: overturning, sliding and base pressure.

Method (textbook, level backfill, per metre length of wall):
  * Rankine active thrust on a vertical plane through the heel, plus a uniform
    surcharge on the backfill.
  * Weights of the stem, base slab and the soil over the heel (and the surcharge
    over the heel), each with its own lever arm to the toe.
  * Overturning about the toe, sliding on the base with friction, and the base
    pressure from the resultant's position.

Not included: water behind the wall, seismic load, passive resistance (unless you
switch it on and give an embedment), slope above the wall, global slope
stability, settlement, structural design of the stem and slab.
"""
from __future__ import annotations

import math

from . import loads
from .errors import CivilInputError


def check_wall(
    *, stem_height_m: float, stem_thickness_base_m: float, stem_thickness_top_m: float,
    base_width_m: float, base_thickness_m: float, toe_length_m: float,
    backfill_unit_weight: float = 18.0, backfill_phi_deg: float = 30.0,
    foundation_phi_deg: float = 30.0, base_adhesion_kpa: float = 0.0,
    concrete_unit_weight: float = loads.CONCRETE_UNIT_WEIGHT,
    surcharge_kpa: float = 0.0,
    include_passive: bool = False, embedment_m: float = 0.0,
    front_soil_unit_weight: float | None = None, front_soil_phi_deg: float | None = None,
    allowable_bearing_kpa: float | None = None,
) -> dict:
    for name, v in (("stem_height_m", stem_height_m), ("stem_thickness_base_m", stem_thickness_base_m),
                    ("stem_thickness_top_m", stem_thickness_top_m), ("base_width_m", base_width_m),
                    ("base_thickness_m", base_thickness_m), ("backfill_unit_weight", backfill_unit_weight),
                    ("concrete_unit_weight", concrete_unit_weight)):
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            raise CivilInputError(f"{name} must be a number above zero")
    if toe_length_m < 0 or surcharge_kpa < 0 or base_adhesion_kpa < 0 or embedment_m < 0:
        raise CivilInputError("toe_length_m, surcharge_kpa, base_adhesion_kpa and embedment_m cannot be negative")
    if stem_thickness_top_m > stem_thickness_base_m:
        raise CivilInputError("The stem cannot be thicker at the top than at the base")
    heel_start = toe_length_m + stem_thickness_base_m
    if base_width_m <= heel_start:
        raise CivilInputError("The base is not wide enough: it must extend behind the stem to form a heel")

    B, tb, H1 = base_width_m, base_thickness_m, stem_height_m
    H = tb + H1                                       # height of the retained earth plane
    back_top = toe_length_m + stem_thickness_top_m
    front = toe_length_m

    ka = loads.rankine_ka(backfill_phi_deg)
    gamma_c, gamma_s = concrete_unit_weight, backfill_unit_weight

    # Vertical loads (kN/m) and lever arms from the toe (m).
    parts = []
    a, cx, _ = loads.polygon_area_centroid([(0, 0), (B, 0), (B, tb), (0, tb)])
    parts.append(("Base slab", a * gamma_c, cx))
    a, cx, _ = loads.polygon_area_centroid([(front, tb), (heel_start, tb), (back_top, H), (front, H)])
    parts.append(("Stem", a * gamma_c, cx))
    a, cx, _ = loads.polygon_area_centroid([(heel_start, tb), (B, tb), (B, H), (back_top, H)])
    parts.append(("Soil over heel", a * gamma_s, cx))
    if surcharge_kpa > 0:
        span = B - back_top
        parts.append(("Surcharge over heel", surcharge_kpa * span, back_top + span / 2.0))

    sum_v = sum(w for _, w, _ in parts)
    m_resist = sum(w * x for _, w, x in parts)

    pa_soil = 0.5 * ka * gamma_s * H * H
    pa_sur = ka * surcharge_kpa * H
    m_over = pa_soil * H / 3.0 + pa_sur * H / 2.0
    sum_h = pa_soil + pa_sur

    pp = 0.0
    m_pp = 0.0
    if include_passive and embedment_m > 0:
        kp = loads.rankine_kp(front_soil_phi_deg if front_soil_phi_deg is not None else foundation_phi_deg)
        gp = front_soil_unit_weight if front_soil_unit_weight is not None else backfill_unit_weight
        pp = 0.5 * kp * gp * embedment_m ** 2
        m_pp = pp * embedment_m / 3.0

    delta = math.radians(2.0 / 3.0 * foundation_phi_deg)       # base friction angle, a common assumption
    friction = sum_v * math.tan(delta) + base_adhesion_kpa * B
    fs_overturning = (m_resist + m_pp) / m_over
    fs_sliding = (friction + pp) / sum_h

    x_bar = (m_resist - m_over) / sum_v
    e = B / 2.0 - x_bar
    if abs(e) <= B / 6.0:
        q_max = sum_v / B * (1.0 + 6.0 * e / B)
        q_min = sum_v / B * (1.0 - 6.0 * e / B)
        contact = "full"
    else:
        lever = B / 2.0 - abs(e)
        q_max = 2.0 * sum_v / (3.0 * lever) if lever > 0 else float("inf")
        q_min = 0.0
        contact = "partial"
    toe_side = e >= 0                                          # resultant towards the toe

    return {
        "geometry": {"total_height_m": H, "heel_length_m": B - heel_start},
        "loads": [{"name": n, "force_kn_per_m": w, "lever_arm_m": x, "moment_about_toe_knm_per_m": w * x}
                  for n, w, x in parts],
        "earth_pressure": {"ka": ka, "active_thrust_soil_kn_per_m": pa_soil,
                           "active_thrust_surcharge_kn_per_m": pa_sur, "passive_kn_per_m": pp},
        "sum_vertical_kn_per_m": sum_v, "sum_horizontal_kn_per_m": sum_h,
        "resisting_moment_knm_per_m": m_resist + m_pp, "overturning_moment_knm_per_m": m_over,
        "factor_of_safety_overturning": fs_overturning,
        "factor_of_safety_sliding": fs_sliding,
        "base_friction_angle_deg": math.degrees(delta),
        "resultant_from_toe_m": x_bar, "eccentricity_m": e, "middle_third_limit_m": B / 6.0,
        "resultant_within_middle_third": abs(e) <= B / 6.0,
        "resultant_towards": "toe" if toe_side else "heel",
        "contact": contact, "q_max_kpa": q_max, "q_min_kpa": q_min,
        "allowable_bearing_kpa": allowable_bearing_kpa,
        "notes": ["Level backfill, Rankine thrust, no water, no seismic load, no global slope check.",
                  "Passive resistance is ignored unless include_passive is set."],
    }
