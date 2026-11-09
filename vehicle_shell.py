"""
Automatic body shells: a vehicle body is hollowed by the expander, so no model has to remember to do it.

A body written as a solid feature tree (a car profile extruded across its width) has the volume and mass of a
block of steel. Instead of asking the model to add an 'interior' cut and hoping it does, expand_vehicle calls
auto_shell() on every part that is named like a body. Each additive extrude gets a matching cut: the same
profile moved inward by one wall thickness, and shortened by a wall at each end. What is left is a sealed
sheet-metal skin of the same outside shape.

    shelled, problems = auto_shell(parts)          # edits the parts in place

What it handles
  * feature_tree bodies whose additive features are all extrudes with a polygon, rect or circle profile.
  * simple_box bodies, which get hollow=True and a wall (the box template leaves the top open, as a tub).

What it does not do, and says so in `problems` instead of guessing
  * Concave polygon profiles. The inward offset of a concave outline needs a polygon library; a body like that
    is reported and left alone, so the plan is sent back to be split into panels.
  * Revolves, lofts and sweeps in the body, or a pattern or mirror of an additive feature.
  * It does not turn one body into doors, a roof and a hood. That is the job of the car_body module and of
    vehicle_decompose, which still rejects a one-piece body with no panels.

Each additive extrude is hollowed on its own, so a cabin that sits on a lower body gets its own sealed cavity
with a thin floor between the two. Mass and volume are credible; the interior is not one connected room.

Pure Python. Nothing here builds geometry: the cut features it writes are ordinary extrude cuts that the feature
builder runs like any other, and they are checked by the same validator as the rest of the tree. Not yet run
against a real CAD kernel.
"""
from __future__ import annotations

import math
import re
from typing import Any

WALL_MIN_MM = 1.5            # thinnest wall written automatically
WALL_MAX_MM = 3.0            # thickest (sheet metal body: 2 to 3 mm)
WALL_PER_MM = 0.0006         # wall = 0.06 % of the body's longest side, inside the limits above
MIN_CAVITY_MM = 1.0          # a cut thinner than this is not worth writing

BODY_NAME = re.compile(r"(^|_)(body|hull|shell|cabin|fuselage|bodywork|chassis)(_|$)", re.I)
HOLLOW_ID = re.compile(r"cavity|interior|hollow|shell|inner|inside|pocket", re.I)
_BASE_OPS = ("extrude", "revolve", "loft", "sweep")


def default_wall(extent_mm: float) -> float:
    """Wall thickness for a body whose longest side is `extent_mm`: 2.5 mm for a 4.2 m car, 1.5 mm for a toy."""
    return round(min(WALL_MAX_MM, max(WALL_MIN_MM, extent_mm * WALL_PER_MM)), 3)


def has_hollow(features: list[dict[str, Any]]) -> bool:
    """True when a cut feature with an inside-like id already removes the body's inside."""
    return any(isinstance(f, dict) and f.get("mode") == "cut" and HOLLOW_ID.search(str(f.get("id", "")))
               for f in features)


# ----------------------------------------------------------------------------------------------- geometry
def _area2(pts: list[tuple[float, float]]) -> float:
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts)))


def polygon_area(points: list[Any]) -> float:
    pts = [(float(p[0]), float(p[1])) for p in points]
    return abs(_area2(pts)) / 2.0 if len(pts) >= 3 else 0.0


def is_convex(points: list[Any]) -> bool:
    pts = [(float(p[0]), float(p[1])) for p in points]
    n = len(pts)
    if n < 3:
        return False
    sign = 0
    for i in range(n):
        a, b, c = pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cr) < 1e-9:
            continue
        s = 1 if cr > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return sign != 0


def _clip(poly: list[tuple[float, float]], a: tuple[float, float], nrm: tuple[float, float], t: float
          ) -> list[tuple[float, float]]:
    """Keep the part of a convex polygon at least t from the line through a, on the side nrm points to."""
    def dist(p: tuple[float, float]) -> float:
        return (p[0] - a[0]) * nrm[0] + (p[1] - a[1]) * nrm[1] - t
    out: list[tuple[float, float]] = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        dc, dp = dist(cur), dist(prev)
        if dc >= 0:
            if dp < 0:
                k = dp / (dp - dc)
                out.append((prev[0] + k * (cur[0] - prev[0]), prev[1] + k * (cur[1] - prev[1])))
            out.append(cur)
        elif dp >= 0:
            k = dp / (dp - dc)
            out.append((prev[0] + k * (cur[0] - prev[0]), prev[1] + k * (cur[1] - prev[1])))
    return out


def inset_polygon(points: list[Any], t: float) -> list[list[float]] | None:
    """The convex polygon moved inward by t on every side, or None when it is concave or too small to take it."""
    if not is_convex(points) or t <= 0:
        return None
    pts = [(float(p[0]), float(p[1])) for p in points]
    if _area2(pts) < 0:
        pts = pts[::-1]                                   # counter-clockwise, so the left side is the inside
    poly = pts
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy)
        if ln < 1e-9:
            continue
        poly = _clip(poly, a, (-dy / ln, dx / ln), t)
        if len(poly) < 3:
            return None
    clean: list[tuple[float, float]] = []
    for p in poly:
        if not clean or math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-6:
            clean.append(p)
    if len(clean) > 1 and math.hypot(clean[0][0] - clean[-1][0], clean[0][1] - clean[-1][1]) <= 1e-6:
        clean.pop()
    if len(clean) < 3 or _area2(clean) / 2.0 < 1e-3:
        return None
    return [[round(x, 4), round(y, 4)] for x, y in clean]


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _mode(f: dict[str, Any], index: int) -> str:
    return str(f.get("mode") or ("new" if index == 0 else "add"))


def _inset_profile(prof: Any, t: float) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(prof, dict):
        return None, "its profile is missing"
    if prof.get("holes"):
        return None, "its profile has holes"
    kind = prof.get("type")
    centre = prof.get("center", [0, 0])
    if kind == "polygon":
        pts = prof.get("points")
        if not isinstance(pts, list) or not all(isinstance(p, (list, tuple)) and len(p) == 2 and _num(p[0])
                                                and _num(p[1]) for p in pts):
            return None, "its polygon points are not plain numbers"
        if not is_convex(pts):
            return None, "its outline is concave (the inward offset needs a polygon library)"
        inner = inset_polygon(pts, t)
        if inner is None:
            return None, f"its outline is too small for a {t:g} mm wall"
        return {"type": "polygon", "points": inner, "center": list(centre)}, ""
    if kind == "rect" and _num(prof.get("width")) and _num(prof.get("height")):
        w, h = prof["width"] - 2 * t, prof["height"] - 2 * t
        if w < MIN_CAVITY_MM or h < MIN_CAVITY_MM:
            return None, f"its rectangle is too small for a {t:g} mm wall"
        return {"type": "rect", "width": round(w, 4), "height": round(h, 4), "center": list(centre)}, ""
    if kind == "circle" and _num(prof.get("diameter")):
        d = prof["diameter"] - 2 * t
        if d < MIN_CAVITY_MM:
            return None, f"its circle is too small for a {t:g} mm wall"
        return {"type": "circle", "diameter": round(d, 4), "center": list(centre)}, ""
    return None, f"a {kind!r} profile cannot be offset"


def _extent(features: list[dict[str, Any]]) -> float:
    """Longest side seen across the additive extrudes (profile span or extrusion length), in mm."""
    best = 0.0
    for i, f in enumerate(features):
        if not isinstance(f, dict) or f.get("op") != "extrude" or _mode(f, i) not in ("new", "add"):
            continue
        prof = f.get("profile") or {}
        if _num(f.get("distance")):
            best = max(best, float(f["distance"]))
        if prof.get("type") == "polygon" and isinstance(prof.get("points"), list):
            try:
                xs = [float(p[0]) for p in prof["points"]]
                ys = [float(p[1]) for p in prof["points"]]
                best = max(best, max(xs) - min(xs), max(ys) - min(ys))
            except (TypeError, ValueError, IndexError):
                pass
        elif prof.get("type") == "rect":
            best = max(best, float(prof.get("width") or 0), float(prof.get("height") or 0))
        elif prof.get("type") == "circle":
            best = max(best, float(prof.get("diameter") or 0))
    return best


def is_sheet(features: list[dict[str, Any]], wall: float) -> bool:
    """True when every additive extrude is thinner than four walls: the part already is a sheet, not a block."""
    dists = [f.get("distance") for i, f in enumerate(features)
             if isinstance(f, dict) and f.get("op") == "extrude" and _mode(f, i) in ("new", "add")]
    return bool(dists) and all(_num(d) and d < 4 * wall for d in dists)


def hollow_features(features: list[dict[str, Any]], wall: float) -> tuple[list[dict[str, Any]], int, str]:
    """(cut features, index to insert them at, reason). The reason is empty when the cuts are good."""
    used = {str(f.get("id")) for f in features if isinstance(f, dict)}
    additive: set[str] = set()
    cuts: list[dict[str, Any]] = []
    last = -1
    for i, f in enumerate(features):
        if not isinstance(f, dict):
            return [], 0, "a feature is not an object"
        op, fid = f.get("op"), str(f.get("id"))
        if op in _BASE_OPS:
            if _mode(f, i) not in ("new", "add"):
                continue
            if op != "extrude":
                return [], 0, f"feature {fid!r} is a {op}, and only extrudes can be hollowed"
            additive.add(fid)
            last = i
            inner, why = _inset_profile(f.get("profile"), wall)
            if inner is None:
                return [], 0, f"feature {fid!r}: {why}"
            d, off = f.get("distance"), f.get("offset", 0)
            if not _num(d) or not _num(off):
                return [], 0, f"feature {fid!r} has a distance or offset that is not a plain number"
            if d - 2 * wall < MIN_CAVITY_MM:
                return [], 0, f"feature {fid!r} is only {d:g} mm thick, too thin for a {wall:g} mm wall"
            sym, rev = bool(f.get("symmetric")), bool(f.get("reverse"))
            # normal-ward walls: a one-sided extrude covers off .. off+d (or off-d .. off when reversed)
            new_off = off if sym else (off - wall if rev else off + wall)
            cid = f"interior_{fid}"
            while cid in used:
                cid += "_x"
            used.add(cid)
            cuts.append({"id": cid, "op": "extrude", "plane": f.get("plane", "XY"), "offset": new_off,
                         "symmetric": sym, "reverse": rev, "distance": round(d - 2 * wall, 4), "mode": "cut",
                         "profile": inner})
        elif op in ("pattern", "mirror") and str(f.get("feature")) in additive:
            return [], 0, f"feature {fid!r} repeats an additive feature, which cannot be hollowed automatically"
    if not cuts:
        return [], 0, "it has no additive extrude to hollow"
    return cuts, last + 1, ""


def auto_shell(parts: list[dict[str, Any]], wall_mm: float | None = None) -> tuple[list[str], list[str]]:
    """Hollow every body-named part that is still solid. Edits `parts` in place.
    Returns (names hollowed, plain-language problems for the ones that could not be)."""
    shelled: list[str] = []
    problems: list[str] = []
    for p in parts:
        name = str(p.get("name", ""))
        if not BODY_NAME.search(name):
            continue
        ptype = p.get("part_type") or p.get("type")
        prm = p.get("parameters")
        if not isinstance(prm, dict):
            continue
        if ptype == "feature_tree":
            feats = prm.get("features")
            if not isinstance(feats, list) or has_hollow(feats):
                continue
            wall = float(wall_mm) if _num(wall_mm) and wall_mm > 0 else default_wall(_extent(feats))
            if is_sheet(feats, wall):
                continue
            cuts, at, why = hollow_features(feats, wall)
            if why:
                problems.append(f"'{name}' could not be hollowed automatically: {why}")
                continue
            p["parameters"] = {**prm, "features": feats[:at] + cuts + feats[at:]}
            shelled.append(name)
        elif ptype == "simple_box":
            if prm.get("hollow") or prm.get("has_lid"):
                continue
            dims = [prm.get(k) for k in ("length_mm", "width_mm", "height_mm")]
            if not all(_num(d) for d in dims):
                continue
            wall = float(wall_mm) if _num(wall_mm) and wall_mm > 0 else default_wall(max(dims))
            if wall * 2 + MIN_CAVITY_MM > min(dims):
                problems.append(f"'{name}' is too small for a {wall:g} mm wall")
                continue
            p["parameters"] = {**prm, "hollow": True, "wall_thickness_mm": wall}
            shelled.append(name)
    return shelled, problems
