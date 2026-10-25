"""Preliminary mean-line sizing of fan, compressor and turbine stages (stdlib only).

From a design-point cycle plus the designer's aerodynamic choices (axial Mach
numbers, blade speeds, stage loading limits) this gives annulus areas, fan
diameter and rpm, and stage counts. Mean-line only: no radial equilibrium, no
blade shapes, no losses beyond the cycle efficiencies, stage count simply
total enthalpy change / (loading x blade speed squared). It does not design
real blading. Reference method: Euler work equation, standard mass-flow-parameter
annulus sizing as in conceptual engine-design texts.
"""
import math

from aerospace.engine_cycle import CP_C, CP_T, GAMMA_C, GAMMA_T, R, CycleError, solve_design

REQUIRED = ("fan_axial_mach", "fan_hub_tip", "fan_tip_speed_m_s", "fan_psi_max",
            "hpc_axial_mach", "hpc_mean_speed_m_s", "hpc_psi_max",
            "hpt_mean_speed_m_s", "hpt_psi_max", "lpt_mean_speed_m_s", "lpt_psi_max",
            "turbine_exit_mach")


def mfp(mach: float, gamma: float) -> float:
    """Mass flow parameter mdot*sqrt(Tt)/(Pt*A) for gas constant R."""
    return mach * math.sqrt(gamma / R) * (1 + (gamma - 1) / 2 * mach ** 2) ** (-(gamma + 1) / (2 * (gamma - 1)))


def _area(mdot, tt, pt, mach, gamma):
    return mdot * math.sqrt(tt) / (pt * mfp(mach, gamma))


def meanline(cycle_inputs: dict, ml: dict) -> dict:
    miss = [k for k in REQUIRED if ml.get(k) is None]
    if miss:
        raise CycleError("meanline inputs missing: " + ", ".join(miss))
    if not 0.2 <= ml["fan_axial_mach"] <= 0.75 or not 0.2 <= ml["hpc_axial_mach"] <= 0.7:
        raise CycleError("axial Mach numbers outside 0.2..0.75 are not meaningful for this method")
    if not 0.2 <= ml["fan_hub_tip"] < 0.7:
        raise CycleError("fan_hub_tip must be in 0.2..0.7")
    if min(ml[k] for k in REQUIRED if k.endswith(("psi_max", "speed_m_s"))) <= 0:
        raise CycleError("speeds and loading limits must be positive")
    _, st = solve_design(cycle_inputs)
    beta, f = cycle_inputs["bpr"], st["f"]

    # Fan: face annulus sized for the whole engine flow
    a2 = _area(st["mdot0"], st["tt2"], st["pt2"], ml["fan_axial_mach"], GAMMA_C)
    h = ml["fan_hub_tip"]
    r_tip = math.sqrt(a2 / (math.pi * (1 - h * h)))
    omega = ml["fan_tip_speed_m_s"] / r_tip
    r_mean = r_tip * math.sqrt((1 + h * h) / 2)
    dh_fan = CP_C * (st["tt13"] - st["tt2"])
    fan_stages = max(1, math.ceil(dh_fan / (ml["fan_psi_max"] * (omega * r_mean) ** 2)))

    # Core compressor
    a_hpc_in = _area(st["mdot_core"], st["tt13"], st["pt13"], ml["hpc_axial_mach"], GAMMA_C)
    dh_hpc = CP_C * (st["tt3"] - st["tt13"])
    hpc_stages = max(1, math.ceil(dh_hpc / (ml["hpc_psi_max"] * ml["hpc_mean_speed_m_s"] ** 2)))

    # Turbines (per core mass flow, mechanical efficiency applied to shaft work)
    eta_m, mc = cycle_inputs["eta_mech"], st["mdot_core"]
    dh_hpt = CP_T * (st["tt4"] - st["tt45"])
    dh_lpt = CP_T * (st["tt45"] - st["tt5"])
    hpt_stages = max(1, math.ceil(dh_hpt / (ml["hpt_psi_max"] * ml["hpt_mean_speed_m_s"] ** 2)))
    lpt_stages = max(1, math.ceil(dh_lpt / (ml["lpt_psi_max"] * ml["lpt_mean_speed_m_s"] ** 2)))
    a5 = _area(mc * (1 + f), st["tt5"], st["pt5"], ml["turbine_exit_mach"], GAMMA_T)
    n_lpt = ml["lpt_mean_speed_m_s"] / max(1e-9, math.sqrt(a5 / math.pi))   # rad/s, rough mean radius ~ sqrt(A/pi)
    an2 = (a5 * 1550.0031) * (n_lpt * 60 / (2 * math.pi)) ** 2                # in^2 * rpm^2
    out = {
        "fan_tip_diameter_mm": round(2 * r_tip * 1000, 1), "fan_face_area_m2": round(a2, 4),
        "fan_rpm": round(omega * 60 / (2 * math.pi), 0), "fan_stages": fan_stages,
        "fan_enthalpy_rise_kj_kg": round(dh_fan / 1000, 2),
        "hpc_inlet_area_m2": round(a_hpc_in, 4), "hpc_stages": hpc_stages,
        "hpc_enthalpy_rise_kj_kg": round(dh_hpc / 1000, 2),
        "hpt_stages": hpt_stages, "lpt_stages": lpt_stages,
        "hpt_enthalpy_drop_kj_kg": round(dh_hpt / 1000, 2), "lpt_enthalpy_drop_kj_kg": round(dh_lpt / 1000, 2),
        "turbine_exit_area_m2": round(a5, 4), "lpt_an2_in2_rpm2": float(f"{an2:.3g}"),
    }
    return out


def check_geometry(out: dict, fan_diameter_mm: float, tol: float = 0.05):
    """Warnings comparing the sized fan to the CAD model's fan diameter."""
    d = out["fan_tip_diameter_mm"]
    if fan_diameter_mm and abs(d - fan_diameter_mm) / fan_diameter_mm > tol:
        return [f"mean-line fan tip diameter {d:.0f} mm differs from the model's fan_diameter_mm {fan_diameter_mm:.0f} mm by more than {tol:.0%}; "
                f"set fan_diameter_mm = {d:.0f}"]
    return []
