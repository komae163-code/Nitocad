"""
Piston (gasoline-style, flat or dished crown, up to 3 ring grooves).

Axis is Z; crown is at z = height_mm, open skirt end at z = 0. The piston pin
axis runs along X at z = height_mm - compression_height_mm.

Construction:
  1. solid cylinder (diameter_mm x height_mm)
  2. ring grooves cut as annuli from the top land down
  3. optional dish (flat-bottomed bowl) cut into the crown
  4. two-stage underside cavity: thick "ring belt" wall behind the grooves,
     thinner skirt wall below them. The pin bosses (step 5) are unioned after
     the cavity and may rise into the belt region, as on a real piston
  5. two pin bosses (cylinders along X, clipped to the piston envelope)
  6. pin bore through the whole piston along X

NOT modelled: skirt profile/ovality/taper (real pistons are machined
oval and barrel-shaped; this is a true cylinder), valve reliefs, oil
galleries/drain holes, coatings, pin circlip grooves, ring land
chamfers. Diameter is the nominal piston diameter; cold clearance to the bore
is the user's responsibility. Dimensional geometry only - not a
thermally or structurally rated component.
"""
from __future__ import annotations

from validator import ValidationResult
from domain_packs.specs import P
from . import calc
from .common import fix, prepare, run_validation

SPEC = {
    "diameter_mm":                  P(86.0, 30.0, 200.0),
    "height_mm":                    P(62.0, 20.0, 200.0),
    "compression_height_mm":        P(32.0, 10.0, 120.0),
    "crown_thickness_mm":           P(8.0, 3.0, 30.0),
    "skirt_wall_thickness_mm":      P(4.0, 2.5, 15.0),
    "pin_bore_diameter_mm":         P(22.0, 6.0, 60.0),
    "pin_boss_diameter_mm":         P(None, 10.0, 100.0),
    "pin_boss_gap_mm":              P(None, 10.0, 160.0),
    "ring_groove_count":            P(3, 0, 3, kind="int"),
    "top_land_height_mm":           P(6.0, 2.0, 20.0),
    "ring_land_height_mm":          P(3.5, 1.5, 10.0),
    "compression_ring_width_mm":    P(1.2, 0.8, 4.0),
    "second_ring_width_mm":         P(1.5, 0.8, 4.0),
    "oil_ring_width_mm":            P(3.0, 1.5, 6.0),
    "ring_groove_depth_mm":         P(3.5, 1.0, 8.0),
    "crown_style":                  P("flat", kind="choice", choices=("flat", "dish")),
    "bowl_diameter_mm":             P(None, 5.0, 150.0),
    "bowl_depth_mm":                P(5.0, 0.5, 20.0),
}


def _derive(v: dict) -> dict:
    if v["pin_boss_diameter_mm"] is None:
        v["pin_boss_diameter_mm"] = round(v["pin_bore_diameter_mm"] + 14.0, 3)
    if v["pin_boss_gap_mm"] is None:
        v["pin_boss_gap_mm"] = round(v["diameter_mm"] * 0.58, 3)
    if v["bowl_diameter_mm"] is None:
        v["bowl_diameter_mm"] = round(v["diameter_mm"] * 0.6, 3)
    return v


def _groove_widths(v: dict) -> list[float]:
    return [v["compression_ring_width_mm"], v["second_ring_width_mm"], v["oil_ring_width_mm"]][: v["ring_groove_count"]]


def _pack_depth(v: dict) -> float:
    """Distance from the crown down to the bottom of the last ring groove."""
    widths = _groove_widths(v)
    if not widths:
        return v["top_land_height_mm"]
    return v["top_land_height_mm"] + sum(widths) + (len(widths) - 1) * v["ring_land_height_mm"]


def _check(v: dict, res: ValidationResult) -> None:
    R = v["diameter_mm"] / 2
    H = v["height_mm"]
    z_pin = H - v["compression_height_mm"]
    boss_r = v["pin_boss_diameter_mm"] / 2

    if v["pin_boss_diameter_mm"] < v["pin_bore_diameter_mm"] + 6.0:
        fix(res, v, "pin_boss_diameter_mm", v["pin_bore_diameter_mm"] + 6.0,
            "pin boss needs at least 3mm of wall around the pin bore")
        boss_r = v["pin_boss_diameter_mm"] / 2
    if v["compression_height_mm"] >= H:
        res.add_error("compression_height_mm must be less than height_mm")
        return
    if z_pin < boss_r + 1.5:
        res.add_error(
            f"skirt too short: pin axis sits {z_pin:g}mm above the skirt edge but the pin boss "
            f"needs {boss_r + 1.5:g}mm; increase height_mm or reduce compression_height_mm")
        return
    if v["diameter_mm"] < v["pin_boss_diameter_mm"] + 12.0:
        res.add_error("diameter_mm too small for this pin boss size")
        return
    if R - v["pin_boss_gap_mm"] / 2 < 6.0:
        res.add_error("pin_boss_gap_mm leaves under 6mm of boss; reduce the gap")
        return
    if v["pin_boss_gap_mm"] < v["pin_bore_diameter_mm"] * 0.9:
        res.add_warning("pin_boss_gap_mm is small relative to the pin: the connecting-rod small end will not fit between the bosses")

    pack = _pack_depth(v)
    land = v["ring_land_height_mm"]
    groove_bottom_z = H - pack
    z_step = groove_bottom_z - land   # underside cavity step below the last groove
    if v["ring_groove_depth_mm"] > R * 0.2:
        res.add_error("ring_groove_depth_mm is more than 20% of the radius")
        return
    pin_top = z_pin + v["pin_bore_diameter_mm"] / 2
    if groove_bottom_z < pin_top + 1.5:
        res.add_error(
            f"ring pack ({pack:g}mm below the crown) reaches the pin bore (needs 1.5mm clear above it); "
            "increase compression_height_mm or reduce ring grooves/lands")
        return
    if z_step < 2.0:
        res.add_error("ring pack leaves no room for the skirt cavity; reduce grooves/lands or increase height_mm")
        return
    belt_wall = v["ring_groove_depth_mm"] + 3.0
    if v["skirt_wall_thickness_mm"] >= R - belt_wall:
        res.add_error("skirt_wall_thickness_mm leaves no cavity")
        return

    if v["crown_style"] == "dish":
        if v["bowl_diameter_mm"] / 2 > R - 6.0:
            fix(res, v, "bowl_diameter_mm", 2 * (R - 6.0), "bowl would break through the crown edge")
        if v["bowl_depth_mm"] > v["crown_thickness_mm"] - 2.0:
            fix(res, v, "bowl_depth_mm", v["crown_thickness_mm"] - 2.0,
                "bowl would leave less than 2mm of crown above the cavity")


def validate_piston(params: dict) -> ValidationResult:
    return run_validation(params, SPEC, _derive, _check)


def generate_piston(params: dict):
    from . import geom
    v = prepare(params, SPEC, _derive, _check)
    R = v["diameter_mm"] / 2
    H = v["height_mm"]
    z_pin = H - v["compression_height_mm"]
    boss_r = v["pin_boss_diameter_mm"] / 2
    gap = v["pin_boss_gap_mm"]

    body = geom.cyl(R, H)

    # --- ring grooves ---------------------------------------------------------
    z_top = H - v["top_land_height_mm"]
    for w in _groove_widths(v):
        body = body.cut(geom.annulus(R + 1.0, R - v["ring_groove_depth_mm"], w, z_top - w))
        z_top -= w + v["ring_land_height_mm"]

    # --- crown dish -----------------------------------------------------------
    if v["crown_style"] == "dish":
        body = body.cut(geom.cyl(v["bowl_diameter_mm"] / 2, v["bowl_depth_mm"] + 1.0, H - v["bowl_depth_mm"]))

    # --- two-stage underside cavity ------------------------------------------
    z_step = H - _pack_depth(v) - v["ring_land_height_mm"]
    belt_r = R - (v["ring_groove_depth_mm"] + 3.0)
    skirt_r = R - v["skirt_wall_thickness_mm"]
    z_cav_top = H - v["crown_thickness_mm"]
    z_low_top = min(z_step, z_cav_top)
    body = body.cut(geom.cyl(skirt_r, z_low_top + 1.0, -1.0))
    if z_cav_top > z_step:
        body = body.cut(geom.cyl(belt_r, z_cav_top - z_step + 0.5, z_step - 0.5))

    # --- pin bosses (clipped to the piston envelope) --------------------------
    envelope = geom.cyl(R, H)
    right = geom.axis_cyl(boss_r, R, (gap / 2, 0.0, z_pin), (1.0, 0.0, 0.0)).intersect(envelope)
    left = geom.axis_cyl(boss_r, R, (-gap / 2, 0.0, z_pin), (-1.0, 0.0, 0.0)).intersect(envelope)
    body = body.union(right).union(left)

    # --- pin bore ---------------------------------------------------------------
    pin = geom.axis_cyl(v["pin_bore_diameter_mm"] / 2, 2 * R + 4.0, (-(R + 2.0), 0.0, z_pin), (1.0, 0.0, 0.0))
    return body.cut(pin)
