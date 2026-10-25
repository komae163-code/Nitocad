"""Closed-form screening analyses. Units: mm, N, MPa, kg.

Every method returns an AnalysisResult that states its assumptions and the
limits of its validity. If an input is missing or the part is outside the
method's validity range the status is 'not_evaluable' with a reason; the code
never substitutes a default for a missing load, factor or allowable.

THESE ARE SCREENING CHECKS. They do not replace FEA, test, fatigue, flutter,
damage tolerance or any certification analysis (see release.REQUIRED_EVIDENCE).
"""
import math
from dataclasses import dataclass, field, asdict
from typing import Optional

from aerospace.airfoil import naca4_points, polygon_section_properties
from aerospace.materials import Material

METHOD_VERSIONS = {
    "wing_root_bending": "1.0",
    "frame_diametral_ring": "1.0",
    "bearing": "1.0",
    "spar_bending": "1.0",
    "lug_check": "1.0",
    "mass": "1.0",
    "engine_cycle": "1.0",
    "aircraft_metrics": "1.0",
    "mass_declared": "1.0",
    "engine_offdesign": "1.0",
    "engine_meanline": "1.0",
    "aircraft_performance": "1.0",
    "mass_rollup": "1.0",
    "stability_trim": "1.0",
    "field_performance": "1.0",
    "highlift": "1.0",
    "dynamics": "1.0",
    "elevator_sizing": "1.0",
}

# Which spec inputs each method consumes (drives change-impact reporting).
CONSUMES = {
    "wing_root_bending": ["params.naca", "params.root_chord_mm", "params.tip_chord_mm",
                          "params.span_mm", "material", "loads.wing_limit_lift",
                          "factors.ultimate"],
    "frame_diametral_ring": ["params.outer_diameter_mm", "params.ring_width_mm",
                             "params.thickness_mm", "params.lightening_hole_count",
                             "params.lightening_hole_diameter_mm", "material",
                             "loads.frame_diametral", "factors.ultimate"],
    "bearing": ["attachment", "material", "factors.ultimate"],
    "spar_bending": ["params.length_mm", "params.height_mm", "params.flange_width_mm",
                     "params.flange_thickness_mm", "params.web_thickness_mm", "material",
                     "loads.spar_tip_load", "factors.ultimate"],
    "engine_cycle": ["cycle"],
    "aircraft_metrics": ["params", "weights"],
    "mass_declared": ["mass_properties"],
    "engine_offdesign": ["cycle"],
    "engine_meanline": ["cycle", "params.fan_diameter_mm"],
    "aircraft_performance": ["params", "cycle", "flight"],
    "mass_rollup": ["mass_components", "params"],
    "stability_trim": ["params", "stability", "mass_components"],
    "field_performance": ["params", "cycle", "field", "highlift"],
    "highlift": ["params", "highlift"],
    "dynamics": ["params", "dynamics", "mass_components"],
    "elevator_sizing": ["params", "elevator", "dynamics.inertia"],
    "lug_check": ["params.width_mm", "params.thickness_mm", "params.hole_diameter_mm", "material",
                  "loads.lug_axial", "factors.ultimate"],
    "mass": ["params", "material", "geometry.volume_mm3"],
}


@dataclass
class AnalysisResult:
    method: str
    status: str                 # 'pass' | 'fail' | 'not_evaluable'
    summary: str
    outputs: dict = field(default_factory=dict)
    assumptions: list = field(default_factory=list)
    limits: list = field(default_factory=list)
    reasons: list = field(default_factory=list)   # why not_evaluable / why failed
    warnings: list = field(default_factory=list)
    version: str = ""

    def to_dict(self):
        return asdict(self)


def _na(method, why, **kw):
    return AnalysisResult(method, "not_evaluable", f"Not evaluable: {why}", reasons=[why],
                          version=METHOD_VERSIONS[method], **kw)


def _margins(sigma_limit, mat: Material, fs_ult: float):
    ms_u = mat.ftu_mpa / (fs_ult * sigma_limit) - 1.0
    ms_y = mat.fty_mpa / sigma_limit - 1.0      # no yield at limit load
    return ms_u, ms_y


def _finish(method, sigma, mat, fs_u, outputs, assumptions, limits):
    ms_u, ms_y = _margins(sigma, mat, fs_u)
    ms = min(ms_u, ms_y)
    outputs.update(sigma_limit_mpa=round(sigma, 3), ms_ultimate=round(ms_u, 4),
                   ms_yield=round(ms_y, 4), ms_min=round(ms, 4))
    warnings = []
    if not mat.is_design_allowable:
        warnings.append("Margins use TYPICAL material properties, not design allowables.")
    return AnalysisResult(
        method, "pass" if ms >= 0 else "fail",
        f"Minimum margin of safety {ms:+.3f} ({'governed by ultimate' if ms_u <= ms_y else 'governed by yield'})",
        outputs, assumptions, limits,
        [] if ms >= 0 else ["Negative margin of safety"], warnings, METHOD_VERSIONS[method])


def wing_root_bending(params, mat, semi_span_limit_lift_n, distribution, fs_ult) -> AnalysisResult:
    m = "wing_root_bending"
    if semi_span_limit_lift_n is None or fs_ult is None:
        return _na(m, "limit lift and ultimate factor of safety must both be supplied")
    if distribution not in ("elliptic", "uniform"):
        return _na(m, "distribution must be 'elliptic' or 'uniform'")
    b = float(params.get("span_mm", 600.0))
    root = naca4_points(params.get("naca", "2412"), float(params.get("root_chord_mm", 200.0)))
    sec = polygon_section_properties(root)
    arm = 4 * b / (3 * math.pi) if distribution == "elliptic" else b / 2
    moment = semi_span_limit_lift_n * arm
    sigma = moment * sec["c_max_y"] / sec["ixx"]
    return _finish(
        m, sigma, mat, fs_ult,
        {"root_bending_moment_nmm": round(moment, 1), "load_arm_mm": round(arm, 2),
         "root_ixx_mm4": round(sec["ixx"], 1), "c_max_mm": round(sec["c_max_y"], 3)},
        ["Modeled solid root section carries all bending (as generated by the template)",
         "Euler-Bernoulli beam theory, linear elastic",
         f"{distribution} spanwise lift distribution on the semi-span",
         "Root section governs (largest moment and largest section); taper ignored",
         "Shear, torsion, sweep effects and inertial relief ignored"],
        ["Straight, slender beam: span >= 2x root chord",
         "Not valid for hollow/ribbed construction different from the modeled solid"]
    ) if b >= 2 * float(params.get("root_chord_mm", 200.0)) else _na(
        m, "span < 2x root chord: beam theory not valid for this planform")


def frame_diametral_ring(params, mat, load_n, fs_ult, kt=None) -> AnalysisResult:
    m = "frame_diametral_ring"
    if load_n is None or fs_ult is None:
        return _na(m, "limit load and ultimate factor of safety must both be supplied")
    ro = float(params.get("outer_diameter_mm", 300.0)) / 2
    w = float(params.get("ring_width_mm", 40.0))
    t = float(params.get("thickness_mm", 2.0))
    n = int(params.get("lightening_hole_count", 8))
    hd = float(params.get("lightening_hole_diameter_mm", 0.0)) or w * 0.5
    r_mean = ro - w / 2
    if w / r_mean > 0.3:
        return _na(m, "w / R_mean > 0.3: thin-ring theory not valid")
    has_holes = n > 0
    if has_holes and hd >= w:
        return _na(m, "lightening hole diameter >= ring width")
    i_net = t * (w ** 3 - (hd ** 3 if has_holes else 0.0)) / 12.0
    kt_used = kt if kt is not None else (3.0 if has_holes else 1.0)
    moment = load_n * r_mean / math.pi               # at load point, Roark diametral ring
    sigma = kt_used * moment * (w / 2) / i_net
    return _finish(
        m, sigma, mat, fs_ult,
        {"max_ring_moment_nmm": round(moment, 1), "i_net_mm4": round(i_net, 1),
         "kt_used": kt_used, "r_mean_mm": round(r_mean, 2)},
        ["Thin circular ring, opposed diametral point loads (M = P R / pi at the load point)",
         "Inner flange neglected (conservative)",
         "Lightening hole assumed at the load point; net section used",
         f"Stress concentration Kt = {kt_used}"
         + (" (classical hole value, conservative)" if kt is None and has_holes else "")],
        ["w / R_mean <= 0.3", "In-plane loading only", "No pressure, no out-of-plane load"])


def bearing(attachment, mat, fs_ult) -> AnalysisResult:
    m = "bearing"
    if not attachment or fs_ult is None:
        return _na(m, "attachment (limit load, diameter, thickness) and factor of safety required")
    if mat.fbru_mpa is None:
        return _na(m, "material has no bearing allowable (fbru_mpa); supply one with a source")
    p, d, t = (float(attachment[k]) for k in ("limit_load_n", "hole_diameter_mm", "thickness_mm"))
    if min(p, d, t) <= 0:
        return _na(m, "load, diameter and thickness must be positive")
    sigma = p / (d * t)
    ms = mat.fbru_mpa / (fs_ult * sigma) - 1.0
    out = {"bearing_stress_mpa": round(sigma, 3), "ms_bearing": round(ms, 4), "ms_min": round(ms, 4)}
    warns = [] if mat.is_design_allowable else ["Bearing allowable is not a design-basis value."]
    return AnalysisResult(m, "pass" if ms >= 0 else "fail", f"Bearing margin of safety {ms:+.3f}", out,
                          ["Single fastener, load applied through the hole centre", "Nominal bearing stress P/(d t)"],
                          ["Edge distance e/D >= 2 must be confirmed separately", "Allowable must match e/D and joint type"],
                          [] if ms >= 0 else ["Negative margin of safety"], warns, METHOD_VERSIONS[m])


def wing_volume_mm3(params) -> float:
    """Exact volume of the tapered solid (section area scales with chord^2)."""
    ar = polygon_section_properties(naca4_points(params.get("naca", "2412"), float(params.get("root_chord_mm", 200.0))))["area"]
    lam = float(params.get("tip_chord_mm", 120.0)) / float(params.get("root_chord_mm", 200.0))
    return float(params.get("span_mm", 600.0)) * ar * (1 + lam + lam * lam) / 3.0


def mass(part_type, params, mat, volume_mm3: Optional[float]) -> AnalysisResult:
    m = "mass"
    src = "supplied CAD volume"
    if volume_mm3 is None and part_type == "airfoil_wing":
        volume_mm3, src = wing_volume_mm3(params), "analytic tapered-section volume (polyline airfoil)"
    if volume_mm3 is None:
        return _na(m, "volume unavailable: supply geometry.volume_mm3 from the CAD solid")
    kg = volume_mm3 * mat.density_g_cc * 1e-6
    return AnalysisResult(m, "pass", f"Mass {kg:.4f} kg", {"mass_kg": round(kg, 5), "volume_mm3": round(volume_mm3, 1)},
                          [f"Volume from {src}", "Homogeneous density; no fasteners, coatings or tolerances"],
                          ["Mass of this single part only"], version=METHOD_VERSIONS[m])


def spar_bending(params, mat, tip_load_n, fs_ult) -> AnalysisResult:
    """Cantilever I-spar, tip limit load P at length L: bending, web shear, web shear buckling."""
    from aerospace.sections import i_beam_points
    m = "spar_bending"
    if tip_load_n is None or fs_ult is None:
        return _na(m, "limit tip load and ultimate factor of safety must both be supplied")
    L, h = float(params.get("length_mm", 600.0)), float(params.get("height_mm", 40.0))
    fw, tf, tw = (float(params.get(k, d)) for k, d in
                  (("flange_width_mm", 30.0), ("flange_thickness_mm", 3.0), ("web_thickness_mm", 2.0)))
    if 2 * tf >= h or tw >= fw:
        return _na(m, "invalid I-section proportions")
    if L < 5 * h:
        return _na(m, "length < 5x height: beam theory not valid for a short spar")
    sec = polygon_section_properties(i_beam_points(h, fw, tf, tw))
    moment, shear = tip_load_n * L, tip_load_n
    sigma = moment * (h / 2) / sec["ixx"]
    web_h = h - 2 * tf
    tau = shear / (web_h * tw)
    k_s = 5.34                                   # long simply supported plate in shear
    tau_cr = k_s * math.pi ** 2 * mat.e_mpa / (12 * (1 - mat.nu ** 2)) * (tw / web_h) ** 2
    tau_pl = 0.5 * mat.fty_mpa / math.sqrt(3)    # conservative proportional-limit cap
    tau_used = min(tau_cr, tau_pl)
    ms_buck = tau_used / (fs_ult * tau) - 1.0
    res = _finish(m, sigma, mat, fs_ult,
                  {"root_moment_nmm": round(moment, 1), "ixx_mm4": round(sec["ixx"], 1),
                   "web_shear_mpa": round(tau, 3), "tau_cr_elastic_mpa": round(tau_cr, 2),
                   "tau_buckling_used_mpa": round(tau_used, 2), "ms_web_buckling": round(ms_buck, 4)},
                  ["Cantilever, tip limit load applied through the shear centre",
                   "Flanges and web both effective; bending stress from I at the extreme fibre",
                   "Web shear = V / (clear web height x web thickness), average",
                   "Web buckling: simply supported long plate, k = 5.34, no stiffeners",
                   "Elastic buckling stress capped at 0.5 x shear yield (conservative plasticity bound)"],
                  ["Slender spar: length >= 5x height", "No lateral-torsional buckling, no crippling, no fatigue"])
    ms = min(res.outputs["ms_min"], ms_buck)
    res.outputs["ms_min"] = round(ms, 4)
    if ms < 0 and res.status == "pass":
        res.status, res.reasons = "fail", ["Negative web buckling margin"]
        res.summary = f"Minimum margin of safety {ms:+.3f} (web buckling)"
    return res


def lug_check(params, mat, axial_load_n, fs_ult, kt=None) -> AnalysisResult:
    """Single lug under axial pin load: net tension, bearing, shear tear-out."""
    m = "lug_check"
    if axial_load_n is None or fs_ult is None:
        return _na(m, "limit axial load and ultimate factor of safety must both be supplied")
    w, t, d = (float(params.get(k, v)) for k, v in
               (("width_mm", 30.0), ("thickness_mm", 6.0), ("hole_diameter_mm", 10.0)))
    if w / d < 1.5:
        return _na(m, "w/D < 1.5: outside the range of this screening method")
    if mat.fbru_mpa is None or mat.fsu_mpa is None:
        return _na(m, "material needs both fbru_mpa and fsu_mpa (with source) for lug checks")
    kt_used = kt if kt is not None else 3.0
    e = w / 2                                            # hole centre to end
    s_net = kt_used * axial_load_n / ((w - d) * t)
    s_br = axial_load_n / (d * t)
    tau_so = axial_load_n / (2 * t * (e - (d / 2) * math.cos(math.radians(40))))
    ms_net = mat.ftu_mpa / (fs_ult * s_net) - 1
    ms_br = mat.fbru_mpa / (fs_ult * s_br) - 1
    ms_so = mat.fsu_mpa / (fs_ult * tau_so) - 1
    ms = min(ms_net, ms_br, ms_so)
    warns = [] if mat.is_design_allowable else ["Allowables are not design-basis values."]
    return AnalysisResult(
        m, "pass" if ms >= 0 else "fail", f"Lug minimum margin of safety {ms:+.3f}",
        {"net_tension_mpa": round(s_net, 2), "bearing_mpa": round(s_br, 2), "shear_out_mpa": round(tau_so, 2),
         "ms_net_tension": round(ms_net, 4), "ms_bearing": round(ms_br, 4), "ms_shear_out": round(ms_so, 4),
         "ms_min": round(ms, 4), "kt_used": kt_used},
        ["Single lug, axial load through the hole centre, pin fits without clearance effects",
         f"Net tension with Kt = {kt_used}" + (" (conservative default)" if kt is None else ""),
         "Shear tear-out on two planes at 40 degrees; e = w/2 (full-round end)"],
        ["w/D >= 1.5", "Axial load only: no transverse or oblique load", "Static strength only: no fatigue"],
        [] if ms >= 0 else ["Negative margin of safety"], warns, METHOD_VERSIONS[m])


def engine_cycle(cycle: dict) -> AnalysisResult:
    """Design-point turbofan/turbojet cycle (see aerospace.engine_cycle)."""
    from aerospace.engine_cycle import turbofan_design_point, CycleError, REQUIRED
    m = "engine_cycle"
    vals = {k: cycle.get(k) for k in REQUIRED}
    try:
        out = turbofan_design_point(vals)
    except CycleError as e:
        return _na(m, str(e))
    return AnalysisResult(
        m, "pass", f"Thrust {out['thrust_n'] / 1000:.1f} kN, TSFC {out['tsfc_g_per_kn_s']:.2f} g/(kN s)", out,
        ["Design point only, calorically perfect gas (cold gamma 1.4, hot gamma 1.33)",
         "Ideally expanded nozzles, no bleed or turbine cooling flow, no installation loss",
         "Component efficiencies are inputs you supplied, not predictions",
         "Fuel lower heating value 43 MJ/kg"],
        ["Mach 0..1.0, altitude 0..20 km", "Separate-flow turbofan or turbojet (bpr = 0)",
         "Not an engine certification, off-design or transient analysis"],
        version=METHOD_VERSIONS[m])


def aircraft_metrics_result(params, mtow_kg, static_thrust_n) -> AnalysisResult:
    from aerospace.aircraft import aircraft_metrics
    m = "aircraft_metrics"
    try:
        out, warns = aircraft_metrics(params, mtow_kg, static_thrust_n)
    except ValueError as e:
        return _na(m, str(e))
    return AnalysisResult(
        m, "pass", f"Wing {out['wing_area_m2']} m2, AR {out['aspect_ratio']}, tail volumes {out['horizontal_tail_volume']}/{out['vertical_tail_volume']}",
        out, ["Trapezoidal gross wing planform through the fuselage", "Aerodynamic centres at quarter MAC (no wing-body or downwash effects)",
              "Reference bands are textbook guidance, not requirements"],
        ["Concept-level sizing only: no stability derivatives, no performance, no loads"], [], warns, METHOD_VERSIONS[m])


def mass_declared(value_kg) -> AnalysisResult:
    m = "mass_declared"
    if value_kg is None:
        return _na(m, "outer-mold-line geometry has no meaningful volume mass: supply mass_properties with a source")
    return AnalysisResult(m, "pass", f"Declared mass {value_kg:.1f} kg", {"mass_kg": round(value_kg, 3)},
                          ["Mass is the value you supplied with its source, not computed from geometry"],
                          [], version=METHOD_VERSIONS[m])


def engine_offdesign(cycle: dict) -> AnalysisResult:
    from aerospace.engine_cycle import CycleError, REQUIRED
    from aerospace.engine_offdesign import sweep
    m = "engine_offdesign"
    od = cycle.get("offdesign")
    if not isinstance(od, dict):
        return _na(m, "cycle.offdesign (altitudes_m, machs, tt4_k lists) not supplied")
    try:
        r = sweep({k: cycle.get(k) for k in REQUIRED}, od.get("altitudes_m") or [], od.get("machs") or [],
                  od.get("tt4_k") or [], {k: od.get(k) for k in ("tt3_max_k", "tt4_max_k") if od.get(k) is not None})
    except (CycleError, TypeError, ValueError) as e:
        return _na(m, str(e))
    if not r["points"]:
        return _na(m, "no valid off-design points in the requested sweep")
    outs = dict(r["summary"], points_table=r["points"][:200], design_reproduced=r["design_reproduced"],
                invalid_points=len(r["errors"]), limits_exceeded=r["limits_exceeded"][:50],
                corrected_flow_warnings=len(r["warnings"]))
    fail = bool(r["limits_exceeded"]) or not r["design_reproduced"]
    return AnalysisResult(
        m, "fail" if fail else "pass",
        f"{len(r['points'])} points, thrust up to {r['summary']['max_thrust_n'] / 1000:.1f} kN, best TSFC {r['summary']['min_tsfc_g_per_kn_s']:.2f} g/(kN s)"
        + (f"; {len(r['limits_exceeded'])} point(s) exceed your limits" if r["limits_exceeded"] else ""),
        outs,
        ["Fixed-geometry engine matched to the design point; turbines choked (constant temperature and pressure ratios)",
         "Component efficiencies and bypass ratio held at design values (no component maps)",
         "Ideally expanded nozzles; no bleed, cooling flow or installation loss"],
        ["No surge margin, spool speed or transient prediction", "Points with fan_corrected_flow_ratio > 1.05 need maps to judge"],
        ["Limit exceeded at one or more points"] if fail else [], [], METHOD_VERSIONS[m])


def engine_meanline(cycle: dict, fan_diameter_mm=None) -> AnalysisResult:
    from aerospace.engine_cycle import CycleError, REQUIRED
    from aerospace.engine_meanline import meanline, check_geometry
    m = "engine_meanline"
    ml = cycle.get("meanline")
    if not isinstance(ml, dict):
        return _na(m, "cycle.meanline (axial Mach numbers, blade speeds, loading limits) not supplied")
    try:
        out = meanline({k: cycle.get(k) for k in REQUIRED}, ml)
    except (CycleError, TypeError, ValueError) as e:
        return _na(m, str(e))
    warns = check_geometry(out, fan_diameter_mm) if fan_diameter_mm else []
    return AnalysisResult(
        m, "pass", f"Fan {out['fan_tip_diameter_mm']:.0f} mm, stages: fan {out['fan_stages']}, HPC {out['hpc_stages']}, HPT {out['hpt_stages']}, LPT {out['lpt_stages']}",
        out, ["Mean-line only: Euler work with a loading limit per stage; no radial equilibrium or blade shapes",
              "Annulus sizing from mass-flow parameter at your chosen axial Mach numbers",
              "Aerodynamic choices (speeds, loading, Mach) are inputs you supplied"],
        ["Preliminary stage counts, not a blading design", "AN^2 is reported for information; compare with your material limits"],
        [], warns, METHOD_VERSIONS[m])


def aircraft_performance_result(params, cycle, flight) -> AnalysisResult:
    from aerospace.engine_cycle import CycleError, REQUIRED
    from aerospace.performance import aircraft_performance
    m = "aircraft_performance"
    try:
        out = aircraft_performance(params, {k: cycle.get(k) for k in REQUIRED}, flight)
    except (CycleError, TypeError, ValueError, KeyError) as e:
        return _na(m, f"{type(e).__name__}: {e}")
    cr = out["cruise"]
    ok = out.get("cruise_sustainable", False)
    flat = dict(out)
    flat["cruise"] = cr
    return AnalysisResult(
        m, "pass" if ok else "fail",
        (f"Cruise L/D {cr['l_over_d']}, range {out.get('range_km')} km, TSFC {out.get('cruise_tsfc_g_per_kn_s')}" if ok
         else "Engines cannot sustain the requested cruise condition"),
        flat,
        ["Drag build-up: flat-plate skin friction, form and interference factors, Oswald efficiency, Korn wave drag",
         "Engines from the off-design model (Tt4 throttle), thrust = drag in cruise, no installation losses",
         "Thrust available is limited so the fan never exceeds max_corrected_flow_ratio (default 1.0, the design corrected flow)",
         "Breguet range with mid-cruise L/D and TSFC; no climb/descent or reserves", "Smooth surfaces, no trim drag, no high-lift devices"],
        ["Conceptual accuracy; not validated against wind-tunnel or CFD data", "Mach 0.1..1.0, altitude 0..20 km",
         "Set airfoil_kappa to about 0.95 for supercritical sections (default 0.87 is conventional)"],
        [] if ok else ["Insufficient thrust for the cruise condition"], [], METHOD_VERSIONS[m])


def mass_rollup_result(components, params=None) -> AnalysisResult:
    from aerospace.mass_cg import rollup
    from aerospace.requirements import SpecError
    m = "mass_rollup"
    mac = None
    if params is not None:
        from aerospace.aircraft import mac_position
        mac = mac_position(params)
    try:
        out, blockers = rollup(components, *(mac or (None, None)))
    except SpecError as e:
        return _na(m, str(e))
    res = AnalysisResult(m, "pass", f"Mass {out['mass_kg']:.1f} kg" + (f", CG {out['cg_percent_mac']}% MAC" if "cg_percent_mac" in out else ""),
                         out, ["Mass is the sum of declared components, or thrust / (g x declared thrust-to-weight), never CAD volume",
                               "CG uses the x positions you declared"], ["Longitudinal CG only"], version=METHOD_VERSIONS[m])
    res.warnings = blockers
    pct = out.get("cg_percent_mac")
    if pct is not None and not 0 <= pct <= 100:
        res.warnings.append(f"CG at {pct}% MAC lies outside the wing mean chord: check the x positions of the components (nose is x = 0, aft positive)")
    return res


def stability_trim_result(params, st, rollup_cg_percent=None) -> AnalysisResult:
    from aerospace.engine_cycle import CycleError
    from aerospace.stability import analyze
    m = "stability_trim"
    try:
        r = analyze(params, st, rollup_cg_percent)
    except (CycleError, TypeError, ValueError, KeyError) as e:
        return _na(m, f"{type(e).__name__}: {e}" if not isinstance(e, CycleError) else str(e))
    outs = {k: v for k, v in r.items() if k != "issues"}
    return AnalysisResult(
        m, "fail" if r["issues"] else "pass",
        f"Static margin {r['static_margin_min_percent_mac']}..{r['static_margin_max_percent_mac']}% MAC, max trim tail CL {r['max_abs_trim_tail_cl']}",
        outs,
        ["Wing + horizontal tail neutral point with downwash gradient 2 CLalpha / (pi AR)",
         "Fuselage and nacelle effect is the forward shift you declared, not computed",
         "Wing pitching moment from thin-airfoil theory with a finite-wing correction; no twist, flap or fuselage moment",
         "Trim by horizontal-tail lift in steady level flight, tail efficiency as declared"],
        ["Longitudinal static stability only: no lateral-directional, dynamic modes, elevator sizing or control-system analysis",
         "Mach 0.1..1.0 subsonic lift slopes; no aeroelastic effects"],
        r["issues"], [], METHOD_VERSIONS[m])


def field_performance_result(params, cycle, field) -> AnalysisResult:
    from aerospace.engine_cycle import CycleError, REQUIRED
    from aerospace.fieldperf import analyze
    m = "field_performance"
    try:
        out = analyze(params, {k: cycle.get(k) for k in REQUIRED}, field)
    except (CycleError, TypeError, ValueError, KeyError) as e:
        return _na(m, str(e) if isinstance(e, CycleError) else f"{type(e).__name__}: {e}")
    warns = []
    if out.get("meets_second_segment_min") is False:
        warns.append(f"one-engine-out second-segment gradient {out['second_segment_gradient']} is below the commonly cited transport minimum {out['second_segment_min_far25']}; confirm the rule that applies to your aircraft")
    return AnalysisResult(
        m, "pass", f"Takeoff {out['takeoff_distance_aeo_m']:.0f} m to {10.668 / 0.3048:.0f} ft (all engines), landing {out['landing_distance_factored_m']:.0f} m factored",
        out,
        ["Ground roll and braking integrated against engine thrust at each speed (off-design model, corrected-flow limited)",
         "Circular-arc transition at load factor 1.2; speeds 1.1/1.15/1.2 Vs and approach 1.23 Vs unless overridden",
         "High-lift and gear drag increments, CL values and friction coefficients are the values you declared",
         "ISA, still air, level dry runway, no ground effect"],
        ["Not a balanced field length: no V1, no engine failure during the roll, no wet or contaminated runway",
         "x115 and /0.6 factors are screening multipliers, not a compliance finding"],
        [], warns, METHOD_VERSIONS[m])


def highlift_result(params, hl) -> AnalysisResult:
    from aerospace.engine_cycle import CycleError
    from aerospace.highlift import analyze
    m = "highlift"
    try:
        out = analyze(params, hl)
    except (CycleError, TypeError, ValueError, KeyError) as e:
        return _na(m, str(e) if isinstance(e, CycleError) else f"{type(e).__name__}: {e}")
    out["delta_cd0_inoperative_engine"] = float(hl["delta_cd0_engine_out"])
    return AnalysisResult(
        m, "pass", f"CLmax clean {out['cl_max_clean']}, takeoff {out['cl_max_takeoff']}, landing {out['cl_max_landing']}", out,
        ["Flap and slat maximum-lift increments from type, flapped area, chord ratio and hinge sweep (empirical, Raymer-style)",
         "Clean wing CLmax = 0.9 x airfoil cl_max x cos(quarter-chord sweep), with the airfoil cl_max you supplied",
         "Flap profile-drag increment from chord ratio, area and deflection; gear and engine-out drag are your inputs"],
        ["Typical accuracy +-20..30%: replace with wind-tunnel or CFD data when available",
         "No flap effect on pitching moment, lift-curve slope or stall behaviour"], [], [], METHOD_VERSIONS[m])


def dynamics_result(params, d, cg_rollup_percent=None) -> AnalysisResult:
    from aerospace.dynamics import analyze
    from aerospace.engine_cycle import CycleError
    m = "dynamics"
    try:
        r = analyze(params, d, cg_rollup_percent)
    except (CycleError, TypeError, ValueError, KeyError) as e:
        return _na(m, str(e) if isinstance(e, CycleError) else f"{type(e).__name__}: {e}")
    outs = {k: v for k, v in r.items() if k != "issues"}
    return AnalysisResult(
        m, "fail" if r["issues"] else "pass",
        f"Cn_beta min {r['min_cn_beta']}, Dutch-roll damping min {r['min_dutch_roll_zeta']}", outs,
        ["Linearised level-flight longitudinal and lateral models in stability axes",
         "Tail, wing roll damping and longitudinal derivatives estimated from geometry; fuselage yaw effect, dihedral effect and inertias declared",
         "Cn_p = -CL/8 and Cl_r = CL/4 (rough, +-50%); lift compressibility by Prandtl-Glauert only",
         "Any derivative can be overridden by a declared value; the report lists which were estimated"],
        ["No control surfaces, yaw damper, flexibility or nonlinear effects", "Handling-quality limits are the criteria you declared"],
        r["issues"], [], METHOD_VERSIONS[m])


def elevator_result(params, ele, cg_rollup_percent=None, rotation=None) -> AnalysisResult:
    from aerospace.control import analyze
    from aerospace.engine_cycle import CycleError
    m = "elevator_sizing"
    try:
        r = analyze(params, ele, cg_rollup_percent, rotation)
    except (CycleError, TypeError, ValueError, KeyError) as e:
        return _na(m, str(e) if isinstance(e, CycleError) else f"{type(e).__name__}: {e}")
    outs = {k: v for k, v in r.items() if k != "issues"}
    return AnalysisResult(
        m, "fail" if r["issues"] else "pass", f"Elevator effectiveness {r['effectiveness_tau']}, minimum deflection margin {r['min_elevator_margin_deg']} deg", outs,
        ["Thin-airfoil flap effectiveness with your viscous factor and span fraction",
         "Tail angle of attack from wing angle of attack, downwash and your stabilizer incidence",
         "Rotation acceleration is about the CG and ignores gear reaction moment and ground effect (optimistic)"],
        ["No hinge moments, control forces, trim-tab or stabilizer actuator sizing", "Steady level-flight trim only"],
        r["issues"], [], METHOD_VERSIONS[m])
