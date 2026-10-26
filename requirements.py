"""
Requirements that stay attached to a design and are re-checked on every version.

"It must fit in a 100 x 80 x 60 box" or "keep it under 200 g" is said once. Afterwards each new
version of the part is measured against it, so an edit that quietly breaks an earlier
requirement is caught the moment it is built, not when the part arrives.

Pure Python. A requirement is {id, kind, op, value, text}; kinds:
    envelope       value [x, y, z] mm. The part must fit inside in SOME orientation (compared as
                   sorted dimensions), so it is not tied to how the template happens to orient it.
    max_dimension  value mm, the longest overall dimension
    max_mass_g     value g
    min_wall       value mm
    material       value text, matched by grade when both sides are recognised

Evaluation returns pass | fail | unknown. "unknown" means the measurement was not available. It
is never reported as a pass.
"""
from __future__ import annotations

import math
import re
from typing import Any

import dfm
import materials

KINDS = ("envelope", "max_dimension", "max_mass_g", "min_wall", "material")
_NUM = r"(\d+(?:\.\d+)?)"
_UNIT_MM = {"mm": 1.0, "millimetre": 1.0, "millimeter": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, "inches": 25.4}
_TOL = 1e-6


def _mm(value: str, unit: str | None) -> float:
    u = (unit or "mm").lower()
    return float(value) * (_UNIT_MM.get(u) or _UNIT_MM.get(u.rstrip("s"), 1.0))


def parse_requirements(text: str) -> list[dict[str, Any]]:
    """Pull explicit requirements out of a sentence. Conservative: only clear phrasings, so a
    casual mention is never turned into a hard requirement."""
    t = " ".join(str(text).split())
    low = t.lower()
    found: list[dict[str, Any]] = []

    for m in re.finditer(rf"(?:must|should|needs? to|has to|have to|to)?\s*fit\w*\s+(?:in|inside|within|into)\s+(?:a\s+|an\s+)?{_NUM}\s*[x\u00d7*]\s*{_NUM}\s*[x\u00d7*]\s*{_NUM}\s*(mm|cm|m|in|inch|inches)?", low):
        u = m.group(4)
        dims = [round(_mm(m.group(i), u), 3) for i in (1, 2, 3)]
        found.append({"kind": "envelope", "op": "<=", "value": dims, "text": m.group(0).strip()})

    for m in re.finditer(rf"(?:under|below|less than|at most|no more than|max(?:imum)?(?: mass| weight)?(?: of)?|lighter than|not (?:heavier|more) than|weigh\w*\s+(?:under|less than|at most))\s+{_NUM}\s*(kg|kilograms?|g|grams?)\b", low):
        grams = float(m.group(1)) * (1000.0 if m.group(2).startswith("k") else 1.0)
        found.append({"kind": "max_mass_g", "op": "<=", "value": round(grams, 3), "text": m.group(0).strip()})

    for m in re.finditer(rf"(?:walls?\s+(?:of\s+)?(?:at least|min(?:imum)?|no thinner than)\s+{_NUM}\s*mm|(?:at least|min(?:imum)?)\s+{_NUM}\s*mm\s+walls?)", low):
        found.append({"kind": "min_wall", "op": ">=", "value": float(m.group(1) or m.group(2)), "text": m.group(0).strip()})

    for m in re.finditer(rf"(?:no|not)\s+(?:larger|bigger|longer|taller|wider)\s+than\s+{_NUM}\s*(mm|cm|m|in|inch|inches)\b|(?:max(?:imum)?|at most)\s+(?:length|width|height|size)\s+(?:of\s+)?{_NUM}\s*(mm|cm|m|in|inch|inches)\b", low):
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        found.append({"kind": "max_dimension", "op": "<=", "value": round(_mm(num, unit), 3), "text": m.group(0).strip()})

    for m in re.finditer(r"(?:must be made (?:of|from)|made (?:of|from)|material (?:must be|is|should be))\s+([a-z0-9][a-z0-9 \-]{1,30})", low):
        name = m.group(1).strip()
        name = re.split(r"\b(?:and|with|that|so|but|,)\b", name)[0].strip()
        if materials.lookup(name):
            found.append({"kind": "material", "op": "==", "value": name, "text": m.group(0).strip()})

    seen, out = set(), []
    for r in found:
        key = (r["kind"], str(r["value"]))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def describe(req: dict) -> str:
    v, k = req["value"], req["kind"]
    if k == "envelope":
        return f"fit inside {v[0]:g} x {v[1]:g} x {v[2]:g} mm"
    if k == "max_dimension":
        return f"no dimension over {v:g} mm"
    if k == "max_mass_g":
        return f"mass at most {v:g} g"
    if k == "min_wall":
        return f"walls at least {v:g} mm"
    return f"made of {v}"


def facts_from_result(result: dict[str, Any], part_type: str | None = None) -> dict[str, Any]:
    """The measurable facts about a built part, from the generator's result dict."""
    params = result.get("parameters") or {}
    ptype = part_type or params.get("part_type")
    # An assembly's measurable facts come from the geometric checks (assembly.assembly_checks_report).
    # result["assembly"] is the mate engine's own report and holds no size or mass.
    asm = result.get("assembly_checks")
    if asm is None and (ptype == "assembly" or params.get("part_type") == "assembly"):
        asm = {}
    if asm is not None:
        mp = asm.get("mass_properties") or {}
        keys = ("bom", "interferences", "disconnected_groups", "unchecked_pairs", "notes", "clean",
                "mass_properties", "overall_size_mm", "bom_csv", "parts")
        return {"material": None, "size_mm": asm.get("overall_size_mm"), "min_wall_mm": None,
                "mass_g": mp.get("mass_g") if mp.get("complete") else None,
                "mass_note": None if mp.get("complete") else "some parts have no known material, so the total mass is unknown",
                "assembly": {k: asm.get(k) for k in keys}}
    pp = params.get("parameters") or {}
    material = params.get("material")
    measured = ((result.get("verification") or {}).get("measured")) or {}
    facts: dict[str, Any] = {"material": material, "size_mm": None, "mass_g": None, "min_wall_mm": None, "mass_note": None}
    bb = measured.get("bbox")
    if bb:
        facts["size_mm"] = [round(bb["xmax"] - bb["xmin"], 3), round(bb["ymax"] - bb["ymin"], 3), round(bb["zmax"] - bb["zmin"], 3)]
    vol, dens = measured.get("volume"), materials.density_g_cm3(material)
    if vol and dens:
        facts["mass_g"] = round(vol / 1000.0 * dens, 3)
    elif vol:
        facts["mass_note"] = "set a material to get mass"
    if ptype:
        facts["min_wall_mm"] = dfm.features(ptype, pp).get("min_wall_mm")
    return facts


def evaluate(reqs: list[dict], facts: dict[str, Any] | None) -> list[dict[str, Any]]:
    facts = facts or {}
    out = []
    for r in reqs:
        res = {"id": r.get("id"), "kind": r["kind"], "text": describe(r), "status": "unknown", "measured": None,
               "limit": r["value"], "detail": ""}
        k, v = r["kind"], r["value"]
        size = facts.get("size_mm")
        if k == "envelope":
            if size:
                a, b = sorted(size, reverse=True), sorted(v, reverse=True)
                ok = all(x <= y + _TOL for x, y in zip(a, b))
                res.update(status="pass" if ok else "fail", measured=size,
                           detail=f"part {size[0]:g} x {size[1]:g} x {size[2]:g} mm in any orientation vs {v[0]:g} x {v[1]:g} x {v[2]:g}")
            else:
                res["detail"] = "overall size was not measured"
        elif k == "max_dimension":
            if size:
                big = max(size)
                res.update(status="pass" if big <= v + _TOL else "fail", measured=round(big, 3), detail=f"longest dimension {big:g} mm")
            else:
                res["detail"] = "overall size was not measured"
        elif k == "max_mass_g":
            m = facts.get("mass_g")
            if m is not None:
                res.update(status="pass" if m <= v + _TOL else "fail", measured=m, detail=f"mass {m:g} g")
            else:
                res["detail"] = facts.get("mass_note") or "mass could not be computed"
        elif k == "min_wall":
            w = facts.get("min_wall_mm")
            if w is not None:
                res.update(status="pass" if w >= v - _TOL else "fail", measured=w, detail=f"thinnest wall {w:g} mm")
            else:
                res["detail"] = "wall thickness is not defined for this part type"
        elif k == "material":
            have = facts.get("material")
            if have:
                a, b = materials.lookup(have), materials.lookup(str(v))
                same = (a.key == b.key) if a and b else str(have).lower() == str(v).lower()
                res.update(status="pass" if same else "fail", measured=have, detail=f"material is {have}")
            else:
                res["detail"] = "no material has been set"
        out.append(res)
    return out


def summarize(results: list[dict]) -> dict[str, int]:
    return {s: sum(r["status"] == s for r in results) for s in ("pass", "fail", "unknown")}
