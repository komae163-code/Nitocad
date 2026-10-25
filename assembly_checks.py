"""
Checks and reports on a built assembly: do parts collide, is anything floating, what is the
parts list and how heavy is it.

Pure Python. The geometry kernel is injected: `common_volume(a, b)` returns the volume (mm^3)
where two parts overlap, and volumes come from the caller. assembly.py supplies the real
CadQuery versions; the tests supply fakes.

What these checks are and are not: they are geometric. An interference means two solids
overlap in space. They do not know about fits, tolerances, preload or motion, and bounding
boxes are only used to skip pairs that obviously cannot touch.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Callable

BBox = dict
MAX_PAIR_CHECKS = 1500

# g/cm^3, typical values. Matched on substrings of the material text, longest first.
DENSITIES: dict[str, float] = {
    "stainless": 8.0, "316": 8.0, "304": 8.0, "steel": 7.85, "iron": 7.2, "cast iron": 7.2,
    "6061": 2.70, "7075": 2.81, "aluminum": 2.70, "aluminium": 2.70, "brass": 8.5, "bronze": 8.8,
    "copper": 8.96, "titanium": 4.43, "ti-6al-4v": 4.43, "magnesium": 1.74, "zinc": 7.1,
    "abs": 1.04, "pla": 1.24, "petg": 1.27, "nylon": 1.15, "pom": 1.41, "delrin": 1.41,
    "polycarbonate": 1.20, "acrylic": 1.18, "pvc": 1.40, "hdpe": 0.95, "peek": 1.32,
    "carbon fiber": 1.55, "carbon fibre": 1.55, "wood": 0.70, "oak": 0.75, "pine": 0.50,
}


def density_for(material: str | None) -> float | None:
    """g/cm^3 for a material name, or None when we do not recognise it."""
    if not material:
        return None
    text = material.lower()
    for key in sorted(DENSITIES, key=len, reverse=True):
        if key in text:
            return DENSITIES[key]
    return None


def _overlap_amount(a: BBox, b: BBox) -> float:
    """Smallest per-axis penetration depth; <= 0 means the boxes do not overlap."""
    return min(min(a["max"][i], b["max"][i]) - max(a["min"][i], b["min"][i]) for i in range(3))


def _gap(a: BBox, b: BBox) -> float:
    """Distance between two boxes along their widest separation; 0 or negative if they overlap."""
    return max(max(a["min"][i] - b["max"][i], b["min"][i] - a["max"][i]) for i in range(3))


def find_interferences(
    boxes: dict[str, BBox],
    common_volume: Callable[[str, str], float],
    volumes: dict[str, float] | None = None,
    *,
    min_volume: float = 1e-3,
) -> tuple[list[dict], list[dict]]:
    """Pairs of parts whose solids overlap. Returns (interferences, skipped).

    Only pairs whose bounding boxes overlap are tested with the real boolean. `skipped` lists
    pairs we could not test (the boolean failed, or there were too many pairs), so a quiet
    result is never mistaken for a clean one."""
    names = list(boxes)
    found: list[dict] = []
    skipped: list[dict] = []
    tested = 0
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if _overlap_amount(boxes[a], boxes[b]) <= 0:
                continue
            if tested >= MAX_PAIR_CHECKS:
                skipped.append({"a": a, "b": b, "reason": "too many part pairs to test"})
                continue
            tested += 1
            try:
                vol = float(common_volume(a, b))
            except Exception as exc:  # noqa: BLE001 - OCCT booleans can fail on awkward shapes
                skipped.append({"a": a, "b": b, "reason": f"overlap test failed: {str(exc)[:80]}"})
                continue
            if vol <= min_volume:
                continue
            smaller = min((volumes or {}).get(a, 0) or 0, (volumes or {}).get(b, 0) or 0)
            ratio = vol / smaller if smaller > 0 else None
            found.append({
                "a": a, "b": b, "volume_mm3": round(vol, 3),
                "share_of_smaller_part": round(ratio, 4) if ratio is not None else None,
                "severity": "major" if ratio is not None and ratio >= 0.05 else "minor",
            })
    found.sort(key=lambda r: -r["volume_mm3"])
    return found, skipped


def find_disconnected(boxes: dict[str, BBox], touch_tolerance: float = 0.05) -> list[list[str]]:
    """Groups of parts that do not touch the rest of the assembly (empty when it is one piece).
    Touching means bounding boxes overlap or are within `touch_tolerance` mm."""
    names = list(boxes)
    if len(names) < 2:
        return []
    parent = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if _gap(boxes[a], boxes[b]) <= touch_tolerance:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)
    if len(groups) == 1:
        return []
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def _summary(params: dict) -> str:
    items = [f"{k}={v}" for k, v in sorted(params.items()) if not isinstance(v, (dict, list))]
    return ", ".join(items)


def bill_of_materials(parts: list[dict], volumes: dict[str, float] | None = None) -> dict[str, Any]:
    """Group identical parts into rows with quantity, volume and mass.

    Identical means same part_type, parameters and material. `volumes` maps part name to mm^3.
    Mass needs both a volume and a recognised material; otherwise it is left out and the
    total is flagged as partial."""
    volumes = volumes or {}
    groups: dict[str, dict] = {}
    for p in parts:
        key = json.dumps([p["part_type"], p.get("parameters", {}), p.get("material")], sort_keys=True, default=str)
        g = groups.setdefault(key, {"part_type": p["part_type"], "parameters": p.get("parameters", {}),
                                    "material": p.get("material"), "names": [], "volume": volumes.get(p["name"])})
        g["names"].append(p["name"])
    rows = []
    total_mass, complete = 0.0, True
    for n, g in enumerate(groups.values(), start=1):
        qty = len(g["names"])
        vol = g["volume"]
        dens = density_for(g["material"])
        unit_mass = (vol / 1000.0) * dens if vol is not None and dens is not None else None   # mm^3 -> cm^3 -> g
        if unit_mass is None:
            complete = False
        else:
            total_mass += unit_mass * qty
        rows.append({
            "item": n, "part_type": g["part_type"], "names": g["names"], "quantity": qty,
            "material": g["material"], "description": _summary(g["parameters"]),
            "unit_volume_mm3": round(vol, 2) if vol is not None else None,
            "unit_mass_g": round(unit_mass, 2) if unit_mass is not None else None,
            "total_mass_g": round(unit_mass * qty, 2) if unit_mass is not None else None,
        })
    return {"rows": rows, "part_count": len(parts), "line_count": len(rows),
            "total_mass_g": round(total_mass, 2), "mass_is_complete": complete}


def _safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # a spreadsheet would run a cell that starts with = + - @ as a formula
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def bom_to_csv(bom: dict[str, Any]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Item", "Qty", "Part type", "Names", "Material", "Description", "Unit volume mm3", "Unit mass g", "Total mass g"])
    for r in bom["rows"]:
        w.writerow([_safe_cell(v) for v in (r["item"], r["quantity"], r["part_type"], " ".join(r["names"]), r["material"],
                                            r["description"], r["unit_volume_mm3"], r["unit_mass_g"], r["total_mass_g"])])
    w.writerow([])
    w.writerow(["Total parts", bom["part_count"], "", "", "", "", "", "",
                bom["total_mass_g"] if bom["mass_is_complete"] else f"{bom['total_mass_g']} (partial: some materials unknown)"])
    return out.getvalue()


def build_report(parts, boxes, common_volume, volumes) -> dict[str, Any]:
    """Everything the studio shows for an assembly, in one JSON-able dict."""
    interferences, skipped = find_interferences(boxes, common_volume, volumes)
    groups = find_disconnected(boxes)
    notes: list[str] = []
    if interferences:
        major = sum(1 for i in interferences if i["severity"] == "major")
        notes.append(f"{len(interferences)} overlapping pair{'s' if len(interferences) != 1 else ''}"
                     + (f", {major} serious" if major else "") + ". Parts that should fit together are overlapping in space.")
    if groups:
        notes.append(f"{len(groups)} separate groups: some parts do not touch the rest. Mate them or check their positions.")
    if skipped:
        notes.append(f"{len(skipped)} pair{'s' if len(skipped) != 1 else ''} could not be checked, so this is not a full clearance result.")
    return {"bom": bill_of_materials(parts, volumes), "interferences": interferences, "disconnected_groups": groups,
            "unchecked_pairs": skipped, "notes": notes, "clean": not (interferences or groups or skipped),
            "overall_size_mm": overall_size(boxes)}


def overall_size(boxes: dict[str, BBox]) -> list[float] | None:
    """Size (x, y, z) of the box that holds every part, or None for an empty assembly."""
    if not boxes:
        return None
    lo = [min(b["min"][i] for b in boxes.values()) for i in range(3)]
    hi = [max(b["max"][i] for b in boxes.values()) for i in range(3)]
    return [round(hi[i] - lo[i], 3) for i in range(3)]
