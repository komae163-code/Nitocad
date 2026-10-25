"""Off-design turbofan performance: throttle (Tt4), altitude and Mach sweeps.

Simplified, textbook-style fixed-geometry model matched to a design point:
  - HP and LP turbine nozzles choked, so each turbine's temperature ratio and
    the turbine pressure ratio stay at their design values
  - core mass flow set by the choked HPT inlet: mdot*sqrt(Tt4)/Pt4 = constant
  - component efficiencies held at design values (no component maps)
  - bypass ratio held at the design value
  - ideally expanded (variable-area) nozzles, no bleed or cooling flow
It gives thrust, fuel flow and TSFC trends, NOT surge margin, spool speeds or
transient behaviour, which need component maps.
"""
import math

from aerospace.engine_cycle import (CP_C, CP_T, GAMMA_C, GAMMA_T, H_PR, R, CycleError, isa, solve_design)

MAX_POINTS = 400


def offdesign_point(state: dict, altitude_m: float, mach: float, tt4: float) -> dict:
    i = state["inputs"]
    if not 0 <= mach <= 1.0:
        raise CycleError("mach must be in 0..1.0")
    if not 1000 <= tt4 <= 2300:
        raise CycleError("tt4_k must be in 1000..2300 K")
    t0, p0 = isa(altitude_m)
    v0 = mach * math.sqrt(GAMMA_C * R * t0)
    tt2 = t0 * (1 + (GAMMA_C - 1) / 2 * mach ** 2)
    pt2 = i["pi_inlet"] * p0 * (tt2 / t0) ** (GAMMA_C / (GAMMA_C - 1))
    beta, eta_m = i["bpr"], i["eta_mech"]
    th, tl = state["tau_hpt"], state["tau_lpt"]
    f = state["f"]
    for _ in range(100):
        tt45 = tt4 * th
        tau_f = 1 + (1 + f) * CP_T * tt45 * (1 - tl) * eta_m / ((1 + beta) * CP_C * tt2)
        tt13 = tt2 * tau_f
        tau_c = 1 + (1 + f) * CP_T * tt4 * (1 - th) * eta_m / (CP_C * tt13)
        tt3 = tt13 * tau_c
        f_new = (CP_T * tt4 - CP_C * tt3) / (i["eta_b"] * H_PR - CP_T * tt4)
        if abs(f_new - f) < 1e-13:
            f = f_new
            break
        f = f_new
    if f <= 0:
        raise CycleError("fuel-air ratio is not positive: tt4_k is too low for this operating point")
    ex = (GAMMA_C - 1) / GAMMA_C
    pi_f = (1 + i["eta_f"] * (tau_f - 1)) ** (1 / ex)
    pi_c = (1 + i["eta_c"] * (tau_c - 1)) ** (1 / ex)
    pt13, pt3 = pt2 * pi_f, pt2 * pi_f * pi_c
    pt4 = i["pi_b"] * pt3
    tt5, pt5 = tt4 * th * tl, pt4 * state["pi_t"]
    pt9, pt19 = i["pi_nozzle"] * pt5, i["pi_nozzle"] * pt13
    if pt9 <= p0:
        raise CycleError("core nozzle total pressure does not exceed ambient")
    if beta > 0 and pt19 <= p0:
        raise CycleError("bypass nozzle total pressure does not exceed ambient")
    t9 = tt5 * (p0 / pt9) ** ((GAMMA_T - 1) / GAMMA_T)
    v9 = math.sqrt(2 * CP_T * (tt5 - t9))
    v19 = math.sqrt(2 * CP_C * tt13 * (1 - (p0 / pt19) ** ex)) if beta > 0 else 0.0
    m4 = state["k_flow"] * pt4 / math.sqrt(tt4)
    mdot_core = m4 / (1 + f)
    mdot0 = mdot_core * (1 + beta)
    thrust = mdot_core * ((1 + f) * v9 - v0 + beta * (v19 - v0))
    if thrust <= 0:
        raise CycleError("net thrust is not positive")
    fuel = f * mdot_core
    theta, delta = tt2 / 288.15, pt2 / 101325.0
    d_theta, d_delta = state["tt2"] / 288.15, state["pt2"] / 101325.0
    return {
        "altitude_m": altitude_m, "mach": mach, "tt4_k": tt4,
        "thrust_n": round(thrust, 1), "tsfc_g_per_kn_s": round(fuel / thrust * 1e6, 3),
        "fuel_flow_kg_s": round(fuel, 5), "mdot0_kg_s": round(mdot0, 2),
        "pi_f": round(pi_f, 4), "pi_c": round(pi_c, 3), "opr": round(pi_f * pi_c, 2),
        "tt3_k": round(tt3, 1), "tt5_k": round(tt5, 1),
        "fan_corrected_flow_ratio": round((mdot0 * math.sqrt(theta) / delta) / (state["mdot0"] * math.sqrt(d_theta) / d_delta), 4),
    }


def sweep(inputs: dict, altitudes, machs, tt4_values, limits: dict = None) -> dict:
    """Grid of off-design points. limits: optional {tt3_max_k, tt4_max_k}."""
    n = len(altitudes) * len(machs) * len(tt4_values)
    if n == 0 or n > MAX_POINTS:
        raise CycleError(f"sweep must have between 1 and {MAX_POINTS} points (got {n})")
    design, state = solve_design(inputs)
    limits = limits or {}
    points, errors, exceeded, warnings = [], [], [], []
    for a in altitudes:
        for m in machs:
            for t in tt4_values:
                try:
                    pt = offdesign_point(state, float(a), float(m), float(t))
                except CycleError as e:
                    errors.append({"altitude_m": a, "mach": m, "tt4_k": t, "error": str(e)})
                    continue
                for key, lim in (("tt3_k", limits.get("tt3_max_k")), ("tt4_k", limits.get("tt4_max_k"))):
                    if lim is not None and pt[key] > lim:
                        exceeded.append({"altitude_m": a, "mach": m, "tt4_k": t, "limit": key.replace("_k", "_max_k"), "value": pt[key]})
                if pt["fan_corrected_flow_ratio"] > 1.05:
                    warnings.append({"altitude_m": a, "mach": m, "tt4_k": t, "fan_corrected_flow_ratio": pt["fan_corrected_flow_ratio"],
                                     "note": "above design corrected flow: a real engine is speed-limited here; needs component maps to judge"})
                points.append(pt)
    ref = offdesign_point(state, inputs["altitude_m"], inputs["mach"], inputs["tt4_k"])
    summary = {}
    if points:
        best = min(points, key=lambda p: p["tsfc_g_per_kn_s"])
        top = max(points, key=lambda p: p["thrust_n"])
        summary = {"points": len(points), "max_thrust_n": top["thrust_n"], "min_tsfc_g_per_kn_s": best["tsfc_g_per_kn_s"],
                   "min_tsfc_at": {k: best[k] for k in ("altitude_m", "mach", "tt4_k")}}
    return {"design": design, "summary": summary, "points": points, "errors": errors, "limits_exceeded": exceeded, "warnings": warnings,
            "design_reproduced": abs(ref["thrust_n"] - design["thrust_n"]) < 0.5}


def thrust_at(state: dict, altitude_m: float, mach: float, tt4: float):
    """(thrust N, tsfc kg/(N s)) for one engine, or raises CycleError."""
    p = offdesign_point(state, altitude_m, mach, tt4)
    return p["thrust_n"], p["tsfc_g_per_kn_s"] * 1e-6
