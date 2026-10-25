"""
Drainage hydrology and hydraulics, screening level.

  * Rational method peak flow, Kirpich time of concentration, an IDF curve from
    coefficients you supply (no rainfall data is built in: it depends on the site)
  * Manning's equation for circular pipes, trapezoidal channels and box sections
  * Pipe size selection, channel normal and critical depth
  * Box culvert capacity: barrel flow by Manning and an inlet control curve

The culvert inlet curve is a simplified, continuity-anchored weir and orifice
curve. Use it to size and screen. Confirm a final culvert with the method your
road agency requires (for example FHWA HDS-5 or HY-8) before relying on it.
"""
from __future__ import annotations

import math

from .errors import CivilInputError

G = 9.80665
STANDARD_PIPE_DIAMETERS_MM = (300, 375, 450, 525, 600, 750, 900, 1050, 1200, 1350, 1500, 1800, 2100, 2400)


def _need_positive(**kw) -> None:
    for k, v in kw.items():
        if not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
            raise CivilInputError(f"{k} must be a number above zero")


# ------------------------------------------------------------------ hydrology
def rational_flow(runoff_coeff: float, intensity_mm_h: float, area_ha: float) -> float:
    """Peak flow in m3/s: Q = C i A / 360 with i in mm/h and A in hectares."""
    if not (0 < runoff_coeff <= 1):
        raise CivilInputError("runoff_coeff must be above 0 and at most 1")
    _need_positive(intensity_mm_h=intensity_mm_h, area_ha=area_ha)
    return runoff_coeff * intensity_mm_h * area_ha / 360.0


def kirpich_tc_min(flow_length_m: float, slope_m_per_m: float) -> float:
    """Time of concentration in minutes (Kirpich, SI): 0.0195 L^0.77 S^-0.385."""
    _need_positive(flow_length_m=flow_length_m, slope_m_per_m=slope_m_per_m)
    return 0.0195 * flow_length_m ** 0.77 * slope_m_per_m ** -0.385


def idf_intensity_mm_h(a: float, b: float, c: float, duration_min: float) -> float:
    """i = a / (t + b)^c, the common three-coefficient IDF form. You supply a, b, c."""
    _need_positive(a=a, c=c, duration_min=duration_min)
    if b < 0:
        raise CivilInputError("b cannot be negative")
    return a / (duration_min + b) ** c


# --------------------------------------------------------------- section maths
def _bisect(f, lo: float, hi: float, iters: int = 200) -> float:
    flo = f(lo)
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def manning_q(area: float, hyd_radius: float, slope: float, n: float) -> float:
    return (1.0 / n) * area * hyd_radius ** (2.0 / 3.0) * math.sqrt(slope)


def _circ_geom(d: float, y: float) -> tuple[float, float, float]:
    """Area, wetted perimeter, top width of a circular pipe filled to depth y."""
    y = min(max(y, 0.0), d)
    theta = 2.0 * math.acos(1.0 - 2.0 * y / d)
    area = d * d / 8.0 * (theta - math.sin(theta))
    perim = d * theta / 2.0
    top = d * math.sin(theta / 2.0)
    return area, perim, top


def pipe_flow(diameter_m: float, slope: float, n: float, flow: float | None = None) -> dict:
    """Full-pipe capacity and, if a flow is given, the partly full state."""
    _need_positive(diameter_m=diameter_m, slope=slope, n=n)
    a_full = math.pi * diameter_m ** 2 / 4.0
    q_full = manning_q(a_full, diameter_m / 4.0, slope, n)
    # Highest free-surface flow occurs near 0.938 of the diameter.
    q_max = max(_q_at(diameter_m, diameter_m * f, slope, n) for f in [i / 200 for i in range(100, 200)])
    out = {"diameter_m": diameter_m, "slope": slope, "n": n,
           "q_full_m3s": q_full, "v_full_ms": q_full / a_full, "q_max_free_surface_m3s": q_max}
    if flow is not None:
        _need_positive(flow=flow)
        out["flow_m3s"] = flow
        if flow > q_max:
            out.update({"status": "exceeds_capacity", "depth_m": None, "fill_ratio": None,
                        "velocity_ms": None, "froude": None})
        else:
            peak_y = diameter_m * 0.938   # flow rises with depth up to about here
            y = _bisect(lambda yy: _q_at(diameter_m, yy, slope, n) - flow, 1e-6, peak_y)
            a, p, t = _circ_geom(diameter_m, y)
            v = flow / a
            out.update({"status": "ok", "depth_m": y, "fill_ratio": y / diameter_m,
                        "velocity_ms": v, "froude": v / math.sqrt(G * a / t)})
    return out


def _q_at(d: float, y: float, slope: float, n: float) -> float:
    a, p, _ = _circ_geom(d, y)
    return manning_q(a, a / p, slope, n) if a > 0 else 0.0


def select_pipe(flow: float, slope: float, n: float, max_fill_ratio: float = 0.8,
                diameters_mm=STANDARD_PIPE_DIAMETERS_MM) -> dict:
    """Smallest standard diameter that carries the flow at or below max_fill_ratio."""
    _need_positive(flow=flow, slope=slope, n=n)
    if not (0.1 <= max_fill_ratio <= 0.94):
        raise CivilInputError("max_fill_ratio must be between 0.1 and 0.94")
    for dmm in diameters_mm:
        r = pipe_flow(dmm / 1000.0, slope, n, flow)
        if r["status"] == "ok" and r["fill_ratio"] <= max_fill_ratio + 1e-9:
            r["selected_diameter_mm"] = dmm
            return r
    raise CivilInputError("No standard pipe is large enough at that slope and fill. Use a bigger pipe, a steeper slope, or several pipes")


def trapezoid_normal_depth(flow: float, bottom_width: float, side_slope: float, slope: float, n: float) -> dict:
    """Normal depth, velocity and Froude number in a trapezoidal channel
    (side_slope = horizontal run per 1 vertical; 0 gives a rectangle)."""
    _need_positive(flow=flow, bottom_width=bottom_width, slope=slope, n=n)
    if side_slope < 0:
        raise CivilInputError("side_slope cannot be negative")
    b, z = bottom_width, side_slope

    def geom(y):
        a = (b + z * y) * y
        p = b + 2.0 * y * math.sqrt(1.0 + z * z)
        t = b + 2.0 * z * y
        return a, p, t

    def f(y):
        a, p, _ = geom(y)
        return manning_q(a, a / p, slope, n) - flow

    hi = 1.0
    while f(hi) < 0:
        hi *= 2.0
        if hi > 100:
            raise CivilInputError("The channel cannot carry that flow at a depth under 100 m, check the inputs")
    y = _bisect(f, 1e-6, hi)
    a, p, t = geom(y)
    v = flow / a
    fr = v / math.sqrt(G * a / t)
    yc = _bisect(lambda yy: flow * flow / G - geom(yy)[0] ** 3 / geom(yy)[2], 1e-6, hi)
    return {"normal_depth_m": y, "critical_depth_m": yc, "velocity_ms": v, "froude": fr,
            "regime": "subcritical" if fr < 1 else "supercritical", "flow_area_m2": a, "top_width_m": t,
            "flow_m3s": flow}


# ------------------------------------------------------------------- culverts
def _box_partial(width: float, height: float, y: float) -> tuple[float, float]:
    y = min(max(y, 0.0), height)
    return width * y, width + 2.0 * y


def box_culvert_check(flow: float, clear_width_m: float, clear_height_m: float, length_m: float,
                      slope: float, n: float = 0.013, barrels: int = 1,
                      weir_coeff: float = 1.5, orifice_coeff: float = 0.6) -> dict:
    """Screening check of a box culvert with `barrels` equal cells carrying `flow`.

    Barrel: normal depth and velocity by Manning at the culvert slope, plus the
    full-barrel capacity. Inlet: headwater depth from a weir curve up to
    HW/D = 1.2, then an orifice curve anchored to it (continuous, increasing).
    """
    _need_positive(flow=flow, clear_width_m=clear_width_m, clear_height_m=clear_height_m,
                   length_m=length_m, slope=slope, n=n, weir_coeff=weir_coeff, orifice_coeff=orifice_coeff)
    if barrels < 1 or int(barrels) != barrels:
        raise CivilInputError("barrels must be a whole number of 1 or more")
    q = flow / barrels
    B, D = clear_width_m, clear_height_m

    a_full, p_full = B * D, 2.0 * (B + D)
    q_barrel_full = manning_q(a_full, a_full / p_full, slope, n)

    def q_manning(y):
        a, p = _box_partial(B, D, y)
        return manning_q(a, a / p, slope, n) if a > 0 else 0.0

    barrel = {"capacity_full_per_barrel_m3s": q_barrel_full,
              "capacity_full_total_m3s": q_barrel_full * barrels}
    if q > q_barrel_full:
        barrel.update({"status": "exceeds_barrel_capacity", "depth_m": None, "velocity_ms": None, "froude": None})
    else:
        y = _bisect(lambda yy: q_manning(yy) - q, 1e-6, D)
        a, _ = _box_partial(B, D, y)
        v = q / a
        barrel.update({"status": "ok", "depth_m": y, "velocity_ms": v, "froude": v / math.sqrt(G * y)})

    h_t = 1.2 * D
    q_t = weir_coeff * B * h_t ** 1.5

    def q_inlet(h):
        if h <= h_t:
            return weir_coeff * B * h ** 1.5
        return q_t * math.sqrt((h - D / 2.0) / (h_t - D / 2.0))

    hw = _bisect(lambda hh: q_inlet(hh) - q, 1e-6, 50.0 * D)
    inlet = {"headwater_m": hw, "headwater_to_depth": hw / D,
             "regime": "unsubmerged (weir)" if hw <= h_t else "submerged (orifice)",
             "weir_coeff": weir_coeff, "orifice_coeff": orifice_coeff}
    return {
        "inputs": {"flow_m3s": flow, "barrels": int(barrels), "flow_per_barrel_m3s": q,
                   "clear_width_m": B, "clear_height_m": D, "length_m": length_m, "slope": slope, "n": n},
        "barrel": barrel, "inlet": inlet,
        "notes": ["Screening level. Inlet control only: outlet control, tailwater and headloss are not checked.",
                  "Confirm the final size with your road agency's culvert method."],
    }
