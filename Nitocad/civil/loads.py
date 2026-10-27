"""
Basic loads: self weight, Rankine earth pressure, and the loads on a box culvert.

Rankine theory assumes a smooth vertical wall, level backfill and a cohesionless
soil. These are the textbook first-pass loads, and they stop at loads: no
reinforcement or member design is done anywhere in NitoCAD.
"""
from __future__ import annotations

import math

from .errors import CivilInputError

CONCRETE_UNIT_WEIGHT = 24.0     # kN/m3, plain/reinforced concrete is often taken as 24 to 25


def _pos(**kw):
    for k, v in kw.items():
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            raise CivilInputError(f"{k} must be a number above zero")


def rankine_ka(phi_deg: float) -> float:
    if not (0 <= phi_deg < 90):
        raise CivilInputError("Friction angle must be from 0 to under 90 degrees")
    return math.tan(math.radians(45.0 - phi_deg / 2.0)) ** 2


def rankine_kp(phi_deg: float) -> float:
    if not (0 <= phi_deg < 90):
        raise CivilInputError("Friction angle must be from 0 to under 90 degrees")
    return math.tan(math.radians(45.0 + phi_deg / 2.0)) ** 2


def polygon_area_centroid(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Area and centroid (x, y) of a simple polygon."""
    a = cx = cy = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    a *= 0.5
    if abs(a) < 1e-12:
        raise CivilInputError("A part of the wall has no area, check the dimensions")
    return abs(a), cx / (6.0 * a), cy / (6.0 * a)


def culvert_loads(clear_width_m: float, clear_height_m: float, wall_thickness_m: float,
                  slab_thickness_m: float, barrels: int, cover_height_m: float,
                  soil_unit_weight: float = 18.0, soil_phi_deg: float = 30.0,
                  live_surcharge_kpa: float = 0.0,
                  concrete_unit_weight: float = CONCRETE_UNIT_WEIGHT) -> dict:
    """Pressures on a buried box culvert, per metre length of culvert.

    Vertical: earth prism over the roof (gamma x cover) plus a live surcharge you
    supply. Lateral: Rankine active pressure at roof and floor level. Self weight
    of the concrete is reported per metre and as base pressure.
    """
    _pos(clear_width_m=clear_width_m, clear_height_m=clear_height_m, wall_thickness_m=wall_thickness_m,
         slab_thickness_m=slab_thickness_m, soil_unit_weight=soil_unit_weight, concrete_unit_weight=concrete_unit_weight)
    if barrels < 1 or int(barrels) != barrels:
        raise CivilInputError("barrels must be a whole number of 1 or more")
    if cover_height_m < 0 or live_surcharge_kpa < 0:
        raise CivilInputError("cover_height_m and live_surcharge_kpa cannot be negative")
    n = int(barrels)
    outer_w = n * clear_width_m + (n + 1) * wall_thickness_m
    outer_h = clear_height_m + 2.0 * slab_thickness_m
    concrete_area = outer_w * outer_h - n * clear_width_m * clear_height_m
    self_weight = concrete_area * concrete_unit_weight                    # kN per m length
    ka = rankine_ka(soil_phi_deg)
    earth_on_roof = soil_unit_weight * cover_height_m                      # kPa
    roof = earth_on_roof + live_surcharge_kpa
    lateral_top = ka * roof
    lateral_bottom = ka * (roof + soil_unit_weight * outer_h)
    vertical_total = roof * outer_w + self_weight
    return {
        "outer_width_m": outer_w, "outer_height_m": outer_h, "ka": ka,
        "self_weight_kn_per_m": self_weight,
        "earth_pressure_on_roof_kpa": earth_on_roof,
        "live_surcharge_kpa": live_surcharge_kpa,
        "roof_pressure_kpa": roof,
        "lateral_pressure_at_roof_level_kpa": lateral_top,
        "lateral_pressure_at_floor_level_kpa": lateral_bottom,
        "total_vertical_load_kn_per_m": vertical_total,
        "base_pressure_kpa": vertical_total / outer_w,
        "notes": ["Loads only. Member design, reinforcement and a live load model such as wheel loads "
                  "must be done by a licensed engineer using the governing code."],
    }
