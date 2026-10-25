"""
Hand-calculation screening checks: is this part obviously too weak or too flexible?

These are closed-form textbook formulas with the assumptions printed next to every answer.
They are NOT finite-element analysis and NOT certification. They ignore stress concentrations at
holes and fillets, fatigue, buckling, joints and load paths. A "pass" means "no obvious problem
at this level of simplification". Use them to catch a 2 mm bracket asked to carry a 2 kN load
before anyone cuts metal, then confirm real parts with proper analysis.

Units: mm, N, MPa, N*mm. Pure Python.
"""
from __future__ import annotations

import math
from typing import Any

import materials
import standards

DISCLAIMER = ("Screening estimate from simplified hand formulas. Not FEA and not a design certification. "
              "Confirm any load-bearing part with proper analysis.")
PASS_LIMIT = 0.8          # utilisation at or below this is a clear pass
MAX_SAFETY_FACTOR = 20.0


class ScreeningError(ValueError):
    """The inputs cannot be checked (missing, non-numeric, or physically meaningless)."""


def _pos(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ScreeningError(f"{name} must be a positive number, got {value!r}")
    return float(value)


def _status(utilisation: float) -> str:
    return "pass" if utilisation <= PASS_LIMIT else "marginal" if utilisation <= 1.0 else "fail"


# ------------------------------------------------------------------ sections ---

def section_properties(kind: str, **d: float) -> dict[str, float]:
    """Area, second moment of area about the BENDING axis, and the distance to the extreme fibre.

    rect:       b (width, the dimension parallel to the neutral axis), h (depth, in the load direction)
    round:      d
    tube:       d_outer, d_inner
    rect_tube:  b, h, t (wall)
    i_beam:     h, b, tw, tf   (bending about the strong axis)"""
    if kind == "rect":
        b, h = _pos(d.get("b"), "b"), _pos(d.get("h"), "h")
        return {"area_mm2": b * h, "i_mm4": b * h**3 / 12, "c_mm": h / 2}
    if kind == "round":
        dd = _pos(d.get("d"), "d")
        return {"area_mm2": math.pi * dd**2 / 4, "i_mm4": math.pi * dd**4 / 64, "c_mm": dd / 2}
    if kind == "tube":
        do, di = _pos(d.get("d_outer"), "d_outer"), _pos(d.get("d_inner"), "d_inner")
        if di >= do:
            raise ScreeningError("d_inner must be smaller than d_outer")
        return {"area_mm2": math.pi * (do**2 - di**2) / 4, "i_mm4": math.pi * (do**4 - di**4) / 64, "c_mm": do / 2}
    if kind == "rect_tube":
        b, h, t = _pos(d.get("b"), "b"), _pos(d.get("h"), "h"), _pos(d.get("t"), "t")
        if 2 * t >= min(b, h):
            raise ScreeningError("wall t is too thick for this tube")
        return {"area_mm2": b * h - (b - 2 * t) * (h - 2 * t), "i_mm4": (b * h**3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12, "c_mm": h / 2}
    if kind == "i_beam":
        h, b, tw, tf = (_pos(d.get(k), k) for k in ("h", "b", "tw", "tf"))
        if 2 * tf >= h or tw >= b:
            raise ScreeningError("the flanges and web do not fit in this section")
        hw = h - 2 * tf
        return {"area_mm2": 2 * b * tf + hw * tw, "i_mm4": (b * h**3 - (b - tw) * hw**3) / 12, "c_mm": h / 2}
    raise ScreeningError(f"unknown section kind {kind!r}")


def section_for_part(part_type: str, p: dict) -> tuple[str, dict[str, float], str] | None:
    """(kind, dimensions, note) for the template parameters, or None when we cannot say.
    The weak/strong axis is a choice the caller should state; this picks bending in the
    direction of the LARGER dimension (the usual way a beam or plate is loaded) and says so."""
    g = lambda k, dflt=None: p.get(k, dflt)  # noqa: E731
    if part_type == "shaft" and g("diameter_mm"):
        return "round", {"d": g("diameter_mm")}, "solid round bar"
    if part_type == "tube":
        shape, do, w = g("shape", "round"), g("outer_diameter_mm", 20), g("wall_thickness_mm", 2)
        if shape == "round":
            return "tube", {"d_outer": do, "d_inner": do - 2 * w}, "round tube"
        b, h = (g("width_mm", do), g("height_mm", do)) if shape == "rectangular" else (do, do)
        return "rect_tube", {"b": min(b, h), "h": max(b, h), "t": w}, "bending in the deeper direction"
    if part_type == "structural_beam" and g("beam_type", "i_beam") == "i_beam":
        t = g("thickness_mm", 5)
        return "i_beam", {"h": g("height_mm", 100), "b": g("width_mm", 50), "tw": t, "tf": t}, "I-beam with web and flange both at the template thickness (a simplification)"
    if part_type == "flat_plate":
        w, t = g("width_mm", 50), g("thickness_mm", 5)
        return "rect", {"b": w, "h": t}, "plate bent about its thin direction"
    return None


# --------------------------------------------------------------------- beams ---

_BEAM_CASES = {   # (support, load): (moment coefficient, deflection coefficient, text)  M = a*F*L, d = b*F*L^3/(E I)
    ("cantilever", "end"): (1.0, 1 / 3, "cantilever, point load at the free end"),
    ("cantilever", "udl"): (1 / 2, 1 / 8, "cantilever, uniform load (F is the total load)"),
    ("simply_supported", "center"): (1 / 4, 1 / 48, "simply supported, point load at mid-span"),
    ("simply_supported", "udl"): (1 / 8, 5 / 384, "simply supported, uniform load (F is the total load)"),
}


def beam_check(section: dict[str, float], length_mm: float, load_n: float, *, support: str = "cantilever",
               load: str = "end", material: str | None = None, safety_factor: float = 1.5,
               deflection_limit_ratio: float = 250.0) -> dict[str, Any]:
    """Bending stress and deflection of a straight beam under one load."""
    L, F = _pos(length_mm, "length_mm"), _pos(load_n, "load_n")
    sf = _pos(safety_factor, "safety_factor")
    if sf > MAX_SAFETY_FACTOR:
        raise ScreeningError(f"safety_factor above {MAX_SAFETY_FACTOR:g} is not meaningful")
    if (support, load) not in _BEAM_CASES:
        raise ScreeningError(f"unsupported case {support!r}/{load!r}; choose from {sorted(_BEAM_CASES)}")
    mat = materials.lookup(material)
    if mat is None or mat.e_gpa is None:
        raise ScreeningError(f"no stiffness data for material {material!r}")
    a, b, text = _BEAM_CASES[(support, load)]
    I, c = section["i_mm4"], section["c_mm"]
    E = mat.e_gpa * 1000.0                                     # GPa -> MPa
    moment = a * F * L
    stress = moment * c / I
    deflection = b * F * L**3 / (E * I)
    checks, assumptions = [], [text, "linear elastic, small deflections, prismatic section",
                               "no stress concentration at holes, fillets or the support",
                               "self-weight ignored", f"material {mat.key}" + (" (assumed from a generic name)" if mat.generic else "")]
    if mat.yield_mpa:
        allowable = mat.yield_mpa / sf
        u = stress / allowable
        checks.append({"check": "bending stress", "value": round(stress, 2), "unit": "MPa", "limit": round(allowable, 2),
                       "utilisation": round(u, 3), "status": _status(u),
                       "detail": f"yield {mat.yield_mpa} MPa / safety factor {sf:g}"})
    else:
        checks.append({"check": "bending stress", "value": round(stress, 2), "unit": "MPa", "limit": None, "utilisation": None,
                       "status": "unknown", "detail": f"{mat.key} has no reliable yield strength in the table. {mat.note}".strip()})
    limit = L / deflection_limit_ratio
    ud = deflection / limit
    checks.append({"check": "deflection", "value": round(deflection, 3), "unit": "mm", "limit": round(limit, 3),
                   "utilisation": round(ud, 3), "status": _status(ud), "detail": f"limit L/{deflection_limit_ratio:g}, a common serviceability rule"})
    worst = max(checks, key=lambda c_: {"fail": 3, "marginal": 2, "unknown": 1, "pass": 0}[c_["status"]])["status"]
    return {"kind": "beam", "status": worst, "checks": checks, "max_moment_nmm": round(moment, 1),
            "assumptions": assumptions, "disclaimer": DISCLAIMER}


def beam_check_for_part(part_type: str, params: dict, load_n: float, *, material: str | None = None,
                        length_key: str = "length_mm", **kw) -> dict[str, Any]:
    spec = section_for_part(part_type, params)
    if spec is None:
        raise ScreeningError(f"no beam model for part type {part_type!r}")
    kind, dims, note = spec
    L = params.get(length_key)
    out = beam_check(section_properties(kind, **dims), L, load_n, material=material or params.get("material"), **kw)
    out["assumptions"].insert(0, note)
    return out


# --------------------------------------------------------------------- bolts ---

def bolt_check(size: str, property_class: str = "8.8", *, tensile_load_n: float = 0.0, shear_load_n: float = 0.0,
               shear_planes: int = 1, safety_factor: float = 2.0) -> dict[str, Any]:
    """Axial and shear capacity of one metric bolt using the tensile stress area.

    Tension is checked against the proof stress, shear against 0.6 x proof stress with the thread in
    the shear plane (the conservative case). Combined loading uses the simple interaction
    (Ft/Ft_allow)^2 + (V/V_allow)^2 <= 1."""
    t = standards.metric_thread(size)
    if t is None:
        raise ScreeningError(f"unknown bolt size {size!r}; known: {', '.join(standards.thread_sizes())}")
    if property_class not in standards.PROPERTY_CLASSES:
        raise ScreeningError(f"unknown property class {property_class!r}; known: {', '.join(standards.PROPERTY_CLASSES)}")
    for name, v in (("tensile_load_n", tensile_load_n), ("shear_load_n", shear_load_n)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            raise ScreeningError(f"{name} must be zero or more, got {v!r}")
    if tensile_load_n == 0 and shear_load_n == 0:
        raise ScreeningError("give a tensile or shear load to check")
    if shear_planes not in (1, 2):
        raise ScreeningError("shear_planes must be 1 or 2")
    sf = _pos(safety_factor, "safety_factor")
    proof, uts = standards.PROPERTY_CLASSES[property_class]
    As = t["stress_area_mm2"]
    t_allow = proof * As / sf
    v_allow = 0.6 * proof * As * shear_planes / sf
    ut, uv = tensile_load_n / t_allow, shear_load_n / v_allow
    combined = math.hypot(ut, uv)
    checks = []
    if tensile_load_n:
        checks.append({"check": "tension", "value": round(tensile_load_n, 1), "unit": "N", "limit": round(t_allow, 1),
                       "utilisation": round(ut, 3), "status": _status(ut), "detail": f"proof {proof} MPa x As {As} mm2 / SF {sf:g}"})
    if shear_load_n:
        checks.append({"check": "shear", "value": round(shear_load_n, 1), "unit": "N", "limit": round(v_allow, 1),
                       "utilisation": round(uv, 3), "status": _status(uv), "detail": f"0.6 x proof, {shear_planes} plane(s), thread in the plane"})
    if tensile_load_n and shear_load_n:
        checks.append({"check": "combined", "value": round(combined, 3), "unit": "", "limit": 1.0, "utilisation": round(combined, 3),
                       "status": _status(combined), "detail": "(Ft/Ft_allow)^2 + (V/V_allow)^2 <= 1"})
    worst = max(checks, key=lambda c_: {"fail": 3, "marginal": 2, "pass": 0}[c_["status"]])["status"]
    return {"kind": "bolt", "status": worst, "checks": checks, "size": t["size"], "property_class": property_class,
            "stress_area_mm2": As,
            "assumptions": ["single bolt carrying the whole load", "no preload relaxation, prying or fatigue",
                            "bearing and tear-out of the clamped plates not checked"],
            "disclaimer": DISCLAIMER}


# ---------------------------------------------------------- mass properties ---

def mass_properties(parts: list[dict]) -> dict[str, Any]:
    """Total mass and centre of mass from per-part volume (mm^3), density (g/cm^3) and centroid (x, y, z).

    A part without a known density is left out and the result says so: a partial total is
    reported as partial, never as complete."""
    total, moment, used, missing = 0.0, [0.0, 0.0, 0.0], 0, []
    for p in parts:
        vol, dens, cen = p.get("volume_mm3"), p.get("density_g_cm3"), p.get("centroid")
        if vol is None or dens is None or cen is None:
            missing.append(p.get("name", "?"))
            continue
        m = vol / 1000.0 * dens
        total += m
        used += 1
        for i in range(3):
            moment[i] += m * cen[i]
    cg = tuple(round(v / total, 3) for v in moment) if total > 0 else None
    return {"mass_g": round(total, 3), "center_of_mass_mm": cg, "parts_counted": used, "parts_missing": missing,
            "complete": not missing and used > 0}
