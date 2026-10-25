"""
PRELIMINARY foundation sizing, gated on a real soil bearing capacity.

Without a soil bearing capacity from a geotechnical investigation the result is UNKNOWN: nothing is
estimated. With one, strip footings are sized under the load-bearing walls from simple statics
(service loads, one-way slabs spanning the building depth, 10% allowance for footing self-weight).
This is an ESTIMATE for an engineer to review, never a design: no settlement, wind, seismic, lateral
earth pressure, groundwater, reinforcement or soil-structure interaction is considered. Every load
and weight used is an assumption listed in the result.
"""
from __future__ import annotations

import math
from typing import Any

LOADS = {
    "slab_finish_kN_m2": 1.5, "partition_allowance_kN_m2": 1.0, "live_floor_kN_m2": 1.5, "live_top_slab_kN_m2": 0.25,
    "roof_dead_kN_m2": 1.0, "roof_live_kN_m2": 0.5, "concrete_kN_m3": 24.0, "ext_wall_kN_m2": 3.0, "int_wall_kN_m2": 2.1,
}
MIN_FOOTING_WIDTH = 450.0
MIN_FOOTING_DEPTH = 225.0
LIMITATIONS = [
    "Preliminary estimate for review by a structural engineer; not a design.",
    "The soil bearing capacity must come from a geotechnical investigation of the actual site.",
    "No settlement, wind, seismic, lateral earth pressure, groundwater, frost or reinforcement design.",
    "Loads are service-level assumptions (listed); slabs assumed one-way spanning the building depth.",
    "Internal partitions are assumed non-load-bearing; heavy partitions need a thickened slab or ground beam.",
]
LOAD_BEARING = ("ext_s", "ext_n", "ext_w", "ext_e", "cor_s", "cor_n")


def _ceil(v: float, step: float) -> float:
    return math.ceil(v / step - 1e-9) * step


def preliminary_foundation(model: dict[str, Any], soil_bearing_kpa: float | None = None,
                           founding_depth_mm: float = 900.0, loads: dict[str, float] | None = None) -> dict[str, Any]:
    if soil_bearing_kpa is None:
        return {"status": "UNKNOWN", "engineer_review_required": True,
                "reason": "Soil bearing capacity was not provided. A geotechnical investigation is required before any "
                          "foundation sizing; nothing has been estimated.", "limitations": LIMITATIONS}
    if not (isinstance(soil_bearing_kpa, (int, float)) and 25 <= soil_bearing_kpa <= 1000):
        return {"status": "UNKNOWN", "engineer_review_required": True,
                "reason": "soil_bearing_kpa must be between 25 and 1000 kN/m2 (allowable bearing pressure).", "limitations": LIMITATIONS}
    L = {**LOADS, **(loads or {})}
    g, levels, roof = model["geometry"], model["levels"], model["roof"]
    n = len(levels)
    h_w, e = g["h_w"] / 1000, g["e"]
    tan = math.tan(math.radians(roof["pitch_deg"])) if roof["type"] == "gable" else 0.0
    slab_g = L["concrete_kN_m3"] * g["slab"] / 1000
    trib = {"ext_s": (g["yc0"] - g["Y0"]) / 2, "cor_s": (g["yc0"] - g["Y0"]) / 2 + (g["yc1"] - g["yc0"]) / 2,
            "cor_n": (g["yc1"] - g["yc0"]) / 2 + (g["Y1"] - g["yc1"]) / 2, "ext_n": (g["Y1"] - g["yc1"]) / 2}
    footings: list[dict[str, Any]] = []
    walls = {w["id"]: w for w in levels[0]["walls"]}
    if founding_depth_mm < g["slab"] + MIN_FOOTING_DEPTH + 50:
        return {"status": "UNKNOWN", "engineer_review_required": True,
                "reason": f"founding_depth_mm must be at least {g['slab'] + MIN_FOOTING_DEPTH + 50:.0f} mm.", "limitations": LIMITATIONS}
    for wid in LOAD_BEARING:
        w = walls[wid]
        t = w["t"]
        length = abs(w["b"][0] - w["a"][0]) + abs(w["b"][1] - w["a"][1])
        wall_w = L["ext_wall_kN_m2"] if w["kind"] == "ext" else L["int_wall_kN_m2"]
        dead = n * h_w * wall_w
        live = 0.0
        if wid in trib:
            tw = trib[wid] / 1000
            for k in range(1, n + 1):
                top = k == n
                dead += tw * (slab_g + (0.0 if top else L["slab_finish_kN_m2"] + L["partition_allowance_kN_m2"]))
                live += tw * (L["live_top_slab_kN_m2"] if top else L["live_floor_kN_m2"])
        if roof["type"] == "gable" and wid in ("ext_s", "ext_n"):
            span = (g["D"] / 2 + roof["overhang_mm"]) / 1000
            dead += span * L["roof_dead_kN_m2"]
            live += span * L["roof_live_kN_m2"]
        if roof["type"] == "gable" and wid in ("ext_w", "ext_e"):
            dead += (g["D"] / 1000) * ((g["D"] / 2000) * tan) / 2 * L["ext_wall_kN_m2"] / (g["D"] / 1000)
        n_line = dead + live
        b = max(MIN_FOOTING_WIDTH, _ceil(1.10 * n_line / soil_bearing_kpa * 1000, 50))
        depth = max(MIN_FOOTING_DEPTH, _ceil((b - t) / 2, 25))
        footings.append({"wall": wid, "length_mm": round(length, 1), "wall_thickness_mm": t,
                         "service_line_load_kN_per_m": round(n_line, 1), "width_mm": b, "depth_mm": depth})
    if max(f["depth_mm"] for f in footings) + g["slab"] + 50 > founding_depth_mm:
        return {"status": "UNKNOWN", "engineer_review_required": True,
                "reason": "The footings are too deep for the founding depth given; increase founding_depth_mm or the soil capacity.",
                "limitations": LIMITATIONS}
    vol = sum(f["length_mm"] * f["width_mm"] * f["depth_mm"] for f in footings) / 1e9
    return {"status": "PRELIMINARY", "engineer_review_required": True, "soil_bearing_kpa": soil_bearing_kpa,
            "founding_depth_mm": founding_depth_mm, "footings": footings, "footing_concrete_m3": round(vol, 1),
            "assumptions": [f"{k.replace('_', ' ')} = {v}" for k, v in L.items()], "limitations": LIMITATIONS}


def foundation_parts(model: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """3D strip footings with a plinth wall up to ground level (only for a PRELIMINARY result)."""
    if result.get("status") != "PRELIMINARY":
        return []
    walls = {w["id"]: w for w in model["levels"][0]["walls"]}
    z_bottom = -float(result["founding_depth_mm"])
    parts = []
    for f in result["footings"]:
        w = walls[f["wall"]]
        t, b, d = f["wall_thickness_mm"], f["width_mm"], f["depth_mm"]
        cx, cy = (w["a"][0] + w["b"][0]) / 2, (w["a"][1] + w["b"][1]) / 2
        ln = f["length_mm"]
        if w["orient"] == "x":
            foot = {"type": "rect", "width": ln, "height": b, "center": [cx, cy]}
            plinth = {"type": "rect", "width": ln, "height": t, "center": [cx, cy]}
        else:
            foot = {"type": "rect", "width": b, "height": ln, "center": [cx, cy]}
            plinth = {"type": "rect", "width": t, "height": ln, "center": [cx, cy]}
        feats = [{"id": "footing", "op": "extrude", "plane": "XY", "offset": z_bottom, "profile": foot, "distance": d},
                 {"id": "plinth", "op": "extrude", "plane": "XY", "offset": z_bottom + d, "profile": plinth,
                  "distance": -(z_bottom + d), "mode": "add"}]
        parts.append({"name": f"footing_{f['wall']}", "part_type": "feature_tree", "parameters": {"features": feats},
                      "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "mate": None})
    return parts
