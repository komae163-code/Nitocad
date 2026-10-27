"""Flight performance for jet_aircraft: engine off-design + aerodynamics (stdlib only)."""
import math

from aerospace.aero import G, air, level_flight
from aerospace.engine_cycle import CycleError, solve_design
from aerospace.engine_offdesign import offdesign_point


def _thrust(state, alt, mach, tt4):
    p = offdesign_point(state, alt, mach, tt4)
    return p["thrust_n"], p["tsfc_g_per_kn_s"] * 1e-6, p


def rating_tt4(state, alt, mach, tt4_max, cap):
    """Highest Tt4 <= tt4_max that keeps fan corrected flow <= cap (None if none works)."""
    def ok(t):
        try:
            return offdesign_point(state, alt, mach, t)["fan_corrected_flow_ratio"] <= cap
        except CycleError:
            return True          # too cold to run at all: certainly below the cap
    if ok(tt4_max):
        return tt4_max
    lo, hi = 1000.0, tt4_max
    if not ok(lo):
        return None
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ok(mid) else (lo, mid)
    return lo


def cruise_throttle(state, n_eng, alt, mach, drag_n, tt4_max, cap=1.0):
    """Lowest Tt4 for which n engines give thrust = drag. Returns (tt4, thrust_per_engine, tsfc) or None."""
    top = rating_tt4(state, alt, mach, tt4_max, cap)
    if top is None:
        return None
    try:
        if _thrust(state, alt, mach, top)[0] * n_eng < drag_n:
            return None
    except CycleError:
        return None
    lo, hi = 1000.0, top
    for _ in range(60):
        mid = (lo + hi) / 2
        try:
            ok = _thrust(state, alt, mach, mid)[0] * n_eng >= drag_n
        except CycleError:
            ok = False
        lo, hi = (lo, mid) if ok else (mid, hi)
    t, c, _ = _thrust(state, alt, mach, hi)
    return hi, t, c


def rate_of_climb(params, state, n_eng, alt, mach, mass_kg, tt4_max, cap=1.0):
    lf = level_flight(params, alt, mach, mass_kg)
    top = rating_tt4(state, alt, mach, tt4_max, cap)
    try:
        t = _thrust(state, alt, mach, top)[0]
    except (CycleError, TypeError):
        return None, lf
    return (n_eng * t - lf["drag_n"]) * lf["tas_m_s"] / (mass_kg * G), lf


def service_ceiling(params, state, n_eng, mach, mass_kg, tt4_max, roc_min=0.508, cap=1.0):
    prev = None
    for alt in range(0, 20001, 500):
        r, _ = rate_of_climb(params, state, n_eng, float(alt), mach, mass_kg, tt4_max, cap)
        if r is None or r < roc_min:
            if prev is None:
                return None
            lo, hi = float(prev), float(alt)
            for _ in range(30):
                mid = (lo + hi) / 2
                rm, _ = rate_of_climb(params, state, n_eng, mid, mach, mass_kg, tt4_max, cap)
                lo, hi = (mid, hi) if (rm is not None and rm >= roc_min) else (lo, mid)
            return lo
        prev = alt
    return 20000.0


def aircraft_performance(params: dict, cycle: dict, flight: dict) -> dict:
    """flight: {tt4_max_k, cruise:{altitude_m, mach, start_mass_kg, end_mass_kg}, climb:{altitude_m, mach, mass_kg}}"""
    _, st = solve_design(cycle)
    n_eng = int(params.get("engines", 2))
    if n_eng < 1:
        raise CycleError("performance needs engines >= 1")
    tt4_max = float(flight["tt4_max_k"])
    cap = float(flight.get("max_corrected_flow_ratio", 1.0))
    if not 0.5 <= cap <= 1.2:
        raise CycleError("max_corrected_flow_ratio must be 0.5..1.2")
    cr = flight["cruise"]
    w0, w1 = float(cr["start_mass_kg"]), float(cr["end_mass_kg"])
    if not 0 < w1 < w0:
        raise CycleError("cruise end_mass_kg must be positive and below start_mass_kg")
    alt, mach = float(cr["altitude_m"]), float(cr["mach"])
    wm = (w0 + w1) / 2
    lf = level_flight(params, alt, mach, wm)
    out = {"cruise": lf, "max_tt4_k": tt4_max, "max_corrected_flow_ratio": cap}
    th = cruise_throttle(st, n_eng, alt, mach, lf["drag_n"], tt4_max, cap)
    if th is None:
        out["cruise_sustainable"] = False
    else:
        tt4, t_eng, tsfc = th
        v = lf["tas_m_s"]
        out.update({"cruise_sustainable": True, "cruise_tt4_k": round(tt4, 1), "cruise_thrust_per_engine_n": round(t_eng, 1),
                    "cruise_tsfc_g_per_kn_s": round(tsfc * 1e6, 3),
                    "cruise_throttle_fraction": round(t_eng / _thrust(st, alt, mach, rating_tt4(st, alt, mach, tt4_max, cap))[0], 3),
                    "range_km": round(v * lf["l_over_d"] / (G * tsfc) * math.log(w0 / w1) / 1000, 1),
                    "fuel_burn_kg": round(w0 - w1, 1)})
    cl = flight.get("climb")
    if cl:
        roc, lfc = rate_of_climb(params, st, n_eng, float(cl["altitude_m"]), float(cl["mach"]), float(cl["mass_kg"]), tt4_max, cap=cap)
        out["climb_roc_m_s"] = None if roc is None else round(roc, 2)
        ceil = service_ceiling(params, st, n_eng, float(cl["mach"]), float(cl["mass_kg"]), tt4_max, cap=cap)
        out["service_ceiling_m"] = None if ceil is None else round(ceil, 0)
    return out
