"""Door, window and room schedules plus approximate quantities, all computed from the model."""
from __future__ import annotations

import math
from typing import Any


def build_schedules(model: dict[str, Any]) -> dict[str, Any]:
    g = model["geometry"]
    levels = model["levels"]
    room_name = {r["id"]: r["name"] for lv in levels for r in lv["rooms"]}

    groups: dict[tuple, dict[str, Any]] = {}
    for lv in levels:
        for o in lv["openings"]:
            key = (o["kind"], round(o["width"]), round(o["height"]), round(o["sill"]))
            grp = groups.setdefault(key, {"kind": o["kind"], "width": key[1], "height": key[2], "sill": key[3],
                                          "count": 0, "locations": []})
            grp["count"] += 1
            grp["locations"].append(f"L{lv['index']} {room_name[o['room']]}")
    prefix = {"door": "D", "window": "W", "opening": "O"}
    out = {"doors": [], "windows": [], "openings": []}
    plural = {"door": "doors", "window": "windows", "opening": "openings"}
    counters = {"door": 0, "window": 0, "opening": 0}
    for key in sorted(groups, key=lambda k: (k[0], -k[1], -k[2], k[3])):
        grp = groups[key]
        counters[grp["kind"]] += 1
        grp["mark"] = f"{prefix[grp['kind']]}{counters[grp['kind']]}"
        out[plural[grp["kind"]]].append(grp)

    rooms = []
    for lv in levels:
        for r in lv["rooms"]:
            x0, y0, x1, y1 = r["net"]
            rooms.append({"level": lv["index"], "name": r["name"], "type": r["type"],
                          "length_m": round((x1 - x0) / 1000, 2), "width_m": round((y1 - y0) / 1000, 2),
                          "area_m2": r["area_m2"]})

    ext_len = int_len = ext_area = int_area = 0.0
    for lv in levels:
        for w in lv["walls"]:
            length = abs(w["b"][0] - w["a"][0]) + abs(w["b"][1] - w["a"][1])
            gross = length * g["h_w"]
            cut = sum(o["width"] * o["height"] for o in w["openings"])
            if w["kind"] == "ext":
                ext_len += length
                ext_area += gross - cut
            else:
                int_len += length
                int_area += gross - cut
    roof = model["roof"]
    slab_area = g["W"] * g["D"] / 1e6
    void = sum((lv["stair"]["x1"] - lv["stair"]["x0"]) * (lv["stair"]["y1"] - lv["stair"]["y0"]) / 1e6
               for lv in levels if lv["stair"])
    if roof["type"] == "gable":
        rise = (g["D"] / 2) * math.tan(math.radians(roof["pitch_deg"]))
        slope = math.hypot(g["D"] / 2 + roof["overhang_mm"], rise + roof["overhang_mm"] * math.tan(math.radians(roof["pitch_deg"])))
        roof_area = 2 * slope * (g["W"] + 2 * roof["overhang_mm"]) / 1e6
    else:
        roof_area = slab_area
    quantities = {
        "note": "Approximate concept quantities, not a bill of quantities.",
        "levels": len(levels),
        "gross_floor_area_m2": round(slab_area * len(levels) - void, 1),
        "external_wall_length_m": round(ext_len / 1000, 1), "external_wall_area_m2": round(ext_area / 1e6, 1),
        "internal_wall_length_m": round(int_len / 1000, 1), "internal_wall_area_m2": round(int_area / 1e6, 1),
        "slab_area_m2": round(slab_area * (len(levels) + 1) - void, 1),
        "roof_area_m2": round(roof_area, 1),
        "doors": sum(x["count"] for x in out["doors"]), "windows": sum(x["count"] for x in out["windows"]),
    }
    return {**out, "rooms": rooms, "quantities": quantities}


def schedule_csv(sched: dict[str, Any]) -> str:
    lines = ["MARK,TYPE,WIDTH_mm,HEIGHT_mm,SILL_mm,COUNT,LOCATIONS"]
    for kind in ("doors", "windows", "openings"):
        for r in sched[kind]:
            lines.append(f'{r["mark"]},{r["kind"]},{r["width"]},{r["height"]},{r["sill"]},{r["count"]},"{"; ".join(r["locations"])}"')
    lines += ["", "LEVEL,ROOM,TYPE,LENGTH_m,WIDTH_m,AREA_m2"]
    for r in sched["rooms"]:
        lines.append(f'{r["level"]},{r["name"]},{r["type"]},{r["length_m"]},{r["width_m"]},{r["area_m2"]}')
    return "\n".join(lines) + "\n"
