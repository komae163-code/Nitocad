"""
Drawing builders: floor plans, elevations and sections, as neutral Sheets (see sheet.py).

Everything is derived from the building model, never from an LLM. Walls are drawn as poche pieces
with real gaps at doors and windows (not white patches), so the DXF is usable in CAD.
"""
from __future__ import annotations

import math
from typing import Any

from .model3d import ROOF_THICKNESS, gable_triangle, roof_polygon
from .sheet import Sheet

WALL_FILL = "#2b2b2b"
CUT_FILL = "#2b2b2b"
SLAB_FILL = "#6b6b6b"
STAIR_FILL = "#8a8a8a"
ROOF_FILL = "#b9695a"
GLASS = "#cfe6ff"


def _pieces(a0: float, a1: float, spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out, cur = [], a0
    for s0, s1 in sorted(spans):
        if s0 > cur:
            out.append((cur, s0))
        cur = max(cur, s1)
    if cur < a1:
        out.append((cur, a1))
    return out


def _wall_rects(w: dict[str, Any]) -> list[tuple[float, float, float, float]]:
    t = w["t"]
    spans_along = []
    origin = w["a"][0] if w["orient"] == "x" else w["a"][1]
    for o in w["openings"]:
        spans_along.append((origin + o["offset"], origin + o["offset"] + o["width"]))
    if w["orient"] == "x":
        a0, a1 = sorted((w["a"][0], w["b"][0]))
        y = w["a"][1]
        return [(s0, y - t / 2, s1, y + t / 2) for s0, s1 in _pieces(a0, a1, spans_along)]
    a0, a1 = sorted((w["a"][1], w["b"][1]))
    x = w["a"][0]
    return [(x - t / 2, s0, x + t / 2, s1) for s0, s1 in _pieces(a0, a1, spans_along)]


def section_defs(model: dict[str, Any]) -> list[dict[str, Any]]:
    g = model["geometry"]
    st = next((lv["stair"] for lv in model["levels"] if lv["stair"]), None)
    a_at = (st["y0"] + st["y1"]) / 2 if st else (g["yc0"] + g["yc1"]) / 2
    south = [r for r in model["levels"][0]["rooms"] if r["band"] == "south"]
    centres = [(r["net"][0] + r["net"][2]) / 2 for r in south[1:] + south[:1]]
    keep_clear = (st["x0"] - 400, st["x1"] + 400) if st else None
    b_at = next((c for c in centres if not keep_clear or not (keep_clear[0] <= c <= keep_clear[1])), g["W"] * 0.3)
    return [{"id": "A-A", "axis": "y", "at": a_at, "label": "Longitudinal section through the stair"
             if st else "Longitudinal section through the corridor"},
            {"id": "B-B", "axis": "x", "at": b_at, "label": "Cross section"}]


# ------------------------------------------------------------------------------------ plan
def plan_sheet(model: dict[str, Any], level: int, furniture: list[dict[str, Any]] | None = None,
               sheet_no: str | None = None) -> Sheet:
    g, levels = model["geometry"], model["levels"]
    lv = levels[level]
    W, D = g["W"], g["D"]
    name = model["spec"]["name"]
    title = "GROUND FLOOR PLAN" if level == 0 else f"LEVEL {level} PLAN"
    sh = Sheet(title, name, sheet_no or f"A-10{level + 1}")

    for w in lv["walls"]:
        for x0, y0, x1, y1 in _wall_rects(w):
            sh.rect(x0, y0, x1, y1, "A-WALL", WALL_FILL)

    for w in lv["walls"]:
        t = w["t"]
        origin = w["a"][0] if w["orient"] == "x" else w["a"][1]
        for o in w["openings"]:
            d0, d1 = origin + o["offset"], origin + o["offset"] + o["width"]
            if w["orient"] == "x":
                yw = w["a"][1]
                if o["kind"] == "window":
                    for dy in (-t * 0.3, 0.0, t * 0.3):
                        sh.line((d0, yw + dy), (d1, yw + dy), "A-GLAZ")
                    sh.line((d0, yw - t / 2), (d0, yw + t / 2), "A-GLAZ")
                    sh.line((d1, yw - t / 2), (d1, yw + t / 2), "A-GLAZ")
                elif o["kind"] == "door" and o["swing"]:
                    into = o["swing"]["into"][1]
                    sh.line((d0, yw), (d0, yw + into * o["width"]), "A-DOOR")
                    sh.arc((d0, yw), o["width"], 0 if into > 0 else 270, 90 if into > 0 else 360, "A-DOOR")
                    sh.line((d0, yw - t / 2), (d0, yw + t / 2), "A-DOOR")
                    sh.line((d1, yw - t / 2), (d1, yw + t / 2), "A-DOOR")
                else:
                    sh.line((d0, yw - t / 2), (d0, yw + t / 2), "A-DOOR")
                    sh.line((d1, yw - t / 2), (d1, yw + t / 2), "A-DOOR")
            else:
                xw = w["a"][0]
                if o["kind"] == "window":
                    for dx in (-t * 0.3, 0.0, t * 0.3):
                        sh.line((xw + dx, d0), (xw + dx, d1), "A-GLAZ")
                    sh.line((xw - t / 2, d0), (xw + t / 2, d0), "A-GLAZ")
                    sh.line((xw - t / 2, d1), (xw + t / 2, d1), "A-GLAZ")

    st = lv["stair"] or (levels[level - 1]["stair"] if level > 0 else None)
    if st:
        layer = "A-STRS"
        sh.rect(st["x0"], st["y0"], st["x1"], st["y1"], layer)
        for n in range(1, st["risers"] - 1):
            x = st["x0"] + n * st["tread"]
            sh.line((x, st["y0"]), (x, st["y1"]), layer)
        ym = (st["y0"] + st["y1"]) / 2
        up = bool(lv["stair"])
        xa, xb = (st["x0"] + 150, st["x1"] - 150) if up else (st["x1"] - 150, st["x0"] + 150)
        sh.line((xa, ym), (xb, ym), "A-STRS-ARRW")
        sgn = 1 if xb > xa else -1
        sh.poly([(xb, ym), (xb - sgn * 180, ym + 70), (xb - sgn * 180, ym - 70)], "A-STRS-ARRW", True, "#b03a2e")
        sh.text(((st["x0"] + st["x1"]) / 2, ym + 160), "UP" if up else "DN", 2.4, "A-STRS-ARRW")

    if furniture:
        for it in furniture:
            if it["level"] == level:
                sh.rect(it["x0"], it["y0"], it["x1"], it["y1"], "A-FURN")
                if it.get("symbol"):
                    for a, b in it["symbol"]:
                        sh.line(a, b, "A-FURN")

    for r in lv["rooms"]:
        x0, y0, x1, y1 = r["net"]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if r["type"] == "stair_hall" and st:
            cy = y0 + (st["y0"] - y0) / 2
        sh.text((cx, cy + 250), r["name"], 3.0, bold=True)
        sh.text((cx, cy - 50), f"{(x1 - x0) / 1000:.2f} x {(y1 - y0) / 1000:.2f} m", 2.0)
        sh.text((cx, cy - 300), f"{r['area_m2']:.1f} m2", 2.2)

    sh.dim_h(0, W, 0, f"{W:,.0f}", -900)
    sh.dim_v(0, D, W + 900, f"{D:,.0f}")
    if level == 0:
        for sd in section_defs(model):
            if sd["axis"] == "y":
                y = sd["at"]
                sh.line((-1400, y), (W + 1400, y), "A-ANNO-SECT")
                for x in (-1400, W + 1400):
                    sh.poly([(x, y + 700), (x - 160, y + 300), (x + 160, y + 300)], "A-ANNO-SECT", True, "#b03a2e")
                    sh.text((x, y - 450), sd["id"][0], 3.5, "A-ANNO-SECT", bold=True)
            else:
                x = sd["at"]
                sh.line((x, -1400), (x, D + 1400), "A-ANNO-SECT")
                for y in (-1400, D + 1400):
                    sgn = 1 if y > 0 else -1
                    sh.poly([(x + 700, y), (x + 300, y - sgn * 160), (x + 300, y + sgn * 160)], "A-ANNO-SECT", True, "#b03a2e")
                    sh.text((x - 450, y), sd["id"][0], 3.5, "A-ANNO-SECT", bold=True)
    sh.poly([(W + 2600, D - 200), (W + 2800, D - 1200), (W + 2600, D - 900), (W + 2400, D - 1200)], "A-ANNO-TEXT", True, "#222222")
    sh.text((W + 2600, D - 1700), "N", 3.5, bold=True)
    return sh


# --------------------------------------------------------------------------------- elevation
_FACADE = {"south": ("ext_s", "x"), "north": ("ext_n", "x"), "east": ("ext_e", "y"), "west": ("ext_w", "y")}


def _facade_u(model: dict[str, Any], side: str, c: float, w: float) -> tuple[float, float]:
    g = model["geometry"]
    lo, hi = c - w / 2, c + w / 2
    if side in ("south", "east"):
        return lo, hi
    length = g["W"] if side == "north" else g["D"]
    return length - hi, length - lo


def elevation_sheet(model: dict[str, Any], side: str, sheet_no: str | None = None) -> Sheet:
    g, levels, roof = model["geometry"], model["levels"], model["roof"]
    wall_id, _ = _FACADE[side]
    length = g["W"] if side in ("south", "north") else g["D"]
    n = len(levels)
    z_top = n * g["fh"]
    sh = Sheet(f"{side.upper()} ELEVATION", model["spec"]["name"], sheet_no or f"A-2{['south', 'east', 'north', 'west'].index(side) + 1:02d}")

    sh.line((-2500, 0), (length + 2500, 0), "A-GRND")
    sh.rect(0, 0, length, z_top, "A-ELEV")
    for k in range(1, n):
        sh.line((0, k * g["fh"]), (length, k * g["fh"]), "A-ELEV-BEYOND")

    for lv in levels:
        for o in lv["openings"]:
            if o["wall"] != wall_id:
                continue
            u0, u1 = _facade_u(model, side, o["center_along"], o["width"])
            z0 = lv["elevation"] + o["sill"]
            z1 = z0 + o["height"]
            if o["kind"] == "window":
                sh.rect(u0, z0, u1, z1, "A-GLAZ", GLASS)
                sh.line(((u0 + u1) / 2, z0), ((u0 + u1) / 2, z1), "A-GLAZ")
                sh.line((u0, (z0 + z1) / 2), (u1, (z0 + z1) / 2), "A-GLAZ")
            else:
                sh.rect(u0, z0, u1, z1, "A-DOOR", "#b9a07a")

    if roof["type"] == "gable":
        tan = math.tan(math.radians(roof["pitch_deg"]))
        ov = roof["overhang_mm"]
        if side in ("south", "north"):
            z_e = z_top - ov * tan
            z_r = z_top + (g["D"] / 2) * tan
            sh.poly([(-ov, z_e - ROOF_THICKNESS), (length + ov, z_e - ROOF_THICKNESS), (length + ov, z_r), (-ov, z_r)],
                    "A-ROOF", True, ROOF_FILL)
        else:
            poly = [(p[0], p[1]) for p in roof_polygon(g, roof, z_top)]
            sh.poly(poly, "A-ROOF", True, ROOF_FILL)
            tri = gable_triangle(g, roof, z_top)
            sh.poly([(p[0], p[1]) for p in tri], "A-ELEV", True, None)
        z_ridge = z_top + (g["D"] / 2) * tan
    else:
        z_ridge = z_top

    for k in range(n + 1):
        z = k * g["fh"]
        sh.line((length + 600, z), (length + 1500, z), "A-ANNO-DIMS")
        sh.text((length + 1600, z), f"+{z / 1000:.3f}" if z else "+0.000", 2.2, "A-ANNO-DIMS", "start")
    sh.text((length + 1600, z_ridge), f"RIDGE +{z_ridge / 1000:.3f}" if roof["type"] == "gable" else "", 2.2, "A-ANNO-DIMS", "start")
    sh.dim_v(0, z_top, -1500, f"{z_top:,.0f}")
    return sh


# ----------------------------------------------------------------------------------- section
def section_sheet(model: dict[str, Any], sd: dict[str, Any], sheet_no: str | None = None) -> Sheet:
    g, levels, roof = model["geometry"], model["levels"], model["roof"]
    axis, at = sd["axis"], sd["at"]
    W, D = g["W"], g["D"]
    along = W if axis == "y" else D          # horizontal extent of the section
    n = len(levels)
    z_top = n * g["fh"]
    sh = Sheet(f"SECTION {sd['id']}", model["spec"]["name"], sheet_no or f"A-3{0 if sd['id'] == 'A-A' else 1}1",
               note=sd["label"] + " | CONCEPT DESIGN. NOT FOR CONSTRUCTION. Compliance: UNKNOWN.")
    sh.line((-2500, 0), (along + 2500, 0), "A-GRND")
    sh.rect(0, -g["slab"], along, 0, "A-SLAB", SLAB_FILL)

    for lv in levels:
        k = lv["index"]
        z0 = k * g["fh"]
        for w in lv["walls"]:
            if (axis == "y" and w["orient"] == "y" and w["a"][1] - 1 <= at <= w["b"][1] + 1) or \
               (axis == "x" and w["orient"] == "x" and w["a"][0] - 1 <= at <= w["b"][0] + 1):
                centre = w["a"][0] if axis == "y" else w["a"][1]
                u0, u1 = centre - w["t"] / 2, centre + w["t"] / 2
                origin = w["a"][1] if axis == "y" else w["a"][0]
                cuts: list[tuple[float, float]] = []
                for o in w["openings"]:
                    d0 = origin + o["offset"]
                    if d0 <= at <= d0 + o["width"]:
                        cuts.append((o["sill"], o["sill"] + o["height"]))
                z = 0.0
                for c0, c1 in sorted(cuts) + [(g["h_w"], g["h_w"])]:
                    if c0 > z:
                        sh.rect(u0, z0 + z, u1, z0 + c0, "A-WALL-CUT", CUT_FILL)
                    z = max(z, c1)
        slab_z0, slab_z1 = z0 + g["h_w"], z0 + g["fh"]
        st = lv["stair"]
        void = None
        if st and ((axis == "y" and st["y0"] <= at <= st["y1"]) or (axis == "x" and st["x0"] <= at <= st["x1"])):
            void = (st["x0"], st["x1"]) if axis == "y" else (st["y0"], st["y1"])
        if void:
            sh.rect(0, slab_z0, void[0], slab_z1, "A-SLAB", SLAB_FILL)
            sh.rect(void[1], slab_z0, along, slab_z1, "A-SLAB", SLAB_FILL)
        else:
            sh.rect(0, slab_z0, along, slab_z1, "A-SLAB", SLAB_FILL)
        if st and axis == "y" and st["y0"] <= at <= st["y1"]:
            pts = [(st["x0"], z0)]
            for i in range(1, st["risers"] + 1):
                xi = st["x0"] + (i - 1) * st["tread"]
                pts.append((xi, z0 + i * st["riser"]))
                if i < st["risers"]:
                    pts.append((xi + st["tread"], z0 + i * st["riser"]))
            pts.append((st["x0"] + (st["risers"] - 1) * st["tread"], z0))
            sh.poly(pts, "A-STRS", True, STAIR_FILL)
        elif st and axis == "x" and st["x0"] <= at <= st["x1"]:
            i = min(st["risers"], int((at - st["x0"]) // st["tread"]) + 1)
            sh.rect(st["y0"], z0, st["y1"], z0 + i * st["riser"], "A-STRS", STAIR_FILL)
        sh.text((along + 1600, z0), f"FFL +{z0 / 1000:.3f}", 2.2, "A-ANNO-DIMS", "start")
        for r in lv["rooms"]:
            x0, y0, x1, y1 = r["net"]
            u_mid = (x0 + x1) / 2 if axis == "y" else (y0 + y1) / 2
            if r["type"] == "stair_hall" and axis == "y" and lv["stair"]:
                u_mid = (lv["stair"]["x1"] + x1) / 2          # the clear landing beside the stair
            if (axis == "y" and y0 <= at <= y1) or (axis == "x" and x0 <= at <= x1):
                sh.text((u_mid, z0 + 1500), r["name"], 2.4)

    if roof["type"] == "gable":
        tan = math.tan(math.radians(roof["pitch_deg"]))
        ov = roof["overhang_mm"]
        if axis == "y":
            zs = z_top + min(at, D - at) * tan
            sh.rect(-ov, zs - ROOF_THICKNESS, W + ov, zs, "A-ROOF", ROOF_FILL)
            hi = z_top + min(at, D - at) * (tan - 2 * ROOF_THICKNESS / D)
            for x0, x1 in ((0, g["e"]), (W - g["e"], W)):
                sh.rect(x0, z_top, x1, hi, "A-WALL-CUT", CUT_FILL)
        else:
            sh.poly([(p[0], p[1]) for p in roof_polygon(g, roof, z_top)], "A-ROOF", True, ROOF_FILL)
            if at <= g["e"] or at >= W - g["e"]:
                sh.poly([(p[0], p[1]) for p in gable_triangle(g, roof, z_top)], "A-WALL-CUT", True, CUT_FILL)
    sh.dim_v(0, z_top, -1500, f"{z_top:,.0f}")
    sh.text((along / 2, -g["slab"] - 900), f"SECTION {sd['id']}", 4.0, bold=True)
    return sh
