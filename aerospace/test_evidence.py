"""Structured test evidence: NitoCAD compares measurements with criteria itself (stdlib only)."""
import re

REQUIRED = ("id", "test_type", "lab", "article", "report_ref", "report_sha256", "calibration_refs", "criteria", "measurements")


def _cmp(v, op, t):
    return {"<=": v <= t, ">=": v >= t, "==": abs(v - t) <= 1e-9 * max(1.0, abs(t))}[op]


def evaluate(ev: dict, revision_id: str = None) -> dict:
    miss = [k for k in REQUIRED if ev.get(k) in (None, "", [], {})]
    issues = []
    if miss:
        return {"verdict": "incomplete", "issues": ["missing: " + ", ".join(miss)], "criteria": []}
    lab, art = ev["lab"], ev["article"]
    if not str(lab.get("name", "")).strip() or not str(lab.get("accreditation", "")).strip():
        issues.append("lab needs a name and an accreditation reference")
    if not str(art.get("serial", "")).strip() or not str(art.get("conformity_record_id", "")).strip():
        issues.append("test article needs a serial number and a conformity record id")
    if revision_id and art.get("design_revision_id") != revision_id:
        issues.append("test article was built to a different design revision")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", str(ev["report_sha256"])):
        issues.append("report_sha256 must be a 64-character SHA-256 of the test report")
    rows = []
    meas = {m.get("name"): m for m in ev["measurements"] if isinstance(m, dict)}
    for c in ev["criteria"]:
        m = meas.get(c.get("name"))
        if m is None:
            rows.append({"criterion": c.get("name"), "result": "missing measurement"})
            issues.append(f"no measurement for criterion {c.get('name')}")
            continue
        if str(m.get("unit", "")) != str(c.get("unit", "")):
            rows.append({"criterion": c["name"], "result": "unit mismatch"})
            issues.append(f"{c['name']}: unit {m.get('unit')} does not match the criterion unit {c.get('unit')}")
            continue
        if c.get("comparator") not in ("<=", ">=", "=="):
            issues.append(f"{c['name']}: comparator must be <=, >= or ==")
            continue
        ok = _cmp(float(m["value"]), c["comparator"], float(c["target"]))
        rows.append({"criterion": c["name"], "measured": m["value"], "comparator": c["comparator"], "target": c["target"], "result": "pass" if ok else "fail"})
        if not ok:
            issues.append(f"{c['name']}: measured {m['value']} violates {c['comparator']} {c['target']}")
    return {"verdict": "accepted" if not issues else ("rejected" if any(r.get("result") == "fail" for r in rows) else "incomplete"),
            "issues": issues, "criteria": rows}
