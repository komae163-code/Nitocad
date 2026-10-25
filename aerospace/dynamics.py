"""Lateral-directional stability and dynamic modes (stdlib only).

Linearised small-perturbation longitudinal and lateral models in stability
axes, level flight. Derivatives are ESTIMATED from geometry where a reliable
conceptual method exists (tail contributions, wing roll damping, longitudinal
terms from the stability model) and DECLARED by you where it does not
(fuselage/nacelle yaw effect, dihedral effect, inertias). Any derivative can be
overridden with a declared value. The report lists which are which.
Rough approximations used: Cnp = -CL/8, Clr = CL/4 (+-50%). No Mach corrections
beyond Prandtl-Glauert lift, no aeroelasticity, no control surfaces.
"""
import cmath
import math

from aerospace import aero, stability
from aerospace.aircraft import G, positions
from aerospace.engine_cycle import CycleError

REQUIRED = ("inertia", "cn_beta_fuselage", "cl_beta", "eta_tail", "criteria", "cases")
INERTIA = ("ix", "iy", "iz", "ixz")
OVERRIDABLE = ("cm_alpha", "cm_q", "cm_alphadot", "cl_alpha", "cl_q", "cy_beta", "cn_beta", "cl_beta",
               "cl_p", "cl_r", "cn_p", "cn_r")


def charpoly(a):
    """Coefficients [1, c1, ..., cn] of det(lambda I - A) (Faddeev-LeVerrier)."""
    n = len(a)
    m = [[0.0] * n for _ in range(n)]
    c = [1.0]
    for k in range(1, n + 1):
        # M_k = A M_{k-1} + c_{k-1} I
        m = [[sum(a[i][j] * m[j][l] for j in range(n)) + (c[-1] if i == l else 0.0) for l in range(n)] for i in range(n)]
        am = [[sum(a[i][j] * m[j][l] for j in range(n)) for l in range(n)] for i in range(n)]
        c.append(-sum(am[i][i] for i in range(n)) / k)
    return c


def roots(coefs, iters=4000):
    """Durand-Kerner roots of a real polynomial (highest power first)."""
    n = len(coefs) - 1
    lead = coefs[0]
    a = [x / lead for x in coefs]
    radius = 1 + max(abs(x) for x in a[1:])
    z = [radius * 0.6 * cmath.exp(2j * math.pi * (k + 0.37) / n) for k in range(n)]

    def poly(x):
        r = 0j
        for c in a:
            r = r * x + c
        return r
    for _ in range(iters):
        delta = 0.0
        for i in range(n):
            den = 1 + 0j
            for j in range(n):
                if j != i:
                    den *= z[i] - z[j]
            step = poly(z[i]) / den
            z[i] -= step
            delta = max(delta, abs(step))
        if delta < 1e-13:
            break
    for r in z:                                         # verify: residual relative to scale
        if abs(poly(r)) > 1e-6 * max(1.0, abs(r)) ** n:
            raise CycleError("eigenvalue solver did not converge for this derivative set")
    return z


def _pairs(eig):
    """Split eigenvalues into complex-pair modes and real roots."""
    cplx = sorted([e for e in eig if e.imag > 1e-9], key=lambda e: abs(e))
    real = sorted([e.real for e in eig if abs(e.imag) <= 1e-9])
    return [{"wn": abs(e), "zeta": -e.real / abs(e), "wd": e.imag} for e in cplx], real


def _estimated(p, case, d, st_case):
    """Returns (derivative dict, source dict) for one loading case."""
    pos = positions(p)
    t = stability.static_margin_terms(p, case["mach"], float(d["eta_tail"]), float(d.get("fuselage_nacelle_forward_shift_mac", 0.0)))
    sm = st_case["static_margin_percent_mac"] / 100.0
    x_cg = pos["mac_le_x"] + st_case["cg_percent_mac"] / 100.0 * pos["mac"]
    l_t = (pos["x_ac_h"] - x_cg) / 1e3
    l_v = (pos["x_ac_v"] - x_cg) / 1e3
    mac = pos["mac"] / 1e3
    eta = float(d["eta_tail"])
    vh = t["s_t"] * l_t / (t["s_ref"] * mac)
    cl, a_w, a_t, de = st_case["cl"], t["a_w"], t["a_t"], t["d_eps"]
    cl_alpha = a_w + eta * a_t * (t["s_t"] / t["s_ref"]) * (1 - de)
    dp = aero.drag_polar(p, case["altitude_m"], case["mach"], cl)
    g = aero.geometry(p)
    # vertical tail
    s_v = pos["s_v"]
    ar_v = pos["vtail_height"] ** 2 / s_v
    lam_v = math.radians(pos["vtail_sweep_deg"])
    a_v = aero.lift_curve_slope(ar_v, lam_v, case["mach"])
    wz = float(p.get("wing_z_ratio", -0.6)) * float(p.get("fuselage_diameter_mm", 3950.0)) / 2
    z_w = -wz / float(p.get("fuselage_diameter_mm", 3950.0))                    # positive for a low wing
    side = 0.724 + 3.06 * (s_v / t["s_ref"]) / (1 + math.cos(g["lam_c4"])) + 0.4 * z_w + 0.009 * g["ar"]
    v_v = s_v * l_v / (t["s_ref"] * pos["b"])
    cn_beta_v = a_v * side * v_v
    lam = g["lam"]
    est = {
        "cl_alpha": cl_alpha, "cm_alpha": -a_w * sm,
        "cm_q": -2 * eta * a_t * vh * l_t / mac, "cl_q": 2 * eta * a_t * vh,
        "cm_alphadot": -2 * eta * a_t * vh * (l_t / mac) * de,
        "cy_beta": -a_v * side * s_v / t["s_ref"],
        "cn_beta": cn_beta_v + float(d["cn_beta_fuselage"]), "cl_beta": float(d["cl_beta"]),
        "cl_p": -(a_w / 12) * (1 + 3 * lam) / (1 + lam), "cn_p": -cl / 8, "cl_r": cl / 4,
        "cn_r": -2 * cn_beta_v * l_v / pos["b"],
    }
    src = {k: "estimated" for k in est}
    src["cn_beta"] = "tail estimated + fuselage declared"
    src["cl_beta"] = "declared"
    for k, v in (d.get("overrides") or {}).items():
        if k not in OVERRIDABLE:
            raise CycleError(f"override {k!r} is not a recognised derivative; allowed: {', '.join(OVERRIDABLE)}")
        est[k] = float(v)
        src[k] = "declared override"
    est["_extra"] = {"cd": dp["cd"], "k": dp["k"], "cl": cl, "v_v": v_v, "a_v": a_v, "side_factor": side,
                     "cn_beta_tail": cn_beta_v, "l_t": l_t, "l_v": l_v, "vh": vh}
    return est, src


def longitudinal_matrix(p, case, der, mass, iy, s, mac, u0, q):
    cl, cd = der["_extra"]["cl"], der["_extra"]["cd"]
    m_ = case["mach"]
    cl_alpha = der["cl_alpha"]
    cd_alpha = 2 * der["_extra"]["k"] * cl * cl_alpha
    cl_u = m_ ** 2 / (1 - m_ ** 2) * cl
    qs = q * s
    xu, zu = -(0 + 2 * cd) * qs / (mass * u0), -(cl_u + 2 * cl) * qs / (mass * u0)
    xw, zw = -(cd_alpha - cl) * qs / (mass * u0), -(cl_alpha + cd) * qs / (mass * u0)
    zq = -der["cl_q"] * qs * mac / (2 * mass * u0)
    zwd = -0.0 * qs                                                     # CLalphadot neglected on the force side
    mw, mq = der["cm_alpha"] * qs * mac / (iy * u0), der["cm_q"] * qs * mac ** 2 / (2 * iy * u0)
    mwd = der["cm_alphadot"] * qs * mac ** 2 / (2 * iy * u0 ** 2)
    return [[xu, xw, 0.0, -G],
            [zu, zw, zq + u0, 0.0],
            [0.0 + mwd * zu, mw + mwd * zw, mq + mwd * (zq + u0), 0.0],
            [0.0, 0.0, 1.0, 0.0]]


def lateral_matrix(der, mass, ix, iz, ixz, s, b, u0, q):
    qs = q * s
    yb = qs * der["cy_beta"] / mass
    lb, lp, lr = (qs * b * der["cl_beta"] / ix, qs * b * b * der["cl_p"] / (2 * ix * u0), qs * b * b * der["cl_r"] / (2 * ix * u0))
    nb, np_, nr = (qs * b * der["cn_beta"] / iz, qs * b * b * der["cn_p"] / (2 * iz * u0), qs * b * b * der["cn_r"] / (2 * iz * u0))
    den = 1 - ixz ** 2 / (ix * iz)
    lbp, lpp, lrp = ((lb + ixz / ix * nb) / den, (lp + ixz / ix * np_) / den, (lr + ixz / ix * nr) / den)
    nbp, npp, nrp = ((nb + ixz / iz * lb) / den, (np_ + ixz / iz * lp) / den, (nr + ixz / iz * lr) / den)
    return [[yb / u0, 0.0, -1.0, G / u0],
            [lbp, lpp, lrp, 0.0],
            [nbp, npp, nrp, 0.0],
            [0.0, 1.0, 0.0, 0.0]]


def analyze(p: dict, d: dict, cg_rollup_percent=None) -> dict:
    miss = [k for k in REQUIRED if d.get(k) is None]
    if miss:
        raise CycleError("dynamics inputs missing: " + ", ".join(miss))
    inr = d["inertia"]
    if any(inr.get(k) is None for k in INERTIA) or min(float(inr["ix"]), float(inr["iy"]), float(inr["iz"])) <= 0:
        raise CycleError("inertia needs positive ix, iy, iz and ixz (kg m^2)")
    ix, iy, iz, ixz = (float(inr[k]) for k in INERTIA)
    if ixz ** 2 >= ix * iz:
        raise CycleError("ixz is inconsistent with ix and iz (ixz^2 must be below ix*iz)")
    crit = d["criteria"]
    cases = d["cases"]
    if not isinstance(cases, list) or not cases:
        raise CycleError("dynamics.cases must list at least one loading case")
    g = aero.geometry(p)
    out, issues = [], []
    for c in cases:
        st_cfg = {"eta_tail": d["eta_tail"], "fuselage_nacelle_forward_shift_mac": d.get("fuselage_nacelle_forward_shift_mac", 0.0)}
        stc = stability.analyze_case(p, c, st_cfg, cg_rollup_percent)
        der, src = _estimated(p, c, d, stc)
        a = aero.air(c["altitude_m"])
        u0 = c["mach"] * a["a"]
        q = 0.5 * a["rho"] * u0 ** 2
        mass, s, mac, b = float(c["mass_kg"]), g["s_ref"], g["mac"], g["span"]
        el, rl = roots(charpoly(longitudinal_matrix(p, c, der, mass, iy, s, mac, u0, q))), None
        rl = roots(charpoly(lateral_matrix(der, mass, ix, iz, ixz, s, b, u0, q)))
        lp, lr = _pairs(el)
        dp_, dr_real = _pairs(rl)
        row = {"name": c.get("name", "case"), "mach": c["mach"], "altitude_m": c["altitude_m"], "mass_kg": mass,
               "cg_percent_mac": stc["cg_percent_mac"], "static_margin_percent_mac": stc["static_margin_percent_mac"],
               "cn_beta_total": round(der["cn_beta"], 4), "cn_beta_tail": round(der["_extra"]["cn_beta_tail"], 4),
               "vertical_tail_volume": round(der["_extra"]["v_v"], 4), "derivative_sources": src}
        if len(lp) >= 2:
            sp, ph = lp[-1], lp[0]
            row.update(short_period_wn=round(sp["wn"], 3), short_period_zeta=round(sp["zeta"], 3),
                       phugoid_wn=round(ph["wn"], 4), phugoid_zeta=round(ph["zeta"], 4))
        else:
            row["longitudinal_note"] = "fewer than two oscillatory modes: overdamped or unstable; eigenvalues " + ", ".join(f"{e:.3g}" for e in el)
        if dp_:
            dr = dp_[-1]
            row.update(dutch_roll_wn=round(dr["wn"], 3), dutch_roll_zeta=round(dr["zeta"], 4), dutch_roll_wn_zeta=round(dr["wn"] * dr["zeta"], 4))
        else:
            row["lateral_note"] = "no Dutch-roll oscillation found (aperiodic): eigenvalues " + ", ".join(f"{e:.3g}" for e in rl)
        if dr_real:
            roll = min(dr_real)
            spiral = max(dr_real) if len(dr_real) > 1 else dr_real[0]
            row["roll_mode_tau_s"] = round(-1 / roll, 3) if roll < 0 else None
            row["spiral_root_per_s"] = round(spiral, 5)
            row["spiral_stable"] = bool(spiral < 0)
            row["spiral_time_to_double_s"] = round(math.log(2) / spiral, 1) if spiral > 0 else None
        out.append(row)
        nm = row["name"]
        checks = (("phugoid_zeta", "phugoid_zeta_min", ">="), ("short_period_zeta", "short_period_zeta_min", ">="),
                  ("short_period_zeta", "short_period_zeta_max", "<="), ("dutch_roll_zeta", "dutch_roll_zeta_min", ">="),
                  ("dutch_roll_wn_zeta", "dutch_roll_wn_zeta_min", ">="), ("dutch_roll_wn", "dutch_roll_wn_min", ">="),
                  ("roll_mode_tau_s", "roll_tau_max_s", "<="))
        for key, ck, op in checks:
            if crit.get(ck) is None:
                continue
            v = row.get(key)
            if v is None:
                issues.append(f"{nm}: {key} could not be determined, cannot check {ck}")
            elif (op == ">=" and v < crit[ck]) or (op == "<=" and v > crit[ck]):
                issues.append(f"{nm}: {key} = {v} violates {ck} {op} {crit[ck]}")
        if crit.get("spiral_t2_min_s") is not None and row.get("spiral_stable") is False:
            t2 = row.get("spiral_time_to_double_s")
            if t2 is not None and t2 < crit["spiral_t2_min_s"]:
                issues.append(f"{nm}: spiral time to double {t2} s is below {crit['spiral_t2_min_s']} s")
        if row["cn_beta_total"] <= 0:
            issues.append(f"{nm}: total yaw stiffness Cnbeta = {row['cn_beta_total']} is not positive (directionally unstable)")
    return {"cases": out, "issues": issues,
            "min_dutch_roll_zeta": min((r["dutch_roll_zeta"] for r in out if "dutch_roll_zeta" in r), default=None),
            "min_cn_beta": min(r["cn_beta_total"] for r in out)}
