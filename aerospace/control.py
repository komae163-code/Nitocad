"""Elevator sizing for jet_aircraft (stdlib only).

Thin-airfoil flap effectiveness with a declared viscous correction and span
fraction, tail angle of attack from wing angle of attack and downwash, the
elevator deflection required to trim in each loading case, the stabilizer
incidence that would trim with zero elevator, control power (Cm per radian)
and, when takeoff data is supplied, pitch acceleration available at rotation
(about the CG: it ignores the gear reaction moment and ground effect, so it is OPTIMISTIC).
Not built: hinge moments and control forces, trim tab/stabilizer actuator
sizing, flare and gust manoeuvre requirements, aerodynamic balance.
"""
import math

from aerospace import aero, stability
from aerospace.engine_cycle import CycleError

REQUIRED = ("chord_ratio", "span_fraction", "max_up_deg", "max_down_deg", "viscous_factor", "stabilizer_incidence_deg", "eta_tail", "cases")


def thin_airfoil_tau(e: float) -> float:
    """Flap effectiveness tau = 1 - (theta - sin theta)/pi, theta = acos(2E - 1)."""
    th = math.acos(2 * e - 1)
    return 1 - (th - math.sin(th)) / math.pi


def analyze(p: dict, ele: dict, cg_rollup_percent=None, rotation: dict = None) -> dict:
    miss = [k for k in REQUIRED if ele.get(k) is None]
    if miss:
        raise CycleError("elevator inputs missing: " + ", ".join(miss))
    e, sf, vis = float(ele["chord_ratio"]), float(ele["span_fraction"]), float(ele["viscous_factor"])
    if not (0.1 <= e <= 0.5 and 0.2 <= sf <= 1.0 and 0.5 <= vis <= 1.0):
        raise CycleError("chord_ratio 0.1..0.5, span_fraction 0.2..1.0, viscous_factor 0.5..1.0 are required")
    up, down = float(ele["max_up_deg"]), float(ele["max_down_deg"])
    if up <= 0 or down < 0 or up > 45 or down > 45:
        raise CycleError("max_up_deg must be 1..45 and max_down_deg 0..45")
    tau = thin_airfoil_tau(e) * vis * sf
    it = float(ele["stabilizer_incidence_deg"])
    st_cfg = {"eta_tail": ele["eta_tail"], "fuselage_nacelle_forward_shift_mac": ele.get("fuselage_nacelle_forward_shift_mac", 0.0)}
    a0 = stability.airfoil_alpha_l0(str(p.get("wing_naca", "2412")))
    rows, issues = [], []
    for c in ele["cases"]:
        cs = stability.analyze_case(p, c, st_cfg, cg_rollup_percent)
        t = stability.static_margin_terms(p, float(c["mach"]), float(ele["eta_tail"]), float(st_cfg["fuselage_nacelle_forward_shift_mac"]))
        alpha_w = cs["trim_wing_cl"] / t["a_w"] + a0
        alpha_t = (1 - t["d_eps"]) * alpha_w + t["d_eps"] * a0 + math.radians(it)
        delta = (cs["trim_tail_cl"] / t["a_t"] - alpha_t) / tau
        i0 = cs["trim_tail_cl"] / t["a_t"] - (1 - t["d_eps"]) * alpha_w - t["d_eps"] * a0
        d_deg = math.degrees(delta)
        row = {"name": cs["name"], "trim_tail_cl": cs["trim_tail_cl"], "wing_alpha_deg": round(math.degrees(alpha_w), 2),
               "trim_elevator_deg": round(d_deg, 2), "stabilizer_incidence_for_zero_elevator_deg": round(math.degrees(i0), 2),
               "elevator_margin_deg": round(min(d_deg + up, down - d_deg), 2),
               "control_power_cm_per_rad": round(-t["v_h"] * float(ele["eta_tail"]) * t["a_t"] * tau, 4)}
        rows.append(row)
        if d_deg < -up or d_deg > down:
            issues.append(f"{row['name']}: trim needs elevator {d_deg:.1f} deg, outside the available range -{up:g}..+{down:g} deg")
    out = {"effectiveness_tau": round(tau, 4), "cases": rows,
           "min_elevator_margin_deg": min(r["elevator_margin_deg"] for r in rows),
           "worst_trim_elevator_deg": max(rows, key=lambda r: abs(r["trim_elevator_deg"]))["trim_elevator_deg"]}
    if rotation:
        for k in ("v_r_m_s", "mass_kg", "iy_kgm2", "altitude_m", "accel_min_deg_s2"):
            if rotation.get(k) is None:
                raise CycleError(f"rotation input missing: {k}")
        g = aero.geometry(p)
        t = stability.static_margin_terms(p, 0.2, float(ele["eta_tail"]), float(st_cfg["fuselage_nacelle_forward_shift_mac"]))
        q = 0.5 * aero.air(float(rotation["altitude_m"]))["rho"] * float(rotation["v_r_m_s"]) ** 2
        cm_de = t["v_h"] * float(ele["eta_tail"]) * t["a_t"] * tau
        acc = math.degrees(q * g["s_ref"] * g["mac"] * cm_de * math.radians(up) / float(rotation["iy_kgm2"]))
        out["rotation_pitch_accel_deg_s2"] = round(acc, 2)
        out["rotation_pitch_accel_min_deg_s2"] = float(rotation["accel_min_deg_s2"])
        if acc < float(rotation["accel_min_deg_s2"]):
            issues.append(f"rotation: available pitch acceleration {acc:.1f} deg/s^2 is below the requirement {float(rotation['accel_min_deg_s2']):g}")
    out["issues"] = issues
    return out
