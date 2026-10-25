"""
Rule checking with an honest result vocabulary: PASS / FAIL / UNKNOWN / NOT_APPLICABLE.

A rule set carries its own provenance: whether it is authoritative, which jurisdiction, which
citation each rule comes from. NitoCAD ships ONLY a conceptual rule set of rules of thumb
(authoritative = false); it contains no building-code values, because none were available to verify.
A jurisdiction's real rules can be loaded as JSON (see load_ruleset). Even when every loaded rule
passes, the overall result is never PASS, because structural, fire, egress, services and approvals
remain unassessed (UNASSESSED below): the model reports UNKNOWN for them rather than staying silent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .spec import ROOMS

UNASSESSED = {
    "structural_design": "No structural analysis or design has been performed.",
    "foundation_design": "Foundations need a geotechnical investigation and an engineer (see structure.py for a gated preliminary estimate).",
    "fire_safety": "No fire-safety rules or evidence loaded.",
    "means_of_egress": "Exit routes, travel distances and doors are not assessed against any code.",
    "accessibility": "No accessibility rules loaded.",
    "electrical_services": "Not designed.",
    "plumbing_and_drainage": "Not designed.",
    "ventilation_and_daylight": "Only a glazing rule of thumb is checked unless an authoritative rule set is loaded.",
    "energy_and_environment": "Not assessed.",
    "planning_zoning_and_approvals": "Local approval, setbacks and professional sign-off (architect, engineer) are required.",
}

KINDS = {"min_room_area", "min_room_dimension", "min_clear_height", "stair_max_riser", "stair_min_tread",
         "stair_min_width", "min_door_width", "min_corridor_width", "min_glazing_ratio"}

CONCEPT_RULESET: dict[str, Any] = {
    "name": "NitoCAD concept rules of thumb", "authoritative": False, "jurisdiction": None,
    "source": "Rules of thumb used for conceptual layout. NOT building-code values.",
    "rules": [
        {"id": "C1", "title": "Minimum room area", "kind": "min_room_area",
         "by_type": {t: v["min_area"] for t, v in ROOMS.items()}},
        {"id": "C2", "title": "Minimum room dimension", "kind": "min_room_dimension",
         "by_type": {t: v["min_dim"] for t, v in ROOMS.items()}},
        {"id": "C3", "title": "Minimum clear storey height", "kind": "min_clear_height", "value": 2400},
        {"id": "C4", "title": "Maximum stair riser", "kind": "stair_max_riser", "value": 190},
        {"id": "C5", "title": "Minimum stair tread", "kind": "stair_min_tread", "value": 250},
        {"id": "C6", "title": "Minimum stair width", "kind": "stair_min_width", "value": 900},
        {"id": "C7", "title": "Minimum door width", "kind": "min_door_width", "by_kind": {"door": 750, "entrance": 900}},
        {"id": "C8", "title": "Minimum corridor width", "kind": "min_corridor_width", "value": 1000},
        {"id": "C9", "title": "Minimum glazing as a share of floor area", "kind": "min_glazing_ratio", "value": 0.10},
    ],
}


def load_ruleset(src: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Load and validate a jurisdiction rule set (dict, JSON text or a path to a JSON file)."""
    errors: list[str] = []
    try:
        if isinstance(src, (str, Path)) and Path(str(src)).exists():
            data = json.loads(Path(src).read_text(encoding="utf-8"))
        elif isinstance(src, str):
            data = json.loads(src)
        else:
            data = src
    except (OSError, ValueError) as exc:
        return None, [f"Could not read the rule set: {exc}"]
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list) or not data["rules"]:
        return None, ["A rule set needs a non-empty \"rules\" list."]
    ids: set[str] = set()
    for i, r in enumerate(data["rules"]):
        if not isinstance(r, dict) or r.get("kind") not in KINDS:
            errors.append(f"rules[{i}]: kind must be one of {', '.join(sorted(KINDS))}.")
            continue
        if r.get("id") in ids or not r.get("id"):
            errors.append(f"rules[{i}]: id must be present and unique.")
        ids.add(r.get("id"))
        vals = list(r.get("by_type", {}).values()) + list(r.get("by_kind", {}).values()) + ([r["value"]] if "value" in r else [])
        if not vals or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in vals):
            errors.append(f"rules[{i}] ({r.get('id')}): needs positive numeric value(s).")
    out = {"name": str(data.get("name", "unnamed rule set")), "authoritative": bool(data.get("authoritative", False)),
           "jurisdiction": data.get("jurisdiction"), "source": data.get("source"), "rules": data["rules"]}
    return (None if errors else out), errors


def _res(rule, subject, result, measured=None, required=None, unit="", note=""):
    return {"rule_id": rule["id"], "title": rule["title"], "subject": subject, "result": result, "measured": measured,
            "required": required, "unit": unit, "citation": rule.get("citation"), "note": note}


def evaluate(model: dict[str, Any], ruleset: dict[str, Any] | None = None) -> dict[str, Any]:
    rs = ruleset or CONCEPT_RULESET
    g, levels = model["geometry"], model["levels"]
    results: list[dict[str, Any]] = []
    for rule in rs["rules"]:
        kind = rule["kind"]
        missing_cite = rs.get("authoritative") and not rule.get("citation")
        before = len(results)
        if kind in ("min_room_area", "min_room_dimension"):
            table = rule["by_type"]
            for lv in levels:
                for r in lv["rooms"]:
                    if r["type"] in table:
                        x0, y0, x1, y1 = r["net"]
                        measured = r["area_m2"] if kind == "min_room_area" else min(x1 - x0, y1 - y0)
                        need = table[r["type"]]
                        results.append(_res(rule, f"L{lv['index']} {r['name']}", "PASS" if measured >= need else "FAIL",
                                            round(measured, 2), need, "m2" if kind == "min_room_area" else "mm"))
        elif kind == "min_clear_height":
            results.append(_res(rule, "building", "PASS" if g["h_w"] >= rule["value"] else "FAIL", g["h_w"], rule["value"], "mm",
                                "Structural height before finishes; finishes reduce the clear height."))
        elif kind in ("stair_max_riser", "stair_min_tread", "stair_min_width"):
            if len(levels) < 2:
                results.append(_res(rule, "building", "NOT_APPLICABLE", note="Single-storey: no stair."))
            else:
                st = model["stair"]
                measured = {"stair_max_riser": st["riser"], "stair_min_tread": st["tread"], "stair_min_width": st["width"]}[kind]
                ok = measured <= rule["value"] if kind == "stair_max_riser" else measured >= rule["value"]
                results.append(_res(rule, "stair", "PASS" if ok else "FAIL", round(measured, 1), rule["value"], "mm"))
        elif kind == "min_door_width":
            for lv in levels:
                for o in lv["openings"]:
                    if o["kind"] == "door":
                        need = rule["by_kind"].get("entrance" if o["wall"] == "ext_s" else "door")
                        if need:
                            results.append(_res(rule, f"{o['id']} ({'entrance' if o['wall'] == 'ext_s' else 'door'})",
                                                "PASS" if o["width"] >= need else "FAIL", o["width"], need, "mm"))
        elif kind == "min_corridor_width":
            results.append(_res(rule, "corridor", "PASS" if g["cw"] >= rule["value"] else "FAIL", g["cw"], rule["value"], "mm"))
        elif kind == "min_glazing_ratio":
            for lv in levels:
                for r in lv["rooms"]:
                    if ROOMS.get(r["type"], {}).get("habitable"):
                        glaz = sum(o["width"] * o["height"] for o in lv["openings"]
                                   if o["room"] == r["id"] and o["kind"] == "window") / 1e6
                        ratio = glaz / r["area_m2"]
                        results.append(_res(rule, f"L{lv['index']} {r['name']}", "PASS" if ratio >= rule["value"] else "FAIL",
                                            round(ratio, 3), rule["value"], "ratio"))
        if missing_cite:  # an authoritative rule without a citation cannot be trusted as evidence
            for res in results[before:]:
                res["result"], res["note"] = "UNKNOWN", "Rule has no citation, so it cannot be treated as evidence."

    counts = {k: sum(1 for r in results if r["result"] == k) for k in ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE")}
    overall = "FAIL" if counts["FAIL"] else "UNKNOWN"
    authoritative = bool(rs.get("authoritative"))
    return {
        "ruleset": {k: rs.get(k) for k in ("name", "authoritative", "jurisdiction", "source")},
        "rule_results": results, "counts": counts, "overall": overall,
        "building_code_compliance": "NOT_ASSESSED" if not authoritative else ("FAIL" if counts["FAIL"] else "PARTIAL"),
        "statement": ("These are rules of thumb, not a building code. " if not authoritative else "") +
                     f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['UNKNOWN']} unknown. "
                     "This is not an approval: the topics below remain unassessed.",
        "unassessed_topics": dict(UNASSESSED),
    }
