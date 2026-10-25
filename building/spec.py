"""
Building specification: what a user (or the AI parser) asks for, normalized and checked.

Design rule, same as the rest of NitoCAD: the AI only proposes a SPEC (rooms, sizes, levels).
Deterministic code lays it out and checks it. Every default that the user did not state is
recorded in `assumptions` so it can be confirmed or changed; nothing is silently invented.

Units: millimetres (areas in m2). Plan axes: x = east, y = north; the south side is the front.
Defaults reflect common Nigerian residential practice (225 mm external / 150 mm internal
sandcrete block walls) but are ASSUMPTIONS, not code requirements.

v1 limits (stated, not hidden): rectangular footprint with width >= depth, a central corridor
running along the width with one band of rooms on each side, 1 to 4 levels connected by a
straight stair, gable or flat roof.
"""
from __future__ import annotations

import math
from typing import Any

# type -> defaults. min_* are conceptual-design rules of thumb, NOT building-code values.
ROOMS: dict[str, dict[str, Any]] = {
    "living":         {"area": 25.0, "min_area": 14.0, "min_dim": 3000, "door": 900, "win": (1800, 1500, 600),  "band": "public",  "habitable": True},
    "dining":         {"area": 14.0, "min_area": 9.0,  "min_dim": 2700, "door": 900, "win": (1500, 1200, 900),  "band": "public",  "habitable": True},
    "kitchen":        {"area": 12.0, "min_area": 7.0,  "min_dim": 2200, "door": 900, "win": (1200, 1200, 1000), "band": "public",  "habitable": True},
    "study":          {"area": 10.0, "min_area": 6.0,  "min_dim": 2200, "door": 900, "win": (1200, 1200, 900),  "band": "public",  "habitable": True},
    "bedroom":        {"area": 14.0, "min_area": 9.0,  "min_dim": 2700, "door": 900, "win": (1500, 1200, 900),  "band": "private", "habitable": True},
    "master_bedroom": {"area": 20.0, "min_area": 12.0, "min_dim": 3000, "door": 900, "win": (1800, 1200, 900),  "band": "private", "habitable": True},
    "bathroom":       {"area": 5.0,  "min_area": 3.5,  "min_dim": 1500, "door": 800, "win": (600, 600, 1500),   "band": "private", "habitable": False},
    "toilet":         {"area": 2.5,  "min_area": 1.8,  "min_dim": 1100, "door": 750, "win": (600, 600, 1500),   "band": "private", "habitable": False},
    "store":          {"area": 6.0,  "min_area": 3.0,  "min_dim": 1500, "door": 800, "win": (600, 600, 1500),   "band": "private", "habitable": False},
}
ENTRANCE_DOOR = (1000, 2100)
DOOR_HEIGHT = 2100
LABELS = {"master_bedroom": "Master bedroom"}

DEFAULTS = {
    "floor_height_mm": (3000.0, "Floor-to-floor height 3000 mm"),
    "slab_mm": (200.0, "Floor slab thickness 200 mm"),
    "ext_wall_mm": (225.0, "External walls 225 mm (9 in sandcrete block)"),
    "int_wall_mm": (150.0, "Internal walls 150 mm (6 in sandcrete block)"),
    "corridor_width_mm": (1200.0, "Corridor clear width 1200 mm"),
}
MAX_LEVELS = 4


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _title(rtype: str) -> str:
    return LABELS.get(rtype, rtype.replace("_", " ").capitalize())


def normalize_building_spec(raw: Any) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Returns (spec, errors, assumptions). The spec is only usable when errors is empty."""
    errors: list[str] = []
    assumptions: list[str] = []
    if not isinstance(raw, dict):
        return None, ["Provide a JSON object describing the building."], assumptions

    spec: dict[str, Any] = {"name": str(raw.get("name") or "building")[:60]}
    for key, lo, hi, label in (("width_mm", 5000, 40000, "width"), ("depth_mm", 5000, 40000, "depth")):
        v = raw.get(key)
        if not (_num(v) and lo <= v <= hi):
            errors.append(f"{label} ({key}) must be a number between {lo} and {hi} mm.")
        else:
            spec[key] = float(v)
    if "width_mm" in spec and "depth_mm" in spec and spec["depth_mm"] > spec["width_mm"]:
        errors.append(f"width ({spec['width_mm']:g} mm) must be the longer side; swap width and depth "
                      f"(the corridor runs along the width).")

    for key, (default, label) in DEFAULTS.items():
        v = raw.get(key)
        if v is None:
            spec[key] = default
            assumptions.append(f"{label} (assumed)")
        elif _num(v) and v > 0:
            spec[key] = float(v)
        else:
            errors.append(f"{key} must be a positive number of mm.")
            spec[key] = default
    if not (2700 <= spec["floor_height_mm"] <= 4000):
        errors.append("floor_height_mm must be between 2700 and 4000.")
    if not (100 <= spec["ext_wall_mm"] <= 450) or not (75 <= spec["int_wall_mm"] <= 300):
        errors.append("wall thicknesses out of range (external 100-450 mm, internal 75-300 mm).")
    if not (150 <= spec["slab_mm"] <= 400):
        errors.append("slab_mm must be between 150 and 400.")
    if not (900 <= spec["corridor_width_mm"] <= 2400):
        errors.append("corridor_width_mm must be between 900 and 2400.")

    roof_in = raw.get("roof") or {}
    rtype = roof_in.get("type", "gable")
    if roof_in.get("type") is None:
        assumptions.append("Gable roof (assumed)")
    pitch = roof_in.get("pitch_deg", 25.0)
    if roof_in.get("pitch_deg") is None and rtype == "gable":
        assumptions.append("Roof pitch 25 degrees (assumed)")
    over = roof_in.get("overhang_mm", 450.0)
    if rtype not in ("gable", "flat"):
        errors.append("roof.type must be \"gable\" or \"flat\".")
    if not (_num(pitch) and 10 <= pitch <= 45):
        errors.append("roof.pitch_deg must be between 10 and 45.")
        pitch = 25.0
    if not (_num(over) and 0 <= over <= 1500):
        errors.append("roof.overhang_mm must be between 0 and 1500.")
        over = 450.0
    spec["roof"] = {"type": rtype, "pitch_deg": float(pitch), "overhang_mm": float(over)}

    levels_in = raw.get("levels")
    levels: list[dict[str, Any]] = []
    if not isinstance(levels_in, list) or not (1 <= len(levels_in) <= MAX_LEVELS):
        errors.append(f"levels must be a list of 1 to {MAX_LEVELS} levels (ground floor first).")
        levels_in = []
    for k, lv in enumerate(levels_in):
        rooms_in = lv.get("rooms") if isinstance(lv, dict) else None
        if not isinstance(rooms_in, list) or not rooms_in:
            errors.append(f"level {k}: rooms must be a non-empty list.")
            continue
        expanded: list[dict[str, Any]] = []
        for j, r in enumerate(rooms_in):
            if not isinstance(r, dict) or r.get("type") not in ROOMS:
                errors.append(f"level {k} room {j}: type must be one of {', '.join(ROOMS)}.")
                continue
            count = r.get("count", 1)
            if not (isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 10):
                errors.append(f"level {k} room {j}: count must be an integer from 1 to 10.")
                count = 1
            area = r.get("area_m2")
            if area is not None and not (_num(area) and 1.0 <= area <= 200.0):
                errors.append(f"level {k} room {j}: area_m2 must be between 1 and 200.")
                area = None
            for _ in range(count):
                expanded.append({"type": r["type"], "name": r.get("name") if count == 1 else None, "area_m2": area})
        seen: dict[str, int] = {}
        total = {t: sum(1 for e in expanded if e["type"] == t) for t in ROOMS}
        for e in expanded:
            seen[e["type"]] = seen.get(e["type"], 0) + 1
            if not e["name"]:
                n = seen[e["type"]]
                e["name"] = _title(e["type"]) + (f" {n}" if total[e["type"]] > 1 else "")
            if e["area_m2"] is None:
                e["area_m2"] = ROOMS[e["type"]]["area"]
                assumptions.append(f"Level {k}: {e['name']} area {e['area_m2']:g} m2 (assumed default)")
            e["area_m2"] = float(e["area_m2"])
        levels.append({"rooms": expanded})
    if levels and not any(r["type"] == "living" for r in levels[0]["rooms"]):
        errors.append("The ground floor needs a living room (the entrance opens into it).")
    spec["levels"] = levels
    return (spec if not errors else None), errors, assumptions
