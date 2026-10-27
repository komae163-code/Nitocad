"""Turbofan / turbojet design-point cycle analysis (stdlib only).

Separate-flow turbofan, calorically perfect gas with different cold/hot
properties, ideally expanded nozzles, no bleed or cooling flow, no installation
losses. Textbook method (e.g. Mattingly, Hill & Peterson). Station numbering:
0 ambient, 2 fan face, 13 fan exit, 3 compressor exit, 4 turbine inlet,
5 turbine exit, 9 core nozzle, 19 bypass nozzle.

Units: SI (K, Pa, m/s, kg/s, N). A turbojet is bpr = 0 and pi_f = 1.
This is conceptual-design level. It is NOT engine certification analysis.
"""
import math

R = 287.0
GAMMA_C, GAMMA_T = 1.4, 1.33
CP_C = GAMMA_C * R / (GAMMA_C - 1)
CP_T = GAMMA_T * R / (GAMMA_T - 1)
H_PR = 43.0e6        # J/kg, typical Jet-A lower heating value

REQUIRED = ("altitude_m", "mach", "mdot0_kg_s", "bpr", "pi_f", "pi_c", "tt4_k",
            "pi_inlet", "eta_f", "eta_c", "eta_b", "pi_b", "eta_t", "eta_mech", "pi_nozzle")


class CycleError(ValueError):
    pass


def isa(alt_m: float):
    """International Standard Atmosphere up to 20 km: (T K, P Pa)."""
    if not 0 <= alt_m <= 20000:
        raise CycleError("altitude must be between 0 and 20000 m")
    if alt_m <= 11000:
        t = 288.15 - 0.0065 * alt_m
        return t, 101325.0 * (t / 288.15) ** 5.255876
    t = 216.65
    return t, 22632.06 * math.exp(-9.80665 * (alt_m - 11000) / (287.05287 * t))


def validate_inputs(i: dict) -> list:
    errs = [f"{k} is required" for k in REQUIRED if i.get(k) is None]
    if errs:
        return errs
    chk = [
        (0 <= i["mach"] <= 1.0, "mach must be in 0..1.0 (subsonic inlet model)"),
        (i["mdot0_kg_s"] > 0, "mdot0_kg_s must be positive"),
        (0 <= i["bpr"] <= 20, "bpr must be in 0..20"),
        (i["pi_f"] >= 1 and i["pi_c"] >= 1, "pressure ratios must be >= 1"),
        (1000 <= i["tt4_k"] <= 2300, "tt4_k must be in 1000..2300 K"),
        (all(0.5 < i[k] <= 1.0 for k in ("eta_f", "eta_c", "eta_b", "eta_t", "eta_mech", "pi_inlet", "pi_b", "pi_nozzle")),
         "efficiencies and total-pressure recoveries must be in (0.5, 1.0]"),
    ]
    return [m for ok, m in chk if not ok]


def solve_design(i: dict):
    """Returns (rounded outputs, full-precision state for off-design/mean-line use)."""
    errs = validate_inputs(i)
    if errs:
        raise CycleError("; ".join(errs))
    t0, p0 = isa(i["altitude_m"])
    m0 = i["mach"]
    v0 = m0 * math.sqrt(GAMMA_C * R * t0)
    tt0 = t0 * (1 + (GAMMA_C - 1) / 2 * m0 ** 2)
    pt0 = p0 * (tt0 / t0) ** (GAMMA_C / (GAMMA_C - 1))
    tt2, pt2 = tt0, i["pi_inlet"] * pt0
    ex = (GAMMA_C - 1) / GAMMA_C
    tau_f = 1 + (i["pi_f"] ** ex - 1) / i["eta_f"]
    tt13, pt13 = tt2 * tau_f, pt2 * i["pi_f"]
    tau_c = 1 + (i["pi_c"] ** ex - 1) / i["eta_c"]
    tt3, pt3 = tt13 * tau_c, pt13 * i["pi_c"]
    tt4 = i["tt4_k"]
    if tt4 <= tt3 + 50:
        raise CycleError(f"tt4_k ({tt4:.0f} K) is not meaningfully above compressor exit temperature ({tt3:.0f} K)")
    pt4 = i["pi_b"] * pt3
    f = (CP_T * tt4 - CP_C * tt3) / (i["eta_b"] * H_PR - CP_T * tt4)
    if f <= 0:
        raise CycleError("fuel-air ratio is not positive for these inputs")
    beta = i["bpr"]
    # Turbine power balance: HPT drives core compressor, LPT drives fan (core + bypass flow)
    work = CP_C * ((tt3 - tt13) + (1 + beta) * (tt13 - tt2))
    tt5 = tt4 - work / ((1 + f) * CP_T * i["eta_mech"])
    tau_t = tt5 / tt4
    if not 0.3 < tau_t < 1.0:
        raise CycleError(f"turbine temperature ratio {tau_t:.3f} is outside 0.3..1.0: inputs are inconsistent")
    arg = 1 - (1 - tau_t) / i["eta_t"]
    if arg <= 0:
        raise CycleError("turbine efficiency/expansion inconsistent (non-physical pressure ratio)")
    pi_t = arg ** (GAMMA_T / (GAMMA_T - 1))
    pt5 = pt4 * pi_t
    pt9, pt19 = i["pi_nozzle"] * pt5, i["pi_nozzle"] * pt13
    if pt9 <= p0:
        raise CycleError("core nozzle total pressure does not exceed ambient: no thrust")
    if beta > 0 and pt19 <= p0:
        raise CycleError("bypass nozzle total pressure does not exceed ambient: raise pi_f or set bpr = 0")
    t9 = tt5 * (p0 / pt9) ** ((GAMMA_T - 1) / GAMMA_T)
    v9 = math.sqrt(2 * CP_T * (tt5 - t9))
    v19 = math.sqrt(2 * CP_C * tt13 * (1 - (p0 / pt19) ** ex)) if beta > 0 else 0.0
    mdot_core = i["mdot0_kg_s"] / (1 + beta)
    f_per_core = (1 + f) * v9 - v0 + beta * (v19 - v0)
    thrust = mdot_core * f_per_core
    if thrust <= 0:
        raise CycleError("net thrust is not positive at this flight condition")
    fuel = f * mdot_core
    ke_gain = mdot_core * ((1 + f) * v9 ** 2 / 2 + beta * v19 ** 2 / 2 - (1 + beta) * v0 ** 2 / 2)
    tt45 = tt4 - CP_C * (tt3 - tt13) / ((1 + f) * CP_T * i["eta_mech"])
    state = {
        "inputs": dict(i), "f": f, "tau_hpt": tt45 / tt4, "tau_lpt": tt5 / tt45, "pi_t": pi_t,
        "k_flow": mdot_core * (1 + f) * math.sqrt(tt4) / pt4,          # choked HPT inlet: mdot sqrt(Tt4)/Pt4
        "mdot0": i["mdot0_kg_s"], "tt2": tt2, "pt2": pt2, "tt13": tt13, "pt13": pt13, "tt3": tt3, "pt3": pt3,
        "tt4": tt4, "tt45": tt45, "tt5": tt5, "pt5": pt5, "mdot_core": mdot_core, "thrust": thrust,
        "fuel": fuel, "tau_f": tau_f, "tau_c": tau_c,
    }
    out = {
        "thrust_n": round(thrust, 1),
        "tsfc_g_per_kn_s": round(fuel / thrust * 1e6, 3),
        "specific_thrust_n_per_kg_s": round(thrust / i["mdot0_kg_s"], 2),
        "fuel_flow_kg_s": round(fuel, 5),
        "fuel_air_ratio": round(f, 5),
        "opr": round(i["pi_f"] * i["pi_c"], 2),
        "tt3_k": round(tt3, 1), "tt5_k": round(tt5, 1), "turbine_pressure_ratio": round(1 / pi_t, 3),
        "core_jet_velocity_m_s": round(v9, 1), "bypass_jet_velocity_m_s": round(v19, 1),
        "flight_velocity_m_s": round(v0, 1),
        "propulsive_efficiency": round(thrust * v0 / ke_gain, 4) if v0 > 0 and ke_gain > 0 else 0.0,
        "power_balance_residual_w": round(
            mdot_core * ((1 + f) * CP_T * (tt4 - tt5) * i["eta_mech"] - work), 3),
    }
    return out, state


def turbofan_design_point(i: dict) -> dict:
    return solve_design(i)[0]
