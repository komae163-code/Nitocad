"""
Flat building parameters: the Studio's view of a building.

The Studio shows and edits a part as a flat list of scalar parameters ("floors", "bedrooms",
"width_mm", ...). A building is described that way too, so the existing parameter panel, the
chat edit flow ("make it 14 m wide", "add a bedroom") and the stored job parameters all work
unchanged. Each flat description expands, deterministically, into the full building spec that
the layout engine uses.

Parameters (all optional; the defaults are listed in `FLAT_DEFAULTS` and reported as assumptions):
  floors 1-4, bedrooms, master_bedroom (0/1), bathrooms, toilets, dining_room (0/1), study (0/1),
  store (0/1), width_mm, depth_mm (the footprint; chosen automatically when omitted), floor_height_mm,
  roof_type ("gable" | "flat"), roof_pitch_deg.
"""
from __future__ import annotations

from typing import Any

from .intake import no_footprint_message, search_footprint, spec_from_counts, suggest_footprint
from .layout import design_building

FLAT_DEFAULTS: dict[str, Any] = {"floors": 1, "bedrooms": 2, "master_bedroom": 0, "bathrooms": 1, "toilets": 0,
                                 "dining_room": 0, "study": 0, "store": 0, "roof_type": "gable"}
INT_RANGES = {"floors": (1, 4), "bedrooms": (1, 8), "bathrooms": (0, 6), "toilets": (0, 4)}
FLAGS = ("master_bedroom", "dining_room", "study", "store")


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) != float("inf")


def flat_to_raw_spec(params: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Returns (raw spec, assumptions, errors). The footprint is only included when given."""
    errors: list[str] = []
    assumptions: list[str] = []
    p: dict[str, Any] = {}
    for k, default in FLAT_DEFAULTS.items():
        v = params.get(k)
        if v is None:
            p[k] = default
            if k in ("floors", "bedrooms", "bathrooms"):
                assumptions.append(f"{k} = {default} (not stated)")
            continue
        p[k] = v
    for k, (lo, hi) in INT_RANGES.items():
        v = p[k]
        if isinstance(v, bool) or not _num(v) or int(v) != v or not lo <= v <= hi:
            errors.append(f"{k} must be a whole number from {lo} to {hi}.")
        else:
            p[k] = int(v)
    for k in FLAGS:
        v = p[k]
        if isinstance(v, bool):
            p[k] = int(v)
        elif v not in (0, 1, 0.0, 1.0):
            errors.append(f"{k} must be 0 or 1.")
    if p["roof_type"] not in ("gable", "flat"):
        errors.append('roof_type must be "gable" or "flat".')
    if errors:
        return None, assumptions, errors
    counts = {"levels": p["floors"], "bedrooms": p["bedrooms"], "master": bool(p["master_bedroom"]),
              "bathrooms": p["bathrooms"], "toilets": p["toilets"] or None, "dining": bool(p["dining_room"]),
              "study": bool(p["study"]), "store": bool(p["store"]), "kitchen": True, "living": True}
    levels, more = spec_from_counts(counts)
    spec: dict[str, Any] = {"name": "Building", "levels": levels, "roof": {"type": p["roof_type"]}}
    if _num(params.get("roof_pitch_deg")):
        spec["roof"]["pitch_deg"] = float(params["roof_pitch_deg"])
    if _num(params.get("floor_height_mm")):
        spec["floor_height_mm"] = float(params["floor_height_mm"])
    for k in ("width_mm", "depth_mm"):
        if _num(params.get(k)):
            spec[k] = float(params[k])
    return spec, assumptions + more, []


def resolve_flat(params: dict[str, Any]) -> dict[str, Any]:
    """Flat parameters -> checked building. Auto-chooses the footprint when it is missing.

    Returns {"ok", "spec", "model", "errors", "warnings", "assumptions", "corrections"} where corrections
    maps parameter -> (old, new) for values the system filled in (the footprint)."""
    spec, assumptions, errors = flat_to_raw_spec(params)
    if errors or spec is None:
        return {"ok": False, "spec": None, "model": None, "errors": errors, "warnings": [], "assumptions": assumptions, "corrections": {}}
    corrections: dict[str, tuple[Any, Any]] = {}
    if "width_mm" not in spec or "depth_mm" not in spec:
        found = search_footprint({k: v for k, v in spec.items() if k not in ("width_mm", "depth_mm")})
        fp = found["footprint"]
        if fp is None:
            return {"ok": False, "spec": spec, "model": None, "assumptions": assumptions, "warnings": [], "corrections": {},
                    "errors": [no_footprint_message(found, "Reduce the rooms or give a footprint.")]}
        spec["width_mm"], spec["depth_mm"] = fp
        corrections = {"width_mm": ("auto", fp[0]), "depth_mm": ("auto", fp[1])}
        assumptions.append(f"Footprint {fp[0] / 1000:g} m x {fp[1] / 1000:g} m chosen as the smallest that fits")
    design = design_building(spec)
    if not design["ok"]:
        errors = list(design["errors"])
        if "width_mm" in params and "depth_mm" in params:
            alt = suggest_footprint({k: v for k, v in spec.items() if k not in ("width_mm", "depth_mm")})
            if alt:
                errors.append(f"The smallest footprint that fits this program is {alt[0] / 1000:g} x {alt[1] / 1000:g} m.")
        return {"ok": False, "spec": spec, "model": None, "errors": errors, "warnings": design["warnings"],
                "assumptions": assumptions, "corrections": corrections}
    return {"ok": True, "spec": spec, "model": design["model"], "errors": [], "warnings": design["warnings"],
            "assumptions": assumptions + design["assumptions"], "corrections": corrections}
