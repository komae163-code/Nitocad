"""Takeoff and landing performance for jet_aircraft (stdlib only).

Ground roll and braking are integrated numerically against engine thrust at
each speed (off-design model). Air-phase segments follow the standard
conceptual-design circular-arc transition (Raymer-style). Screening level:
all-engines-operating takeoff, one-engine-out second-segment gradient, landing
with braking. NOT built: balanced field length and V1 decision, wet/contaminated
runways, wind and slope, brake energy, noise or obstacle-clearance procedures.

Speeds and factors that are method constants (override in the field block):
V_LOF = 1.1 Vs, transition at 1.15 Vs, V2 = 1.2 Vs, approach 1.23 Vs,
touchdown 1.15 Vs, approach angle 3 degrees, load factor in arcs 1.2.
"""
import math

from aerospace import aero
from aerospace.aircraft import G
from aerospace.engine_cycle import CycleError, solve_design
from aerospace.performance import rating_tt4
from aerospace.engine_offdesign import offdesign_point

REQUIRED = ("altitude_m", "tt4_max_k", "takeoff_mass_kg", "landing_mass_kg", "cl_max_takeoff", "cl_max_landing",
            "cl_ground_roll", "mu_roll", "mu_brake", "delta_cd0_takeoff", "delta_cd0_landing", "delta_cd0_oei",
            "rotation_time_s")
FAR25_SECOND_SEGMENT_MIN = {2: 0.024, 3: 0.027, 4: 0.030}     # minimum net climb gradients


def _vs(rho, w_n, s, cl_max):
    return math.sqrt(2 * w_n / (rho * s * cl_max))


def ground_roll(thrust_fn, v_end, w_n, s, rho, cd0, k, cl_g, mu, steps=240, v_start=0.0):
    """Integrate dV/dt = g[(T/W - mu) - (rho V^2 / (2 W/S)) (CD0 + k CL^2 - mu CL)]. Returns (distance m, time s)."""
    dist = time = 0.0
    dv = (v_end - v_start) / steps
    if dv < 0:
        raise CycleError("ground roll end speed is below its start speed")
    for i in range(steps):
        v = v_start + (i + 0.5) * dv
        acc = G * ((thrust_fn(v) / w_n - mu) - rho * v * v / (2 * w_n / s) * (cd0 + k * cl_g ** 2 - mu * cl_g))
        if acc <= 0:
            raise CycleError("aircraft cannot accelerate to the target speed: thrust too low or drag/rolling resistance too high")
        dist += v * dv / acc
        time += dv / acc
    return dist, time


def braking(v0, w_n, s, rho, cd0, k, cl_g, mu_b, rev_over_w, steps=240):
    """Deceleration distance from v0 to rest: a = g[mu_b + Trev/W] + g rho V^2/(2 W/S) (CD0 + k CL^2 - mu_b CL)."""
    dist = 0.0
    dv = v0 / steps
    for i in range(steps):
        v = (i + 0.5) * dv
        dec = G * (mu_b + rev_over_w) + G * rho * v * v / (2 * w_n / s) * (cd0 + k * cl_g ** 2 - mu_b * cl_g)
        dist += v * dv / dec
    return dist


def _air_arc(v, gamma_rad, h_obs):
    """Circular-arc transition at load factor 1.2. Returns (horizontal distance, height gained in arc)."""
    r = v * v / (0.2 * G)
    h_tr = r * (1 - math.cos(gamma_rad))
    if h_tr >= h_obs:
        return math.sqrt(max(0.0, r * r - (r - h_obs) ** 2)), h_obs
    return r * math.sin(gamma_rad), h_tr


def _balanced_field(p, f, n_eng, rho, s_ref, cd0, k, vs, v_lof, v2, w, thrust_per_engine, aeo_m):
    """Accelerate-go and accelerate-stop distances versus V1; BFL where they are equal (or the limiting case)."""
    for key in ("reaction_time_s", "delta_cd0_inoperative_engine"):
        if f.get(key) is None:
            raise CycleError(f"balanced field inputs missing: {key}")
    vmcg = float(f["vmcg_m_s"])
    if not 0 < vmcg < v_lof:
        raise CycleError("vmcg_m_s must be positive and below the liftoff speed")
    t_all = lambda v: n_eng * thrust_per_engine(v)
    t_oei = lambda v: (n_eng - 1) * thrust_per_engine(v)
    cd_roll = cd0 + f["delta_cd0_takeoff"]
    cd_oei_roll = cd_roll + float(f["delta_cd0_inoperative_engine"])
    cl2 = f["cl_max_takeoff"] / 1.44
    d2 = 0.5 * rho * v2 ** 2 * s_ref * (cd0 + f["delta_cd0_oei"] + k * cl2 ** 2)
    sin_oei = (t_oei(v2) - d2) / w
    result = {}
    for tag, mu_b, h in (("dry", float(f["mu_brake"]), float(f.get("obstacle_height_m", 10.668))),
                         ("wet", f.get("mu_brake_wet"), float(f.get("wet_screen_height_m", 4.572)))):
        if mu_b is None:
            continue
        mu_b = float(mu_b)

        def go(v1):
            s1, _ = ground_roll(t_all, v1, w, s_ref, rho, cd_roll, k, f["cl_ground_roll"], f["mu_roll"])
            s2, _ = ground_roll(t_oei, v_lof, w, s_ref, rho, cd_oei_roll, k, f["cl_ground_roll"], f["mu_roll"], v_start=v1)
            if sin_oei <= 0.001:
                raise CycleError("one-engine-out climb gradient at V2 is not positive: the aircraft cannot continue the takeoff")
            gam = math.asin(min(sin_oei, 0.5))
            s_tr, h_tr = _air_arc(v2, gam, h)
            return s1 + s2 + f["rotation_time_s"] * v_lof + s_tr + max(0.0, (h - h_tr) / math.tan(gam))

        def stop(v1):
            s1, _ = ground_roll(t_all, v1, w, s_ref, rho, cd_roll, k, f["cl_ground_roll"], f["mu_roll"])
            return s1 + float(f["reaction_time_s"]) * v1 + braking(v1, w, s_ref, rho, cd_roll, k, float(f.get("cl_ground_landing", 0.0)), mu_b, 0.0)

        try:
            lo, hi = vmcg, v_lof
            f_lo, f_hi = go(lo) - stop(lo), go(hi) - stop(hi)
            if f_hi >= 0:
                v1, limit, bfl = hi, "go distance still longer than stop distance at V1 = VR (engine-out acceleration or climb is limiting)", go(hi)
            elif f_lo <= 0:
                v1, limit, bfl = lo, "stop distance longer than go distance even at V1 = VMCG (braking is limiting)", stop(lo)
            else:
                for _ in range(40):
                    mid = (lo + hi) / 2
                    lo, hi = (mid, hi) if go(mid) - stop(mid) > 0 else (lo, mid)
                v1, limit = (lo + hi) / 2, "balanced"
                bfl = max(go(v1), stop(v1))
        except CycleError as e:
            result[f"bfl_{tag}_unavailable"] = str(e)
            continue
        result[f"bfl_{tag}_m"] = round(bfl, 0)
        result[f"v1_{tag}_m_s"] = round(v1, 1)
        result[f"bfl_{tag}_limited_by"] = limit
        result[f"takeoff_field_length_{tag}_m"] = round(max(bfl, 1.15 * aeo_m), 0)
    if f.get("tire_pressure_kpa") is not None and "v1_wet_m_s" in result:
        v_hp = 9.0 * math.sqrt(float(f["tire_pressure_kpa"]) * 0.145038) * 0.514444
        result["v1_wet_above_hydroplaning_speed"] = bool(result["v1_wet_m_s"] > v_hp)
    return result


def analyze(p: dict, cycle: dict, f: dict) -> dict:
    miss = [k for k in REQUIRED if f.get(k) is None]
    if miss:
        raise CycleError("field inputs missing: " + ", ".join(miss))
    if not 0 <= f["mu_roll"] <= 0.1 or not 0.05 <= f["mu_brake"] <= 0.9:
        raise CycleError("mu_roll must be 0..0.1 and mu_brake 0.05..0.9")
    if f["cl_max_takeoff"] <= 0 or f["cl_max_landing"] <= 0 or f["rotation_time_s"] < 0:
        raise CycleError("cl_max values must be positive and rotation_time_s non-negative")
    _, st = solve_design(cycle)
    n_eng = int(p.get("engines", 2))
    if n_eng < 1:
        raise CycleError("field performance needs engines >= 1")
    alt = float(f["altitude_m"])
    a = aero.air(alt)
    rho, sound = a["rho"], a["a"]
    s_ref = aero.geometry(p)["s_ref"]
    pol = aero.drag_polar(p, alt, 0.2, 0.5)
    cd0, k = pol["cd0"], pol["k"]
    cap = float(f.get("max_corrected_flow_ratio", 1.0))
    tt4_max = float(f["tt4_max_k"])
    h_obs = float(f.get("obstacle_height_m", 10.668))

    def _thrust_exact(v):
        m = max(v / sound, 0.0)
        top = rating_tt4(st, alt, m, tt4_max, cap)
        if top is None:
            return 0.0
        try:
            return offdesign_point(st, alt, m, top)["thrust_n"]
        except CycleError:
            return 0.0

    # Thrust is a smooth function of speed, but each exact evaluation runs a rating bisection through the
    # engine model, and the roll integrations need it tens of thousands of times. Tabulate once, interpolate.
    v_top = 1.5 * _vs(rho, f["takeoff_mass_kg"] * G, s_ref, f["cl_max_takeoff"])
    n_knots = 48
    knots = [v_top * i / n_knots for i in range(n_knots + 1)]
    table = [_thrust_exact(v) for v in knots]

    def thrust_per_engine(v):
        if v <= 0.0:
            return table[0]
        if v >= v_top:
            return _thrust_exact(v)           # outside the tabulated range: exact (rare)
        x = v / v_top * n_knots
        i = int(x)
        return table[i] + (table[i + 1] - table[i]) * (x - i)

    # ---- takeoff (all engines)
    w = f["takeoff_mass_kg"] * G
    vs = _vs(rho, w, s_ref, f["cl_max_takeoff"])
    v_lof = float(f.get("lof_factor", 1.1)) * vs
    t_fn = lambda v: n_eng * thrust_per_engine(v)
    s_g, t_g = ground_roll(t_fn, v_lof, w, s_ref, rho, cd0 + f["delta_cd0_takeoff"], k, f["cl_ground_roll"], f["mu_roll"])
    s_r = f["rotation_time_s"] * v_lof
    v_tr = 1.15 * vs
    cl_tr = f["cl_max_takeoff"] / 1.15 ** 2
    q_tr = 0.5 * rho * v_tr ** 2
    d_tr = q_tr * s_ref * (cd0 + f["delta_cd0_takeoff"] + k * cl_tr ** 2)
    sin_g = (n_eng * thrust_per_engine(v_tr) - d_tr) / w
    if sin_g <= 0.005:
        raise CycleError("aircraft cannot climb away after liftoff in the takeoff configuration (thrust <= drag)")
    gamma = math.asin(min(sin_g, 0.5))
    s_tr, h_tr = _air_arc(v_tr, gamma, h_obs)
    s_c = max(0.0, (h_obs - h_tr) / math.tan(gamma))
    total = s_g + s_r + s_tr + s_c

    # ---- second segment (one engine out, gear up, V2 = 1.2 Vs)
    v2 = 1.2 * vs
    cl2 = f["cl_max_takeoff"] / 1.44
    d2 = 0.5 * rho * v2 ** 2 * s_ref * (cd0 + f["delta_cd0_oei"] + k * cl2 ** 2)
    grad = ((n_eng - 1) * thrust_per_engine(v2) - d2) / w if n_eng > 1 else None
    req = FAR25_SECOND_SEGMENT_MIN.get(n_eng)

    # ---- landing
    wl = f["landing_mass_kg"] * G
    vsl = _vs(rho, wl, s_ref, f["cl_max_landing"])
    v_a, v_td = 1.23 * vsl, 1.15 * vsl
    gamma_a = math.radians(float(f.get("approach_angle_deg", 3.0)))
    r_f = v_a ** 2 / (0.2 * G)
    h_f = r_f * (1 - math.cos(gamma_a))
    s_air = r_f * math.sin(gamma_a) + max(0.0, (h_obs - h_f) / math.tan(gamma_a))
    s_free = float(f.get("free_roll_s", 2.0)) * v_td
    s_brk = braking(v_td, wl, s_ref, rho, cd0 + f["delta_cd0_landing"], k, float(f.get("cl_ground_landing", 0.0)),
                    f["mu_brake"], float(f.get("reverse_thrust_fraction", 0.0)))
    land = s_air + s_free + s_brk
    out = {
        "takeoff_vs_m_s": round(vs, 2), "takeoff_v_lof_m_s": round(v_lof, 2), "takeoff_v2_m_s": round(v2, 2),
        "takeoff_thrust_to_weight": round(n_eng * thrust_per_engine(0.0) / w, 3),
        "takeoff_ground_roll_m": round(s_g, 0), "takeoff_ground_time_s": round(t_g, 1),
        "takeoff_distance_aeo_m": round(total, 0), "takeoff_distance_aeo_x115_m": round(1.15 * total, 0),
        "takeoff_climb_gradient_after_liftoff": round(sin_g, 4),
        "landing_vs_m_s": round(vsl, 2), "landing_vref_m_s": round(v_a, 2),
        "landing_air_distance_m": round(s_air, 0), "landing_braking_distance_m": round(s_brk, 0),
        "landing_distance_actual_m": round(land, 0), "landing_distance_factored_m": round(land / 0.6, 0),
    }

    # ---- balanced field length with one engine failing during the roll (dry and wet)
    if f.get("vmcg_m_s") is not None and n_eng > 1:
        out.update(_balanced_field(p, f, n_eng, rho, s_ref, cd0, k, vs, v_lof, v2, w, thrust_per_engine, out["takeoff_distance_aeo_m"]))
    if f.get("mu_brake_wet") is not None:
        mu_w = float(f["mu_brake_wet"])
        brk_w = braking(v_td, wl, s_ref, rho, cd0 + f["delta_cd0_landing"], k, float(f.get("cl_ground_landing", 0.0)), mu_w,
                        float(f.get("reverse_thrust_fraction", 0.0)))
        wet = s_air + s_free + brk_w
        out["landing_distance_wet_actual_m"] = round(wet, 0)
        out["landing_distance_wet_factored_m"] = round(max(wet / 0.6, 1.15 * land / 0.6), 0)
    if f.get("tire_pressure_kpa") is not None:
        v_hp = 9.0 * math.sqrt(float(f["tire_pressure_kpa"]) * 0.145038) * 0.514444     # NASA hydroplaning speed
        out["hydroplaning_speed_m_s"] = round(v_hp, 1)
        out["touchdown_above_hydroplaning_speed"] = bool(v_td > v_hp)
    if grad is not None:
        out["second_segment_gradient"] = round(grad, 4)
        out["second_segment_min_far25"] = req
        out["meets_second_segment_min"] = bool(req is not None and grad >= req)
    return out
