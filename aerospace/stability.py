"""Longitudinal static stability and trim for jet_aircraft (stdlib only).

Wing + tail neutral point (Nelson-style), fuselage and nacelle destabilising
effect supplied by YOU as a declared shift, thin-airfoil wing pitching moment
with a finite-wing correction, closed-form trim of the horizontal tail, trim
drag. Conceptual level. Not built: lateral-directional stability, dynamic
modes, elevator sizing, control-system or flight-test correlation.
"""
import math

from aerospace import aero
from aerospace.aircraft import DEFAULTS, G, aircraft_metrics, mac_position
from aerospace.engine_cycle import CycleError
from aerospace.airfoil import naca4_camber, parse_naca4

E_TAIL = 0.8     # tail Oswald efficiency used for tail induced drag (method constant)

REQUIRED = ("eta_tail", "fuselage_nacelle_forward_shift_mac", "sm_min", "sm_max", "tail_cl_limit")


def _g(p, k):
    return float(p.get(k, DEFAULTS[k]))


def airfoil_cm_quarter_chord(naca: str, n: int = 720) -> float:
    """Thin-airfoil pitching moment about c/4 for a NACA 4-digit camber line."""
    m, p, _ = parse_naca4(naca)
    a1 = a2 = 0.0
    for i in range(n):
        th = math.pi * (i + 0.5) / n
        x = (1 - math.cos(th)) / 2
        slope = naca4_camber(x, m, p)[1] if m else 0.0
        a1 += slope * math.cos(th)
        a2 += slope * math.cos(2 * th)
    a1, a2 = 2 / n * a1, 2 / n * a2
    return math.pi / 4 * (a2 - a1)


def tail_geometry(p: dict) -> dict:
    cr, ct = _g(p, "htail_root_chord_mm") / 1e3, _g(p, "htail_tip_chord_mm") / 1e3
    b = _g(p, "htail_span_mm") / 1e3
    s = (cr + ct) / 2 * b
    ar, lam = b * b / s, ct / cr
    sweep = math.radians(_g(p, "htail_sweep_deg"))
    lam_c2 = math.atan(math.tan(sweep) - 0.5 * (4 / ar) * (1 - lam) / (1 + lam))
    return {"s": s, "ar": ar, "lam_c2": lam_c2}


def static_margin_terms(p: dict, mach: float, eta_t: float, shift_mac: float) -> dict:
    g, tg = aero.geometry(p), tail_geometry(p)
    out, _ = aircraft_metrics(p)
    mac = g["mac"]
    a_w = aero.lift_curve_slope(g["ar"], g["lam_c2"], mach)
    a_t = aero.lift_curve_slope(tg["ar"], tg["lam_c2"], mach)
    d_eps = 2 * a_w / (math.pi * g["ar"])
    if d_eps >= 1:
        raise CycleError("downwash gradient >= 1: check wing aspect ratio")
    l_t = out["tail_arm_mm"] / 1e3
    v_h = tg["s"] * l_t / (g["s_ref"] * mac)
    h_n = 0.25 - shift_mac + v_h * eta_t * (a_t / a_w) * (1 - d_eps)
    return {"h_n": h_n, "a_w": a_w, "a_t": a_t, "d_eps": d_eps, "v_h": v_h, "l_t": l_t, "mac": mac,
            "s_t": tg["s"], "ar_t": tg["ar"], "s_ref": g["s_ref"], "lam_c4": g["lam_c4"], "ar": g["ar"]}


def analyze_case(p: dict, case: dict, st: dict, cg_rollup_percent=None) -> dict:
    eta, shift = float(st["eta_tail"]), float(st["fuselage_nacelle_forward_shift_mac"])
    mach, alt, mass = float(case["mach"]), float(case["altitude_m"]), float(case["mass_kg"])
    cg = case.get("cg_percent_mac")
    if cg in (None, "rollup"):
        if cg_rollup_percent is None:
            raise CycleError(f"case {case.get('name', '?')}: no cg_percent_mac and no mass roll-up CG available")
        cg = cg_rollup_percent
    h_cg = float(cg) / 100.0
    t = static_margin_terms(p, mach, eta, shift)
    sm = t["h_n"] - h_cg
    naca = str(p.get("wing_naca", "2412"))
    cm_ac = airfoil_cm_quarter_chord(naca) * t["ar"] * math.cos(t["lam_c4"]) ** 2 / (t["ar"] + 2 * math.cos(t["lam_c4"]))
    a = aero.air(alt)
    q = 0.5 * a["rho"] * (mach * a["a"]) ** 2
    cl = mass * G / (q * t["s_ref"])
    dh = h_cg - 0.25
    r = t["s_t"] / t["s_ref"]
    l_cg = t["l_t"] - dh * t["mac"]
    v_cg = t["s_t"] * l_cg / (t["s_ref"] * t["mac"])
    cl_t = (cm_ac + cl * dh) / (eta * (v_cg + r * dh))
    cl_w = cl - eta * r * cl_t
    dp = aero.drag_polar(p, alt, mach, cl)
    trim = dp["k"] * (cl_w ** 2 - cl ** 2) + r * cl_t ** 2 / (math.pi * t["ar_t"] * E_TAIL)
    return {"name": case.get("name", "case"), "mach": mach, "altitude_m": alt, "mass_kg": mass,
            "cg_percent_mac": round(h_cg * 100, 2), "neutral_point_percent_mac": round(t["h_n"] * 100, 2),
            "static_margin_percent_mac": round(sm * 100, 2), "cl": round(cl, 4), "cm_ac_wing": round(cm_ac, 4),
            "trim_tail_cl": round(cl_t, 4), "trim_wing_cl": round(cl_w, 4),
            "trim_drag_counts": round(trim * 1e4, 2), "tail_load": "download" if cl_t < 0 else "upload",
            "trim_residual": round(cm_ac + cl_w * dh - v_cg * eta * cl_t, 10),
            "cl_alpha_wing_per_rad": round(t["a_w"], 3), "cl_alpha_tail_per_rad": round(t["a_t"], 3),
            "downwash_gradient": round(t["d_eps"], 3), "horizontal_tail_volume": round(t["v_h"], 3)}


def analyze(p: dict, st: dict, cg_rollup_percent=None) -> dict:
    miss = [k for k in REQUIRED if st.get(k) is None]
    if miss:
        raise CycleError("stability inputs missing: " + ", ".join(miss))
    if not 0.5 <= float(st["eta_tail"]) <= 1.1:
        raise CycleError("eta_tail must be 0.5..1.1")
    if not 0 <= float(st["sm_min"]) < float(st["sm_max"]) <= 1:
        raise CycleError("require 0 <= sm_min < sm_max <= 1 (fractions of MAC)")
    cases = st.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CycleError("stability.cases must list at least one loading case (mass, cg, altitude, mach)")
    res = [analyze_case(p, c, st, cg_rollup_percent) for c in cases]
    lo, hi, lim = float(st["sm_min"]) * 100, float(st["sm_max"]) * 100, float(st["tail_cl_limit"])
    issues = []
    for r in res:
        if r["static_margin_percent_mac"] < lo:
            issues.append(f"{r['name']}: static margin {r['static_margin_percent_mac']}% MAC is below the minimum {lo:g}%")
        if r["static_margin_percent_mac"] > hi:
            issues.append(f"{r['name']}: static margin {r['static_margin_percent_mac']}% MAC exceeds {hi:g}% (heavy trim and poor manoeuvrability)")
        if abs(r["trim_tail_cl"]) > lim:
            issues.append(f"{r['name']}: trim tail CL {r['trim_tail_cl']} exceeds the limit {lim:g}")
    first = res[0]
    aft = first["neutral_point_percent_mac"] - lo
    return {"cases": res, "issues": issues, "aft_cg_limit_percent_mac": round(aft, 2),
            "static_margin_min_percent_mac": min(r["static_margin_percent_mac"] for r in res),
            "static_margin_max_percent_mac": max(r["static_margin_percent_mac"] for r in res),
            "max_abs_trim_tail_cl": max(abs(r["trim_tail_cl"]) for r in res),
            "max_trim_drag_counts": max(r["trim_drag_counts"] for r in res)}


def airfoil_alpha_l0(naca: str, n: int = 720) -> float:
    """Zero-lift angle of attack (rad) of a NACA 4-digit camber line, thin-airfoil theory."""
    m, p, _ = parse_naca4(naca)
    if not m:
        return 0.0
    tot = 0.0
    for i in range(n):
        th = math.pi * (i + 0.5) / n
        x = (1 - math.cos(th)) / 2
        tot += naca4_camber(x, m, p)[1] * (math.cos(th) - 1)
    return -(tot * math.pi / n) / math.pi
