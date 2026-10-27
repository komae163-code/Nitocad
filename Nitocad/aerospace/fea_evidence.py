"""Verification of FEA results produced in YOUR solver (stdlib only).

NitoCAD does not run FEA here. This module decides whether FEA evidence you
bring can count: mesh convergence with observed order and grid convergence
index (Roache / Celik), force equilibrium from reactions, material card match,
element quality, a margin of safety computed by NitoCAD from the extrapolated
stress, and a cross-check against the closed-form screening stress. Acceptance
limits come from YOUR declared fea_acceptance block (with a source); none are
defaulted.
"""
import math

from aerospace.requirements import FIRM, SpecError, State

ACCEPT_REQUIRED = ("reaction_tolerance", "gci_max_percent", "min_jacobian", "max_aspect_ratio", "screening_ratio_min", "screening_ratio_max")
EVIDENCE_REQUIRED = ("id", "solver", "solver_version", "model_hash", "load_level", "material", "meshes", "equilibrium", "element_quality")


def observed_order(f1, f2, f3, r21, r32):
    """Celik et al. (2008) fixed-point solution for the observed order p. f1 = finest."""
    e32, e21 = f3 - f2, f2 - f1
    if e21 == 0 or e32 == 0:
        return None
    s = math.copysign(1.0, e32 / e21)
    p = abs(math.log(abs(e32 / e21))) / math.log(r21)
    for _ in range(100):
        q = math.log((r21 ** p - s) / (r32 ** p - s)) if (r21 ** p - s) > 0 and (r32 ** p - s) > 0 else float("nan")
        new = abs(math.log(abs(e32 / e21)) + q) / math.log(r21)
        if not math.isfinite(new):
            return None
        if abs(new - p) < 1e-10:
            p = new
            break
        p = new
    return p, s


def convergence(meshes, key="peak_stress_mpa"):
    ms = sorted(meshes, key=lambda m: -float(m["element_size_mm"]))      # coarse -> fine
    if len(ms) < 3:
        return {"ok": False, "reason": "at least three systematically refined meshes are required"}
    f3, f2, f1 = (float(ms[-3 + i][key]) for i in range(3))
    h3, h2, h1 = (float(ms[-3 + i]["element_size_mm"]) for i in range(3))
    r21, r32 = h2 / h1, h3 / h2
    if r21 < 1.1 or r32 < 1.1:
        return {"ok": False, "reason": f"refinement ratios {r32:.2f}, {r21:.2f} are below 1.1 (not systematically refined)"}
    res = observed_order(f1, f2, f3, r21, r32)
    if res is None:
        return {"ok": False, "reason": "results are identical or the order of convergence cannot be determined"}
    p, s = res
    if s < 0:
        return {"ok": False, "reason": "oscillatory convergence: results do not converge monotonically", "p": round(p, 3)}
    if (f2 - f1) / (f3 - f2) < 0:
        return {"ok": False, "reason": "peak result is not converging monotonically"}
    if not 0.5 <= p <= 4.0:
        return {"ok": False, "reason": f"observed order of convergence p = {p:.2f} is outside 0.5..4 (possible stress singularity or noise)", "p": round(p, 3)}
    f_ext = f1 + (f1 - f2) / (r21 ** p - 1)
    gci = 1.25 * abs((f1 - f2) / f1) / (r21 ** p - 1) * 100
    return {"ok": True, "p": round(p, 3), "extrapolated": f_ext, "gci_fine_percent": round(gci, 3), "fine": f1}


def equilibrium_residual(eq):
    a, r = eq["applied_n"], eq["reactions_n"]
    if len(a) != len(r) or not a:
        raise SpecError("equilibrium applied_n and reactions_n must be equal-length force vectors")
    resid = math.sqrt(sum((x + y) ** 2 for x, y in zip(a, r)))
    ref = math.sqrt(sum(x * x for x in a))
    if ref == 0:
        raise SpecError("applied load is zero")
    return resid / ref


def evaluate(ev: dict, accept: dict, material: dict, fs_ult, screening_stress_limit=None, revision_id=None) -> dict:
    """Returns {'verdict': 'accepted'|'rejected'|'incomplete', 'checks': [...], numbers...}."""
    checks, nums = [], {}

    def chk(name, ok, detail=""):
        checks.append({"check": name, "result": "pass" if ok else "fail", "detail": detail})
        return ok

    miss = [k for k in EVIDENCE_REQUIRED if ev.get(k) in (None, "", [])]
    if miss:
        return {"verdict": "incomplete", "checks": checks, "issues": ["missing: " + ", ".join(miss)]}
    am = [k for k in ACCEPT_REQUIRED if accept.get(k) is None]
    if am or not str(accept.get("source", "")).strip() or State(accept.get("state", "draft")) not in FIRM:
        return {"verdict": "incomplete", "checks": checks,
                "issues": ["fea_acceptance must be confirmed, have a source and define: " + ", ".join(ACCEPT_REQUIRED)]
                if not am else ["fea_acceptance needs a source and a confirmed/verified state"]}
    if ev["load_level"] not in ("limit", "ultimate"):
        return {"verdict": "incomplete", "checks": checks, "issues": ["load_level must be 'limit' or 'ultimate'"]}
    if revision_id and ev.get("revision_id") not in (None, revision_id):
        chk("model bound to this revision", False, "revision_id differs")

    conv = convergence(ev["meshes"])
    nums["convergence"] = {k: v for k, v in conv.items() if k != "ok"}
    chk("mesh convergence (3+ meshes, monotone, sensible order)", conv["ok"], conv.get("reason", f"p = {conv.get('p')}"))
    if conv["ok"]:
        chk("grid convergence index within limit", conv["gci_fine_percent"] <= float(accept["gci_max_percent"]),
            f"GCI {conv['gci_fine_percent']}% vs {accept['gci_max_percent']}%")
    try:
        res = equilibrium_residual(ev["equilibrium"])
        nums["equilibrium_residual"] = round(res, 6)
        chk("reaction forces balance applied loads", res <= float(accept["reaction_tolerance"]), f"{res:.4%} vs {float(accept['reaction_tolerance']):.4%}")
    except SpecError as e:
        chk("reaction forces balance applied loads", False, str(e))
    mc = ev["material"]
    chk("material card matches the design material",
        abs(float(mc.get("e_mpa", 0)) - material["e_mpa"]) <= 0.005 * material["e_mpa"] and abs(float(mc.get("nu", 0)) - material.get("nu", 0.33)) <= 0.01,
        f"E {mc.get('e_mpa')} vs {material['e_mpa']}, nu {mc.get('nu')} vs {material.get('nu', 0.33)}")
    q = ev["element_quality"]
    chk("element quality", float(q.get("min_jacobian", 0)) >= float(accept["min_jacobian"]) and float(q.get("max_aspect_ratio", 1e9)) <= float(accept["max_aspect_ratio"])
        and int(q.get("failed_elements", 1)) == 0, f"min Jacobian {q.get('min_jacobian')}, max aspect {q.get('max_aspect_ratio')}, failed {q.get('failed_elements')}")

    if conv["ok"]:
        sigma = conv["extrapolated"] / (fs_ult if ev["load_level"] == "ultimate" and fs_ult else 1.0)
        nums["extrapolated_limit_stress_mpa"] = round(sigma, 2)
        if fs_ult:
            ms_u = material["ftu_mpa"] / (fs_ult * sigma) - 1
            ms_y = material["fty_mpa"] / sigma - 1
            nums.update(ms_ultimate=round(ms_u, 4), ms_yield=round(ms_y, 4), ms_min=round(min(ms_u, ms_y), 4))
            chk("positive margin of safety from the extrapolated stress", min(ms_u, ms_y) >= 0, f"MS {min(ms_u, ms_y):+.3f}")
        if screening_stress_limit:
            ratio = sigma / screening_stress_limit
            nums["fea_to_screening_ratio"] = round(ratio, 3)
            chk("agrees with the closed-form screening stress", float(accept["screening_ratio_min"]) <= ratio <= float(accept["screening_ratio_max"]),
                f"ratio {ratio:.2f} (allowed {accept['screening_ratio_min']}..{accept['screening_ratio_max']})")
    bad = [c for c in checks if c["result"] == "fail"]
    return {"verdict": "rejected" if bad else "accepted", "checks": checks, "numbers": nums,
            "issues": [f"{c['check']}: {c['detail']}" for c in bad]}
