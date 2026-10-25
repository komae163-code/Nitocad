"""Release gate. The strongest status is 'ready_for_engineering_review'.

That means NitoCAD's own checks are complete and consistent. It is NOT
airworthiness approval, certification, or a flight-readiness statement, and
no code path produces one.
"""
from dataclasses import dataclass, field

from aerospace import analysis as an
from aerospace import certification as cert, fea_evidence, test_evidence
from aerospace.evidence import (
    external_evidence_status, record_from_analysis, revision_id,
)
from aerospace.materials import MaterialError, resolve_material
from aerospace.requirements import FIRM, Requirement, Sourced, SpecError, State
from aerospace_pack import GEOMETRY_ONLY_NOTICE

PART_TYPES = ("airfoil_wing", "wing_rib", "fuselage_frame", "wing_spar", "attachment_lug",
              "fuselage", "tail_surface", "turbofan_engine", "jet_aircraft")
# Outer-mold-line concept geometry: no structural material, mass must be declared.
OML = ("fuselage", "tail_surface", "turbofan_engine", "jet_aircraft")

# Screening hand-calcs never suffice: each part needs external structural
# substantiation (FEA and/or test) bound to the current revision.
REQUIRED_EVIDENCE = {
    "airfoil_wing": ("fea",), "wing_rib": ("fea",), "fuselage_frame": ("fea",),
    "wing_spar": ("fea",), "attachment_lug": ("fea",),
    "fuselage": ("fea",), "tail_surface": ("fea",),
    "turbofan_engine": ("fea", "test"), "jet_aircraft": ("fea", "test"),
}

# Methods that apply to each part, and the load input each one needs.
APPLICABLE = {
    "airfoil_wing": ("wing_root_bending", "mass"),
    "wing_rib": ("mass",),
    "fuselage_frame": ("frame_diametral_ring", "mass"),
    "wing_spar": ("spar_bending", "mass"),
    "attachment_lug": ("lug_check", "mass"),
    "fuselage": ("mass_declared",), "tail_surface": ("mass_declared",),
    "turbofan_engine": ("engine_cycle", "mass_declared"), "jet_aircraft": ("aircraft_metrics", "mass_declared"),
}


def _firm(s: Sourced, what, blockers):
    if s.state not in FIRM:
        blockers.append(f"{what} is '{s.state.value}', must be confirmed or verified")


def verify_external_evidence(spec, rev, mat=None, fs_value=None, screening_sigma=None):
    """Classify each external evidence item: current (bound to this revision), and accepted.

    Structured FEA and test evidence are evaluated by NitoCAD against your declared
    acceptance criteria. Anything else is a manual attestation and needs a tool,
    tool version, report reference and a named reviewer. Returns (list, blockers)."""
    rows, blockers = [], []
    for ev in spec.get("evidence") or []:
        st = dict(external_evidence_status(ev, rev, spec), kind=ev.get("kind"), accepted=False, issues=[], verification="manual attestation")
        struct = ev.get("structured")
        if ev.get("kind") == "fea" and isinstance(struct, dict):
            st["verification"] = "structured FEA verified by NitoCAD"
            if mat is None:
                st["issues"] = ["structured FEA evidence needs a design material"]
            else:
                v = fea_evidence.evaluate(dict(struct, id=ev.get("id")), spec.get("fea_acceptance") or {},
                                          {"e_mpa": mat.e_mpa, "nu": mat.nu, "ftu_mpa": mat.ftu_mpa, "fty_mpa": mat.fty_mpa},
                                          fs_value, screening_sigma, rev)
                st["accepted"], st["issues"], st["numbers"] = v["verdict"] == "accepted", v.get("issues", []), v.get("numbers", {})
                st["checks"] = v.get("checks", [])
        elif ev.get("kind") == "test" and isinstance(struct, dict):
            st["verification"] = "structured test checked by NitoCAD"
            v = test_evidence.evaluate(dict(struct, id=ev.get("id")), rev)
            st["accepted"], st["issues"], st["criteria"] = v["verdict"] == "accepted", v.get("issues", []), v.get("criteria", [])
        else:
            miss = [k for k in ("tool", "tool_version", "report_ref", "reviewer") if not str(ev.get(k, "")).strip()]
            st["issues"] = ([f"manual evidence needs: {', '.join(miss)}"] if miss else []) + (["evidence reports a failure"] if ev.get("result") == "fail" else [])
            st["accepted"] = not st["issues"]
        if st["issues"]:
            blockers.append(f"external evidence {ev.get('id')}: " + "; ".join(st["issues"]))
        rows.append(st)
    return rows, blockers


def assess_design(spec: dict, created_by: str = "", approvals=None) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("spec must be an object")
    part = spec.get("part_type")
    if part not in PART_TYPES:
        raise SpecError(f"part_type must be one of {', '.join(PART_TYPES)}")
    params = spec.get("params") or {}
    design_id = str(spec.get("design_id") or "unnamed")
    mat = None
    if not (part in OML and not spec.get("material")):
        try:
            mat = resolve_material(spec.get("material") or {})
        except MaterialError as e:
            raise SpecError(f"material: {e}")
    material_ref = spec.get("material") or {}
    rev = revision_id(part, params, material_ref)

    # ---- inputs (each parsed with provenance; missing stays missing)
    gaps, blockers = [], []
    factors = spec.get("factors") or {}
    fs = Sourced.parse(factors["ultimate"], "factors.ultimate") if "ultimate" in factors else None
    if fs is None and part not in OML:
        gaps.append("factors.ultimate: ultimate factor of safety not supplied (not defaulted; e.g. 1.5 is common but must be confirmed against the applicable standard)")
    elif fs is not None:
        _firm(fs, "factors.ultimate", blockers)
    loads = spec.get("loads") or {}
    parsed_loads = {}
    for key in ("wing_limit_lift", "frame_diametral", "spar_tip_load", "lug_axial"):
        if key in loads:
            parsed_loads[key] = Sourced.parse(loads[key], f"loads.{key}")
            _firm(parsed_loads[key], f"loads.{key}", blockers)

    fsv = fs.value if fs else None
    results = []
    if part == "airfoil_wing":
        L = parsed_loads.get("wing_limit_lift")
        if L is None:
            gaps.append("loads.wing_limit_lift: semi-span limit lift (N) not supplied")
        results.append(an.wing_root_bending(params, mat, L.value if L else None,
                                            loads.get("wing_limit_lift", {}).get("distribution", "elliptic"), fsv))
    elif part == "fuselage_frame":
        L = parsed_loads.get("frame_diametral")
        if L is None:
            gaps.append("loads.frame_diametral: limit diametral load (N) not supplied")
        results.append(an.frame_diametral_ring(params, mat, L.value if L else None, fsv,
                                               params.get("stress_concentration_kt")))
    elif part == "wing_spar":
        L = parsed_loads.get("spar_tip_load")
        if L is None:
            gaps.append("loads.spar_tip_load: limit tip load (N) not supplied")
        results.append(an.spar_bending(params, mat, L.value if L else None, fsv))
    elif part == "attachment_lug":
        L = parsed_loads.get("lug_axial")
        if L is None:
            gaps.append("loads.lug_axial: limit axial load (N) not supplied")
        results.append(an.lug_check(params, mat, L.value if L else None, fsv, params.get("stress_concentration_kt")))
    elif part == "turbofan_engine":
        cyc = spec.get("cycle")
        if not isinstance(cyc, dict):
            gaps.append("cycle: design-point cycle inputs not supplied (flight condition, bpr, pressure ratios, tt4, efficiencies)")
            results.append(an.engine_cycle({}))
        else:
            if not str(cyc.get("source", "")).strip():
                blockers.append("cycle: a source is required for the cycle inputs")
            if str(cyc.get("state", "draft")) not in ("confirmed", "verified"):
                blockers.append(f"cycle inputs are '{cyc.get('state', 'draft')}', must be confirmed or verified")
            results.append(an.engine_cycle(cyc))
            if "offdesign" in cyc:
                results.append(an.engine_offdesign(cyc))
            else:
                gaps.append("cycle.offdesign: throttle/altitude/Mach sweep not supplied")
            if "meanline" in cyc:
                results.append(an.engine_meanline(cyc, params.get("fan_diameter_mm")))
            else:
                gaps.append("cycle.meanline: compressor/turbine stage sizing inputs not supplied")
    elif part == "jet_aircraft":
        w = spec.get("weights") or {}
        wt = {}
        for k in ("mtow_kg", "static_thrust_total_n"):
            if k in w:
                sv = Sourced.parse(w[k], f"weights.{k}")
                _firm(sv, f"weights.{k}", blockers)
                wt[k] = sv.value
        results.append(an.aircraft_metrics_result(params, wt.get("mtow_kg"), wt.get("static_thrust_total_n")))
        cyc, fl = spec.get("cycle"), spec.get("flight")
        if isinstance(cyc, dict) and isinstance(fl, dict):
            for nm, blk in (("cycle", cyc), ("flight", fl)):
                if not str(blk.get("source", "")).strip():
                    blockers.append(f"{nm}: a source is required")
                if str(blk.get("state", "draft")) not in ("confirmed", "verified"):
                    blockers.append(f"{nm} inputs are '{blk.get('state', 'draft')}', must be confirmed or verified")
            results.append(an.aircraft_performance_result(params, cyc, fl))
        else:
            gaps.append("cycle and flight: engine cycle and flight conditions are needed for aerodynamics and performance")
    if spec.get("attachment"):
        results.append(an.bearing(spec["attachment"], mat, fsv))
    if part in OML:
        if spec.get("mass_components"):
            rr = an.mass_rollup_result(spec["mass_components"], params if part == "jet_aircraft" else None)
            results.append(rr)
            blockers.extend(w for w in rr.warnings if "must be confirmed" in w)
            rr.warnings = [w for w in rr.warnings if "must be confirmed" not in w]
        mp = spec.get("mass_properties")
        declared = None
        if mp is not None:
            sv = Sourced.parse(mp, "mass_properties")
            _firm(sv, "mass_properties", blockers)
            declared = sv.value
        if not spec.get("mass_components"):
            results.append(an.mass_declared(declared))
    else:
        results.append(an.mass(part, params, mat, (spec.get("geometry") or {}).get("volume_mm3")))

    if part == "jet_aircraft":
        cg_nominal = None
        for r in results:
            if r.method == "mass_rollup" and "cg_percent_mac" in r.outputs:
                cg_nominal = r.outputs["cg_percent_mac"]
        hl_derived = {}
        hl = spec.get("highlift")
        if isinstance(hl, dict):
            for chk, msg in ((not str(hl.get("source", "")).strip(), "highlift: a source is required"),
                             (str(hl.get("state", "draft")) not in ("confirmed", "verified"), f"highlift inputs are '{hl.get('state', 'draft')}', must be confirmed or verified")):
                if chk:
                    blockers.append(msg)
            rhl = an.highlift_result(params, hl)
            results.append(rhl)
            if rhl.status == "pass":
                hl_derived = {k: rhl.outputs[k] for k in ("cl_max_takeoff", "cl_max_landing", "delta_cd0_takeoff", "delta_cd0_landing",
                                                          "delta_cd0_oei", "delta_cd0_inoperative_engine")}
        else:
            gaps.append("highlift: flap and slat configuration not supplied (or declare cl_max and drag increments directly in the field block)")
        for key, label in (("stability", "stability: loading cases and tail inputs not supplied"),
                           ("field", "field: takeoff and landing inputs not supplied")):
            blk = spec.get(key)
            if not isinstance(blk, dict):
                gaps.append(label)
                continue
            if not str(blk.get("source", "")).strip():
                blockers.append(f"{key}: a source is required")
            if str(blk.get("state", "draft")) not in ("confirmed", "verified"):
                blockers.append(f"{key} inputs are '{blk.get('state', 'draft')}', must be confirmed or verified")
            if key == "stability":
                results.append(an.stability_trim_result(params, blk, cg_nominal))
            elif isinstance(spec.get("cycle"), dict):
                results.append(an.field_performance_result(params, spec["cycle"], {**hl_derived, **blk}))
            else:
                gaps.append("cycle: engine cycle needed for takeoff and landing performance")
        dyn = spec.get("dynamics")
        if isinstance(dyn, dict):
            if not str(dyn.get("source", "")).strip():
                blockers.append("dynamics: a source is required")
            if str(dyn.get("state", "draft")) not in ("confirmed", "verified"):
                blockers.append(f"dynamics inputs are '{dyn.get('state', 'draft')}', must be confirmed or verified")
            results.append(an.dynamics_result(params, dyn, cg_nominal))
        else:
            gaps.append("dynamics: lateral-directional and dynamic-mode inputs (inertias, derivative choices, criteria) not supplied")
        ele = spec.get("elevator")
        if isinstance(ele, dict):
            if not str(ele.get("source", "")).strip():
                blockers.append("elevator: a source is required")
            if str(ele.get("state", "draft")) not in ("confirmed", "verified"):
                blockers.append(f"elevator inputs are '{ele.get('state', 'draft')}', must be confirmed or verified")
            rot = None
            fres = next((r for r in results if r.method == "field_performance" and r.status == "pass"), None)
            iy = ((spec.get("dynamics") or {}).get("inertia") or {}).get("iy")
            if fres and iy and ele.get("rotation_accel_min_deg_s2") is not None and isinstance(spec.get("field"), dict):
                rot = {"v_r_m_s": fres.outputs["takeoff_v_lof_m_s"], "mass_kg": spec["field"].get("takeoff_mass_kg"), "iy_kgm2": iy,
                       "altitude_m": spec["field"].get("altitude_m"), "accel_min_deg_s2": ele["rotation_accel_min_deg_s2"]}
            results.append(an.elevator_result(params, ele, cg_nominal, rot))
        else:
            gaps.append("elevator: elevator geometry, deflection limits and loading cases not supplied")

    for r in results:
        if r.status == "not_evaluable":
            blockers.append(f"{r.method}: {r.reasons[0]}")
        elif r.status == "fail":
            blockers.append(f"{r.method}: " + ("; ".join(r.reasons) if r.reasons else "failed"))
    if mat is not None and not mat.is_design_allowable:
        blockers.append(f"material {mat.id} uses '{mat.basis}' properties, not design allowables")

    # ---- requirements
    outputs = {}
    for r in results:
        outputs.update(r.outputs)
    ms_vals = [r.outputs["ms_min"] for r in results if "ms_min" in r.outputs]
    if ms_vals:
        outputs["ms_min"] = min(ms_vals)
    req_out = []
    for d in spec.get("requirements") or []:
        rq = Requirement.parse(d)
        verdict = rq.evaluate(outputs)
        req_out.append({"id": rq.id, "state": rq.state.value, "verdict": verdict, "text": rq.text})
        if rq.state not in FIRM:
            blockers.append(f"requirement {rq.id} is '{rq.state.value}'")
        if verdict != "pass":
            blockers.append(f"requirement {rq.id}: verdict {verdict}")
        elif rq.state is State.CONFIRMED:
            pass  # passes analysis; owner can promote to verified once evidence is accepted

    # ---- external evidence
    screening = next((r.outputs["sigma_limit_mpa"] for r in results if "sigma_limit_mpa" in r.outputs), None)
    ext, ev_blockers = verify_external_evidence(spec, rev, mat, fsv, screening)
    blockers.extend(ev_blockers)
    for need in REQUIRED_EVIDENCE[part]:
        if not any(e["kind"] == need and e["current"] and e["accepted"] for e in ext):
            blockers.append(f"no current, accepted '{need}' evidence for this revision (screening hand-calcs are not substantiation)")

    # ---- status
    any_eval = any(r.status != "not_evaluable" for r in results if r.method not in ("mass", "mass_declared", "mass_rollup"))
    if blockers or gaps:
        if any(r.status == "fail" for r in results):
            status = "analysis_failed"
        elif any_eval:
            status = "analysis_incomplete"
        else:
            status = "geometry_only"
    else:
        status = "ready_for_engineering_review"

    records = [record_from_analysis(design_id, rev, spec, r, created_by).to_dict()
               for r in results if r.status != "not_evaluable"]
    result = {
        "design_id": design_id, "part_type": part, "revision_id": rev, "status": status,
        "notice": GEOMETRY_ONLY_NOTICE,
        "gaps": gaps, "blockers": sorted(set(blockers)),
        "results": [r.to_dict() for r in results],
        "requirements": req_out, "external_evidence": ext,
        "material": None if mat is None else {"id": mat.id, "basis": mat.basis, "source": mat.source,
                                              "design_allowable": mat.is_design_allowable},
        "evidence_records": records,
    }
    if isinstance(spec.get("certification"), dict):
        result["certification"] = cert.current_stage(approvals or [], result, spec["certification"])
    return result
