"""Conceptual aerodynamics and flight performance for jet_aircraft (stdlib only).

Component drag build-up (flat-plate skin friction with form and interference
factors), induced drag with an Oswald-efficiency estimate, Korn-equation wave
drag, DATCOM lift-curve slope, level-flight drag, thrust required vs available,
Breguet range, climb rate and ceiling. Methods follow common conceptual-design
texts (e.g. Raymer). Accuracy is conceptual-design level and is NOT validated
here against wind-tunnel or CFD data. Smooth surfaces, no high-lift devices,
no trim drag, no engine installation effects beyond a nacelle interference factor.
"""
import math

from aerospace.aircraft import DEFAULTS, G
from aerospace.engine_cycle import GAMMA_C, R, isa

Q = {"wing": 1.0, "tail": 1.04, "fuselage": 1.0, "nacelle": 1.3}   # interference factors
MISC_FACTOR = 1.03                                                  # leakage and protuberances
KORN_KAPPA = 0.87                                                   # conventional (non-supercritical) airfoil
S0, SUTH = 1.458e-6, 110.4


def _g(p, k):
    return float(p.get(k, DEFAULTS.get(k)))


def air(alt_m: float):
    t, p = isa(alt_m)
    rho = p / (R * t)
    mu = S0 * t ** 1.5 / (t + SUTH)
    return {"t": t, "p": p, "rho": rho, "mu": mu, "a": math.sqrt(GAMMA_C * R * t)}


def geometry(p: dict) -> dict:
    """Reference areas and wetted areas derived from the model parameters."""
    L, D, b = _g(p, "fuselage_length_mm") / 1e3, _g(p, "fuselage_diameter_mm") / 1e3, _g(p, "wing_span_mm") / 1e3
    cr, ct = _g(p, "wing_root_chord_mm") / 1e3, _g(p, "wing_tip_chord_mm") / 1e3
    lam = ct / cr
    s_w = (cr + ct) / 2 * b
    ar = b * b / s_w
    sweep_le = math.radians(_g(p, "wing_sweep_deg"))
    taper_term = (4 / ar) * (1 - lam) / (1 + lam)
    tan_c4 = math.tan(sweep_le) - 0.25 * taper_term
    tan_c2 = math.tan(sweep_le) - 0.5 * taper_term
    tan_m = math.tan(sweep_le) - 0.30 * taper_term        # max-thickness line (NACA 4-digit: 30% chord)
    tc = int(str(p.get("wing_naca", "2412"))[2:]) / 100.0
    s_exp = max(0.5 * s_w, s_w - D * cr)
    mac = (2 / 3) * cr * (1 + lam + lam * lam) / (1 + lam)
    hs = (_g(p, "htail_root_chord_mm") + _g(p, "htail_tip_chord_mm")) / 2e3 * _g(p, "htail_span_mm") / 1e3
    vs = (_g(p, "vtail_root_chord_mm") + _g(p, "vtail_tip_chord_mm")) / 2e3 * _g(p, "vtail_height_mm") / 1e3
    f = L / D
    s_wet_fus = math.pi * D * L * (1 - 2 / f) ** (2 / 3) * (1 + 1 / f ** 2)
    n_eng = int(p.get("engines", 2))
    fd = float(p.get("engine_fan_diameter_mm", 1800.0)) / 1e3
    n_len, n_dia = 1.9 * fd, 1.15 * fd
    return {
        "s_ref": s_w, "ar": ar, "tc": tc, "mac": mac, "span": b, "lam": lam,
        "lam_c4": math.atan(tan_c4), "lam_c2": math.atan(tan_c2), "lam_m": math.atan(tan_m), "lam_le": sweep_le,
        "s_wet": {"wing": s_exp * (1.977 + 0.52 * tc), "tail": (hs + vs) * (1.977 + 0.52 * 0.12),
                  "fuselage": s_wet_fus, "nacelle": n_eng * math.pi * n_dia * n_len},
        "lengths": {"wing": mac, "tail": 0.5 * ((_g(p, "htail_root_chord_mm") + _g(p, "htail_tip_chord_mm")) / 2e3
                                              + (_g(p, "vtail_root_chord_mm") + _g(p, "vtail_tip_chord_mm")) / 2e3),
                    "fuselage": L, "nacelle": n_len},
        "fineness": {"fuselage": f, "nacelle": n_len / n_dia},
    }


def cf_turbulent(re: float, mach: float) -> float:
    return 0.455 / (math.log10(re) ** 2.58 * (1 + 0.144 * mach ** 2) ** 0.65)


def _ff(kind, g, mach):
    if kind == "wing" or kind == "tail":
        tc = g["tc"] if kind == "wing" else 0.12
        lm = g["lam_m"]
        return (1 + 2.0 * tc + 100 * tc ** 4) * (1.34 * max(mach, 0.1) ** 0.18 * math.cos(lm) ** 0.28)
    if kind == "fuselage":
        f = g["fineness"]["fuselage"]
        return 1 + 60 / f ** 3 + f / 400
    f = g["fineness"]["nacelle"]
    return 1 + 0.35 / f


def oswald(ar, sweep_le):
    base = 1 - 0.045 * ar ** 0.68
    if math.degrees(sweep_le) <= 30:
        return 1.78 * base - 0.64
    return 4.61 * base * math.cos(sweep_le) ** 0.15 - 3.1


def lift_curve_slope(ar, lam_c2, mach, kappa=1.0):
    beta2 = 1 - mach ** 2
    return 2 * math.pi * ar / (2 + math.sqrt(4 + (ar ** 2 * beta2 / kappa ** 2) * (1 + math.tan(lam_c2) ** 2 / beta2)))


def drag_polar(p: dict, alt_m: float, mach: float, cl: float) -> dict:
    if not 0.1 <= mach <= 1.0:
        raise ValueError("mach must be in 0.1..1.0 for this drag model")
    g, a = geometry(p), air(alt_m)
    v = mach * a["a"]
    cd0_parts = {}
    for kind in ("wing", "tail", "fuselage", "nacelle"):
        length = g["lengths"][kind]
        re = a["rho"] * v * length / a["mu"]
        cd0_parts[kind] = cf_turbulent(re, mach) * _ff(kind, g, mach) * Q[kind] * g["s_wet"][kind] / g["s_ref"]
    cd0 = sum(cd0_parts.values()) * MISC_FACTOR
    e = oswald(g["ar"], g["lam_le"])
    k = 1 / (math.pi * g["ar"] * e)
    cos_l = math.cos(g["lam_c4"])
    kappa = float(p.get("airfoil_kappa", KORN_KAPPA))
    if not 0.8 <= kappa <= 1.0:
        raise ValueError("airfoil_kappa must be 0.8..1.0 (0.87 conventional, about 0.95 supercritical)")
    m_dd = kappa / cos_l - g["tc"] / cos_l ** 2 - cl / (10 * cos_l ** 3)
    m_crit = m_dd - (0.1 / 80) ** (1 / 3)
    cd_w = 20 * (mach - m_crit) ** 4 if mach > m_crit else 0.0
    cd = cd0 + k * cl * cl + cd_w
    return {"cd0": cd0, "cd0_parts": {kk: vv * MISC_FACTOR for kk, vv in cd0_parts.items()}, "k": k, "oswald_e": e,
            "cd_induced": k * cl * cl, "cd_wave": cd_w, "cd": cd, "m_crit": m_crit, "l_over_d": cl / cd,
            "cl_alpha_per_rad": lift_curve_slope(g["ar"], g["lam_c2"], mach), "s_ref_m2": g["s_ref"]}


def level_flight(p: dict, alt_m: float, mach: float, mass_kg: float) -> dict:
    a = air(alt_m)
    g = geometry(p)
    v = mach * a["a"]
    q = 0.5 * a["rho"] * v * v
    w = mass_kg * G
    cl = w / (q * g["s_ref"])
    dp = drag_polar(p, alt_m, mach, cl)
    drag = q * g["s_ref"] * dp["cd"]
    return {"altitude_m": alt_m, "mach": mach, "tas_m_s": round(v, 2), "cl": round(cl, 4), "cd": round(dp["cd"], 5),
            "cd0": round(dp["cd0"], 5), "cd_induced": round(dp["cd_induced"], 5), "cd_wave": round(dp["cd_wave"], 5),
            "l_over_d": round(dp["l_over_d"], 2), "drag_n": round(drag, 1), "m_crit": round(dp["m_crit"], 3),
            "oswald_e": round(dp["oswald_e"], 3), "cl_alpha_per_rad": round(dp["cl_alpha_per_rad"], 3)}


def max_ld_incompressible(p: dict) -> float:
    dp = drag_polar(p, 0.0, 0.3, 0.5)
    return round(1 / (2 * math.sqrt(dp["cd0"] * dp["k"])), 2)
