"""
Turn a laid-out building into 3D parts: one feature-tree part per element group (walls of each level,
each floor slab, each stair, the roof). Every part is a normal `feature_tree` part, so it goes through
the same strict validation and builder as any other NitoCAD part, and can be used as an assembly.

Wall geometry: each wall is one extrude of a closed profile in its own vertical plane; door openings
are notches in the bottom edge of the outline, windows are holes. Sketch-plane conventions are
those of feature_tree.py (XZ extrudes toward -Y, YZ toward +X).

CONCEPT geometry (massing, walls, slabs, stairs, roof). It does not include foundations beyond a
ground slab, structure design, services, finishes or furniture.
"""
from __future__ import annotations

import math
from typing import Any

ROOF_THICKNESS = 150.0


def _wall_feature(w: dict[str, Any], z0: float, h: float, first: bool) -> dict[str, Any]:
    t, a, b = w["t"], w["a"], w["b"]
    if w["orient"] == "x":
        lo, hi, origin = a[0], b[0], a[0]
        plane, offset = "XZ", -(a[1] + t / 2)
    else:
        lo, hi, origin = a[1], b[1], a[1]
        plane, offset = "YZ", a[0] - t / 2
    pts: list[list[float]] = [[lo, z0]]
    holes: list[dict[str, Any]] = []
    for o in sorted(w.get("openings", []), key=lambda o: o["offset"]):
        d0 = origin + o["offset"]
        d1 = d0 + o["width"]
        if o["kind"] in ("door", "opening"):
            top = z0 + o["height"]
            pts += [[d0, z0], [d0, top], [d1, top], [d1, z0]]
        else:
            holes.append({"type": "rect", "width": o["width"], "height": o["height"],
                          "center": [d0 + o["width"] / 2, z0 + o["sill"] + o["height"] / 2]})
    pts += [[hi, z0], [hi, z0 + h], [lo, z0 + h]]
    profile: dict[str, Any] = {"type": "polygon", "points": pts}
    if holes:
        profile["holes"] = holes
    f: dict[str, Any] = {"id": w["id"], "op": "extrude", "plane": plane, "offset": offset, "profile": profile,
                         "distance": t}
    if not first:
        f["mode"] = "add"
    return f


def _part(name: str, features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "part_type": "feature_tree", "parameters": {"features": features},
            "position": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0], "mate": None}


def _stair_feature(st: dict[str, Any]) -> dict[str, Any]:
    n, r, t, x0, z0 = st["risers"], st["riser"], st["tread"], st["x0"], st["z0"]
    pts = [[x0, z0]]
    for i in range(1, n + 1):
        xi = x0 + (i - 1) * t
        pts.append([xi, z0 + i * r])
        if i < n:
            pts.append([xi + t, z0 + i * r])
    pts.append([x0 + (n - 1) * t, z0])
    return {"id": "flight", "op": "extrude", "plane": "XZ", "offset": -st["y1"],
            "profile": {"type": "polygon", "points": pts}, "distance": st["width"]}


def _slab_feature(g: dict[str, Any], z_bottom: float, void: dict[str, Any] | None) -> dict[str, Any]:
    prof: dict[str, Any] = {"type": "rect", "width": g["W"], "height": g["D"], "center": [g["W"] / 2, g["D"] / 2]}
    if void:
        prof["holes"] = [{"type": "rect", "width": void["x1"] - void["x0"], "height": void["y1"] - void["y0"],
                          "center": [(void["x0"] + void["x1"]) / 2, (void["y0"] + void["y1"]) / 2]}]
    return {"id": "slab", "op": "extrude", "plane": "XY", "offset": z_bottom, "profile": prof, "distance": g["slab"]}


def roof_polygon(g: dict[str, Any], roof: dict[str, Any], z_base: float) -> list[list[float]]:
    """Roof cross-section in (y, z): a thick gable band, ridge along x."""
    D, ov = g["D"], roof["overhang_mm"]
    tan = math.tan(math.radians(roof["pitch_deg"]))
    z_e = z_base - ov * tan
    z_r = z_base + (D / 2) * tan
    return [[-ov, z_e], [D / 2, z_r], [D + ov, z_e], [D + ov, z_e - ROOF_THICKNESS],
            [D / 2, z_r - ROOF_THICKNESS], [-ov, z_e - ROOF_THICKNESS]]


def gable_triangle(g: dict[str, Any], roof: dict[str, Any], z_base: float) -> list[list[float]]:
    """Gable end wall in (y, z); its sloping sides sit inside the roof band so the two fuse."""
    D = g["D"]
    tan = math.tan(math.radians(roof["pitch_deg"]))
    return [[0.0, z_base], [D, z_base], [D / 2, z_base + (D / 2) * (tan - 2 * ROOF_THICKNESS / D)]]


def _roof_feature(g: dict[str, Any], roof: dict[str, Any], z_base: float) -> dict[str, Any]:
    ov = roof["overhang_mm"]
    return {"id": "roof", "op": "extrude", "plane": "YZ", "offset": -ov,
            "profile": {"type": "polygon", "points": roof_polygon(g, roof, z_base)}, "distance": g["W"] + 2 * ov}


def _gable_walls(g: dict[str, Any], roof: dict[str, Any], z_base: float) -> list[dict[str, Any]]:
    tri = gable_triangle(g, roof, z_base)
    e = g["e"]
    return [{"id": "gable_west", "op": "extrude", "plane": "YZ", "offset": 0.0, "profile": {"type": "polygon", "points": tri},
             "distance": e},
            {"id": "gable_east", "op": "extrude", "plane": "YZ", "offset": g["W"] - e, "mode": "add",
             "profile": {"type": "polygon", "points": tri}, "distance": e}]


def build_parts(model: dict[str, Any]) -> list[dict[str, Any]]:
    g, levels, roof = model["geometry"], model["levels"], model["roof"]
    n = len(levels)
    parts = [_part("ground_slab", [_slab_feature(g, -g["slab"], None)])]
    for k, lv in enumerate(levels):
        z0 = k * g["fh"]
        feats = [_wall_feature(w, z0, g["h_w"], first=(j == 0)) for j, w in enumerate(lv["walls"])]
        parts.append(_part(f"level_{k}_walls", feats))
        void = lv["stair"] if lv["stair"] else None
        parts.append(_part(f"slab_{k + 1}", [_slab_feature(g, z0 + g["h_w"], void)]))
        if lv["stair"]:
            parts.append(_part(f"stair_{k}", [_stair_feature(lv["stair"])]))
    if roof["type"] == "gable":
        west, east = _gable_walls(g, roof, n * g["fh"])
        east.pop("mode", None)  # each end wall is its own connected solid, so it starts a new body
        parts.append(_part("gable_west", [west]))
        parts.append(_part("gable_east", [east]))
        parts.append(_part("roof", [_roof_feature(g, roof, n * g["fh"])]))
    return parts
