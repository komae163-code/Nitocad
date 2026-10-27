"""
Earthworks and material quantities along an alignment.

For each station a cross-section is cut through the terrain. The design section
is a crowned formation (carriageway plus shoulders) with straight side slopes
that run out to meet the ground (catch points). Cut and fill areas are found
between the ground line and the design line, and volumes between stations use
the average end area method.

Assumptions worth knowing:
  * Average end area overstates volume a little where a section changes from
    cut to fill, and on sharp curves. It is the common first estimate, not a
    final quantity.
  * The formation level is the finished surface at the centre line minus the
    pavement structure thickness. Side drains and ditches are not modelled.
  * Fill and cut volumes are reported in place (bank) measure. Apply your own
    shrink or swell factor with fill_factor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .alignment import HorizontalAlignment, VerticalAlignment, stations_for
from .errors import CivilInputError
from .terrain import Terrain


@dataclass
class RoadTemplate:
    lane_width_m: float = 3.5
    lane_count: int = 2
    shoulder_width_m: float = 1.5
    crossfall_pct: float = 2.5
    shoulder_crossfall_pct: float = 4.0
    pavement_thickness_m: float = 0.3
    cut_slope: float = 2.0          # horizontal run per 1 vertical, in cut
    fill_slope: float = 2.0         # horizontal run per 1 vertical, in fill
    has_kerbs: bool = False
    pavement_layers: tuple = ()     # ((name, thickness_m), ...) optional breakdown

    def validate(self) -> None:
        if not (1 <= self.lane_count <= 8 and int(self.lane_count) == self.lane_count):
            raise CivilInputError("lane_count must be a whole number from 1 to 8")
        for name, v, lo, hi in (
            ("lane_width_m", self.lane_width_m, 2.0, 6.0),
            ("shoulder_width_m", self.shoulder_width_m, 0.0, 5.0),
            ("crossfall_pct", self.crossfall_pct, 0.0, 8.0),
            ("shoulder_crossfall_pct", self.shoulder_crossfall_pct, 0.0, 10.0),
            ("pavement_thickness_m", self.pavement_thickness_m, 0.0, 2.0),
            ("cut_slope", self.cut_slope, 0.25, 10.0),
            ("fill_slope", self.fill_slope, 0.25, 10.0),
        ):
            if not (lo <= v <= hi) or not math.isfinite(v):
                raise CivilInputError(f"{name} must be between {lo} and {hi}")
        if self.pavement_layers:
            total = sum(t for _, t in self.pavement_layers)
            if abs(total - self.pavement_thickness_m) > 1e-6:
                raise CivilInputError(
                    f"The pavement layers add to {total:.3f} m but pavement_thickness_m is {self.pavement_thickness_m:.3f} m"
                )

    @property
    def half_carriageway(self) -> float:
        return self.lane_count * self.lane_width_m / 2.0

    @property
    def half_formation(self) -> float:
        return self.half_carriageway + self.shoulder_width_m


def _formation_drop(t: RoadTemplate, off: float) -> float:
    """How far the formation falls below its centre line value at |off|."""
    a = abs(off)
    inside = min(a, t.half_carriageway)
    outside = max(0.0, min(a, t.half_formation) - t.half_carriageway)
    return inside * t.crossfall_pct / 100.0 + outside * t.shoulder_crossfall_pct / 100.0


def _section(terrain: Terrain, h: HorizontalAlignment, station: float, z_form_c: float,
             t: RoadTemplate, step: float, reach: float) -> dict:
    half_f = t.half_formation
    width = half_f + reach
    offsets = [i * step for i in range(int(math.ceil(width / step)) + 1)]

    def ground_line(sign: int):
        zs, last, partial = [], None, False
        for o in offsets:
            x, y = h.offset_point(station, sign * o)
            z = terrain.z_at(x, y)
            if z is None:
                partial = True
                z = last
            else:
                last = z
            zs.append(z)
        # Leading Nones (centre line outside the surface) stay None.
        return zs, partial

    left, p_l = ground_line(+1)
    right, p_r = ground_line(-1)
    if left[0] is None:
        return {"station": station, "status": "outside_survey"}

    def interp(zs, o):
        k = min(int(o / step), len(zs) - 2)
        a, b = zs[k], zs[k + 1]
        if a is None or b is None:
            return a if b is None else b
        return a + (b - a) * (o - offsets[k]) / step

    def design(o_abs):
        if o_abs <= half_f:
            return z_form_c - _formation_drop(t, o_abs)
        return None

    z_edge = z_form_c - _formation_drop(t, half_f)

    def catch(zs):
        g_edge = interp(zs, half_f)
        if g_edge is None:
            return half_f, 0.0, False
        if abs(g_edge - z_edge) < 1e-9:
            return half_f, 0.0, True
        cut = g_edge > z_edge
        ratio = t.cut_slope if cut else t.fill_slope
        sgn = 1.0 if cut else -1.0
        o = half_f
        fine = 0.05
        while o < width:
            o_next = min(o + fine, width)
            slope_z = z_edge + sgn * (o_next - half_f) / ratio
            g = interp(zs, o_next)
            if g is not None and ((cut and slope_z >= g) or ((not cut) and slope_z <= g)):
                return o_next, sgn, True
            o = o_next
        return width, sgn, False

    off_l, sgn_l, ok_l = catch(left)
    off_r, sgn_r, ok_r = catch(right)

    def design_at(o_abs, sgn, ratio_cut, ratio_fill):
        if o_abs <= half_f:
            return z_form_c - _formation_drop(t, o_abs)
        ratio = ratio_cut if sgn > 0 else ratio_fill
        return z_edge + sgn * (o_abs - half_f) / ratio

    cut_a = fill_a = 0.0
    fine = 0.05
    for zs, off, sgn in ((left, off_l, sgn_l), (right, off_r, sgn_r)):
        n = max(1, int(math.ceil(off / fine)))
        d_o = off / n
        prev = None
        for k in range(n + 1):
            o = k * d_o
            g = interp(zs, o)
            d = design_at(o, sgn if o > half_f else 0.0, t.cut_slope, t.fill_slope)
            diff = (g - d) if g is not None else 0.0
            cur = (max(diff, 0.0), max(-diff, 0.0))
            if prev is not None:
                cut_a += (prev[0] + cur[0]) / 2.0 * d_o
                fill_a += (prev[1] + cur[1]) / 2.0 * d_o
            prev = cur

    ground_c = left[0]
    return {
        "station": station, "status": "ok",
        "ground_z": ground_c, "formation_z": z_form_c,
        "cut_area_m2": cut_a, "fill_area_m2": fill_a,
        "left_catch_offset_m": off_l, "right_catch_offset_m": off_r,
        "width_m": off_l + off_r,
        "daylight_found": bool(ok_l and ok_r),
        "partial_data": bool(p_l or p_r),
    }


def compute_earthworks(
    terrain: Terrain,
    horizontal: HorizontalAlignment,
    vertical: VerticalAlignment,
    template: RoadTemplate,
    *,
    station_interval_m: float = 20.0,
    section_step_m: float = 0.5,
    max_slope_reach_m: float = 25.0,
    fill_factor: float = 1.0,
    topsoil_strip_depth_m: float = 0.0,
) -> dict:
    template.validate()
    if not (0.1 <= section_step_m <= 2.0):
        raise CivilInputError("section_step_m must be between 0.1 and 2.0")
    if not (1.0 <= max_slope_reach_m <= 100.0):
        raise CivilInputError("max_slope_reach_m must be between 1 and 100")
    if fill_factor <= 0 or topsoil_strip_depth_m < 0:
        raise CivilInputError("fill_factor must be above 0 and topsoil_strip_depth_m cannot be negative")
    if vertical.end + 1e-6 < horizontal.length or vertical.start > 1e-6:
        raise CivilInputError(
            f"The vertical alignment ({vertical.start:.1f} to {vertical.end:.1f} m) must cover the "
            f"horizontal alignment (0 to {horizontal.length:.1f} m)"
        )

    stations = stations_for(horizontal.length, station_interval_m, horizontal.key_stations())
    rows: list[dict] = []
    for s in stations:
        z_crown = vertical.elevation(s)
        rows.append(_section(terrain, horizontal, s, z_crown - template.pavement_thickness_m,
                             template, section_step_m, max_slope_reach_m))

    warnings: list[str] = []
    cum = 0.0
    total_cut = total_fill = strip_area = 0.0
    prev = None
    for r in rows:
        r["cut_volume_m3"] = r["fill_volume_m3"] = 0.0
        if r["status"] == "ok" and prev is not None and prev["status"] == "ok":
            ds = r["station"] - prev["station"]
            r["cut_volume_m3"] = (prev["cut_area_m2"] + r["cut_area_m2"]) / 2.0 * ds
            r["fill_volume_m3"] = (prev["fill_area_m2"] + r["fill_area_m2"]) / 2.0 * ds
            strip_area += (prev["width_m"] + r["width_m"]) / 2.0 * ds
            total_cut += r["cut_volume_m3"]
            total_fill += r["fill_volume_m3"]
        cum += r["cut_volume_m3"] - r["fill_volume_m3"] * fill_factor
        r["mass_ordinate_m3"] = cum
        if r["status"] == "ok":
            prev = r

    outside = [r["station"] for r in rows if r["status"] == "outside_survey"]
    if outside:
        warnings.append(
            f"{len(outside)} station(s) are outside the surveyed surface and were left out of the volumes "
            f"(first at {outside[0]:.1f} m). Survey more of the corridor"
        )
    if any(r.get("partial_data") for r in rows):
        warnings.append("Some sections run past the edge of the survey. The ground there is held level, so volumes near the edges are approximate")
    if any(r["status"] == "ok" and not r["daylight_found"] for r in rows):
        warnings.append(f"The side slopes did not meet the ground within {max_slope_reach_m:g} m at some stations. Raise max_slope_reach_m or change the levels")

    length = horizontal.length
    carriageway_w = template.lane_count * template.lane_width_m
    layers = list(template.pavement_layers) or [("Pavement structure", template.pavement_thickness_m)]
    quantities = {
        "length_m": length,
        "carriageway_width_m": carriageway_w,
        "carriageway_area_m2": carriageway_w * length,
        "shoulder_area_m2": 2 * template.shoulder_width_m * length,
        "pavement_layers": [
            {"name": n, "thickness_m": th, "volume_m3": th * (carriageway_w + 2 * template.shoulder_width_m) * length}
            for n, th in layers
        ],
        "kerb_length_m": 2 * length if template.has_kerbs else 0.0,
        "topsoil_strip_area_m2": strip_area,
        "topsoil_strip_volume_m3": strip_area * topsoil_strip_depth_m,
        "cut_m3": total_cut,
        "fill_m3": total_fill,
        "fill_m3_with_factor": total_fill * fill_factor,
        "net_m3": total_cut - total_fill * fill_factor,
    }
    return {"stations": rows, "quantities": quantities, "warnings": warnings,
            "assumptions": {
                "volume_method": "average end area",
                "cut_slope_h_per_v": template.cut_slope, "fill_slope_h_per_v": template.fill_slope,
                "fill_factor": fill_factor, "section_step_m": section_step_m,
                "side_drains": "not modelled",
            }}
