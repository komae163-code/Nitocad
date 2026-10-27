"""Assemblies and interfaces (spec-driven, not extracted from CAD).

Interface feature data (hole sizes, edge distances, fastener data) is entered
by the engineer and carries a source/state. Joint rules (minimum edge ratio,
fit clearance, minimum pitch) must be supplied with a citation to the
controlling standard: nothing here invents design rules.
"""
import math

from aerospace import analysis as an
from aerospace.evidence import EvidenceRecord, digest, external_evidence_status, change_impact
from aerospace.materials import resolve_material
from aerospace.release import assess_design, verify_external_evidence
from aerospace.requirements import FIRM, Requirement, Sourced, SpecError
from aerospace_pack import GEOMETRY_ONLY_NOTICE

METHOD = "interface_check"
METHOD_VERSION = "1.0"

# Screening pairs that need isolation (sealant, isolating washer, plating). Not exhaustive.
_CAUTION = {frozenset(p) for p in (
    ("aluminium", "titanium"), ("aluminium", "carbon_steel"), ("aluminium", "stainless_steel"),
    ("aluminium", "cfrp"), ("magnesium", "aluminium"), ("magnesium", "titanium"),
    ("magnesium", "carbon_steel"), ("magnesium", "stainless_steel"), ("magnesium", "cfrp"),
    ("carbon_steel", "cfrp"), ("titanium", "cfrp"),
)}


def _num(d, k, what):
    try:
        v = float(d[k])
    except (KeyError, TypeError, ValueError):
        raise SpecError(f"{what}: {k} is required and must be a number")
    if v <= 0:
        raise SpecError(f"{what}: {k} must be positive")
    return v


def _check_interface(itf, parts, fs):
    iid = str(itf.get("id", "?"))
    blockers, gaps, assumptions = [], [], []
    out = {"id": iid, "kind": itf.get("kind", "pinned_or_bolted"), "checks": {}}
    for side in ("a", "b"):
        if not isinstance(itf.get(side), dict) or itf[side].get("part") not in parts:
            raise SpecError(f"interface {iid}: side {side} must name a part that exists in parts")
    if itf["a"]["part"] == itf["b"]["part"]:
        raise SpecError(f"interface {iid}: both sides name the same part")
    rules = itf.get("rules") or {}
    if not str(rules.get("source", "")).strip() or "min_edge_ratio" not in rules or "max_clearance_mm" not in rules:
        gaps.append(f"interface {iid}: rules.{{min_edge_ratio, max_clearance_mm, source}} not supplied (cite the controlling standard; not defaulted)")
        rules = {}
    fas = itf.get("fastener") or {}
    fd = _num(fas, "diameter_mm", f"interface {iid} fastener")
    count = int(_num(fas, "count", f"interface {iid} fastener"))
    planes = int(_num(fas, "shear_planes", f"interface {iid} fastener"))
    load = Sourced.parse(itf.get("limit_load"), f"interface {iid} limit_load")
    if load.state not in FIRM:
        blockers.append(f"interface {iid}: limit_load is '{load.state.value}', must be confirmed or verified")
    per_fastener = load.value / count

    # 1. geometry
    margins, mats = [], {}
    for side in ("a", "b"):
        s = itf[side]
        pname = s["part"]
        mat = resolve_material(parts[pname].get("material") or {})
        mats[side] = mat
        hole, t = _num(s, "hole_diameter_mm", f"interface {iid}.{side}"), _num(s, "thickness_mm", f"interface {iid}.{side}")
        edge = _num(s, "edge_distance_mm", f"interface {iid}.{side}")
        if hole < fd:
            blockers.append(f"interface {iid}.{side}: hole {hole} mm is smaller than fastener {fd} mm")
        elif rules and hole - fd > rules["max_clearance_mm"]:
            blockers.append(f"interface {iid}.{side}: clearance {hole - fd:.3f} mm exceeds rule {rules['max_clearance_mm']} mm")
        if rules and edge / hole < rules["min_edge_ratio"]:
            blockers.append(f"interface {iid}.{side}: e/D {edge / hole:.2f} below rule {rules['min_edge_ratio']}")
        if count > 1:
            pitch = s.get("pitch_mm")
            if rules and "min_pitch_ratio" not in rules:
                gaps.append(f"interface {iid}: rules.min_pitch_ratio required for multi-fastener joints")
            elif rules and (pitch is None or pitch / hole < rules["min_pitch_ratio"]):
                blockers.append(f"interface {iid}.{side}: pitch missing or below rule {rules['min_pitch_ratio']}D")
        # 2. bearing on each mating part (full per-fastener load: conservative for double shear)
        r = an.bearing({"limit_load_n": per_fastener,
                        "hole_diameter_mm": hole, "thickness_mm": t}, mat, fs)
        out["checks"][f"bearing_{side}"] = r.to_dict()
        if r.status == "not_evaluable":
            blockers.append(f"interface {iid}.{side}: {r.reasons[0]}")
        elif r.status == "fail":
            blockers.append(f"interface {iid}.{side}: negative bearing margin")
        else:
            margins.append(r.outputs["ms_bearing"])
        if not mat.is_design_allowable:
            blockers.append(f"interface {iid}.{side}: material {mat.id} is not design-basis")

    # 3. fastener shear
    if fs is None:
        blockers.append(f"interface {iid}: ultimate factor of safety not supplied")
    else:
        try:
            allow = Sourced.parse(fas.get("shear_allowable_n"), f"interface {iid} fastener.shear_allowable_n")
        except SpecError as e:
            allow = None
            gaps.append(str(e))
        if allow:
            if allow.state not in FIRM:
                blockers.append(f"interface {iid}: fastener shear allowable is '{allow.state.value}'")
            ms = allow.value / (fs * per_fastener / planes) - 1
            out["checks"]["fastener_shear"] = {"ms": round(ms, 4), "load_per_plane_n": round(per_fastener / planes, 2)}
            if ms < 0:
                blockers.append(f"interface {iid}: negative fastener shear margin")
            else:
                margins.append(round(ms, 4))

    # 4. galvanic compatibility (unknown stays unknown)
    groups = [mats["a"].galvanic_group, mats["b"].galvanic_group, fas.get("galvanic_group")]
    if any(g is None for g in groups):
        gaps.append(f"interface {iid}: galvanic_group missing for a material or the fastener")
    else:
        bad = sorted({tuple(sorted(p)) for p in ((groups[0], groups[1]), (groups[0], groups[2]), (groups[1], groups[2]))
                      if frozenset(p) in _CAUTION})
        out["checks"]["galvanic"] = {"groups": groups, "caution_pairs": [list(b) for b in bad]}
        if bad and not itf.get("isolation"):
            blockers.append(f"interface {iid}: galvanic couple {bad} needs isolation (declare isolation:true once specified)")

    out["ms_min"] = min(margins) if margins else None
    out["blockers"], out["gaps"] = blockers, gaps
    out["status"] = "fail" if any("negative" in b for b in blockers) else ("incomplete" if blockers or gaps else "pass")
    return out


def assess_assembly(spec: dict, created_by: str = "") -> dict:
    if not isinstance(spec, dict) or not isinstance(spec.get("parts"), dict) or not spec["parts"]:
        raise SpecError("spec.parts must be an object of part specs")
    if len(spec["parts"]) > 50 or len(spec.get("interfaces") or []) > 200:
        raise SpecError("assembly too large for this endpoint (max 50 parts, 200 interfaces)")
    asm_id = str(spec.get("design_id") or "assembly")
    parts, part_out, blockers, gaps, records = spec["parts"], {}, [], [], []
    masses = {}
    for key, ps in parts.items():
        ps = dict(ps, design_id=f"{asm_id}:{key}")
        a = assess_design(ps, created_by)
        part_out[key] = {"status": a["status"], "revision_id": a["revision_id"], "blockers": len(a["blockers"])}
        if a["status"] != "ready_for_engineering_review":
            blockers.append(f"part {key} is '{a['status']}'")
        for r in a["results"]:
            if r["method"] == "mass" and r["status"] == "pass":
                masses[key] = r["outputs"]["mass_kg"]
        for rec in a["evidence_records"]:
            rec = dict(rec, design_id=asm_id)
            rec["outputs"] = dict(rec["outputs"], part=key)
            records.append(rec)

    factors = spec.get("factors") or {}
    fs = None
    if "ultimate" in factors:
        f = Sourced.parse(factors["ultimate"], "factors.ultimate")
        fs = f.value
        if f.state not in FIRM:
            blockers.append("factors.ultimate must be confirmed or verified")
    else:
        gaps.append("factors.ultimate not supplied")

    rev = digest({"parts": {k: v["revision_id"] for k, v in part_out.items()},
                  "interfaces": spec.get("interfaces") or []})
    itf_out = []
    for itf in spec.get("interfaces") or []:
        r = _check_interface(itf, parts, fs)
        itf_out.append(r)
        blockers += r["blockers"]
        gaps += r["gaps"]
        if r["status"] != "incomplete" or r["ms_min"] is not None:
            records.append(EvidenceRecord(
                asm_id, rev, "analysis", METHOD, METHOD_VERSION,
                "pass" if r["status"] == "pass" else "fail" if r["status"] == "fail" else "not_evaluable",
                digest({"interface": itf, "parts": {k: v["revision_id"] for k, v in part_out.items()}}),
                {"interface": r["id"], "ms_min": r["ms_min"]}, ["Spec-driven interface features (not from CAD)"],
                created_by=created_by).to_dict())

    outputs = {}
    if len(masses) == len(parts):
        outputs["mass_kg"] = round(sum(masses.values()), 5)
    ms = [i["ms_min"] for i in itf_out if i["ms_min"] is not None]
    if ms:
        outputs["ms_min"] = min(ms)
    reqs = []
    for d in spec.get("requirements") or []:
        rq = Requirement.parse(d)
        v = rq.evaluate(outputs)
        reqs.append({"id": rq.id, "state": rq.state.value, "verdict": v, "text": rq.text})
        if rq.state not in FIRM or v != "pass":
            blockers.append(f"requirement {rq.id}: state '{rq.state.value}', verdict {v}")

    ext, ev_blockers = verify_external_evidence(spec, rev)
    blockers.extend(ev_blockers)
    if not any(e["kind"] in ("fea", "test") and e["current"] and e["accepted"] for e in ext):
        blockers.append("no current assembly-level 'fea' or 'test' evidence for this assembly revision")
    if not itf_out:
        gaps.append("no interfaces defined: the load path between parts is unverified")

    if blockers or gaps:
        status = "analysis_failed" if any("negative" in b for b in blockers) else (
            "analysis_incomplete" if ms else "geometry_only")
    else:
        status = "ready_for_engineering_review"
    return {"design_id": asm_id, "part_type": "assembly", "revision_id": rev, "status": status,
            "notice": GEOMETRY_ONLY_NOTICE, "gaps": sorted(set(gaps)), "blockers": sorted(set(blockers)),
            "parts": part_out, "interfaces": itf_out, "outputs": outputs, "requirements": reqs,
            "external_evidence": ext, "evidence_records": records}


def assembly_change_impact(old: dict, new: dict) -> dict:
    """Which parts changed revision, which interfaces and part evidence are affected."""
    changed_parts, part_impacts = [], {}
    for k in sorted(set(old.get("parts", {})) | set(new.get("parts", {}))):
        o, n = old.get("parts", {}).get(k), new.get("parts", {}).get(k)
        if o is None or n is None:
            changed_parts.append(k)
            part_impacts[k] = {"added_or_removed": True}
            continue
        imp = change_impact(o, n)
        if imp["revision_changed"]:
            changed_parts.append(k)
            part_impacts[k] = {"changed_fields": imp["changed_fields"], "analyses_to_rerun": imp["analyses_to_rerun"]}
    old_i = {i.get("id"): i for i in old.get("interfaces") or []}
    new_i = {i.get("id"): i for i in new.get("interfaces") or []}
    hit = []
    for iid in sorted(set(old_i) | set(new_i), key=str):
        i = new_i.get(iid) or old_i.get(iid)
        sides = {i.get("a", {}).get("part"), i.get("b", {}).get("part")}
        if old_i.get(iid) != new_i.get(iid) or sides & set(changed_parts):
            hit.append({"interface": iid, "because": "definition changed" if old_i.get(iid) != new_i.get(iid) else "a mating part changed"})
    return {"changed_parts": changed_parts, "part_impacts": part_impacts, "interfaces_to_recheck": hit,
            "assembly_evidence_stale": bool(changed_parts or hit)}
