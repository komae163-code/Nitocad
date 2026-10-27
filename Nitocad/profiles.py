"""
2D files to flat shapes: DXF, SVG, KiCad boards, Gerber and Excellon drill files.

Each reader returns a Profile: a set of "islands", each an outer boundary with holes in
it, all in millimetres with y pointing up. solids.py turns a Profile plus a thickness into
a 3D solid, and the person can then edit it (thickness, holes, scale, and so on).

This module is plain Python with no CAD kernel, so every reader can be tested alone.

What is and is not read:
  * Lines, arcs, circles, polylines (with bulge arcs) and, for SVG, rectangles, ellipses,
    polygons and paths with curves. Curves become short straight segments (0.02 mm chord
    error), so a curved edge in the solid is a fine polygon, not a true arc. Circles stay
    exact circles.
  * Open paths are joined end to end when the ends meet (0.001 mm). Paths that do not close
    are dropped with a warning.
  * Text, dimensions, hatches, splines, blocks and copper are not shape. They are skipped and
    counted in the warnings.
"""
from __future__ import annotations

import math
import re
import zipfile
import io
from dataclasses import dataclass, field
from typing import Any

JOIN_TOL_MM = 1e-3
CHORD_TOL_MM = 0.02
MAX_POINTS = 200_000

Point = tuple[float, float]


class ProfileError(ValueError):
    """The file cannot be turned into a shape. The message is safe to show."""


# --------------------------------------------------------------------- geometry
def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def signed_area(pts: list[Point]) -> float:
    a = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def point_in_polygon(p: Point, poly: list[Point]) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def arc_points(cx: float, cy: float, r: float, a0: float, a1: float, ccw: bool = True) -> list[Point]:
    """Points along an arc from angle a0 to a1 (radians), turning counter-clockwise or not,
    including both ends. A full turn is a0 == a1 (after wrapping)."""
    if r <= 0:
        return [(cx, cy)]
    sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1) % (2 * math.pi))
    if abs(sweep) < 1e-12:
        sweep = 2 * math.pi if ccw else -2 * math.pi
    # chord error r(1 - cos(step/2)) <= tol
    step = 2 * math.acos(max(0.0, 1.0 - CHORD_TOL_MM / r)) if r > CHORD_TOL_MM else math.pi / 2
    step = max(min(step, math.pi / 6), 1e-3)
    n = max(2, int(math.ceil(abs(sweep) / step)))
    return [(cx + r * math.cos(a0 + sweep * i / n), cy + r * math.sin(a0 + sweep * i / n)) for i in range(n + 1)]


def bulge_arc(p0: Point, p1: Point, bulge: float) -> list[Point]:
    """Points from p0 to p1 along a DXF bulge arc (bulge = tan(angle / 4), positive = ccw)."""
    if abs(bulge) < 1e-12:
        return [p0, p1]
    chord = _dist(p0, p1)
    if chord < 1e-12:
        return [p0]
    theta = 4.0 * math.atan(bulge)                   # included angle, signed
    r = chord / (2.0 * math.sin(abs(theta) / 2.0))
    mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
    h = r * math.cos(abs(theta) / 2.0)
    dx, dy = (p1[0] - p0[0]) / chord, (p1[1] - p0[1]) / chord
    sign = 1.0 if bulge > 0 else -1.0
    cx, cy = mx - sign * dy * h, my + sign * dx * h   # centre is left of the chord for ccw arcs
    return arc_points(cx, cy, r, math.atan2(p0[1] - cy, p0[0] - cx), math.atan2(p1[1] - cy, p1[0] - cx), ccw=bulge > 0)


def three_point_arc(p0: Point, pm: Point, p1: Point) -> list[Point]:
    ax, ay = p0
    bx, by = pm
    cx_, cy_ = p1
    d = 2 * (ax * (by - cy_) + bx * (cy_ - ay) + cx_ * (ay - by))
    if abs(d) < 1e-12:
        return [p0, p1]
    ux = ((ax * ax + ay * ay) * (by - cy_) + (bx * bx + by * by) * (cy_ - ay) + (cx_ * cx_ + cy_ * cy_) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx_ - bx) + (bx * bx + by * by) * (ax - cx_) + (cx_ * cx_ + cy_ * cy_) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0, am, a1 = (math.atan2(p[1] - uy, p[0] - ux) for p in (p0, pm, p1))
    ccw = ((am - a0) % (2 * math.pi)) < ((a1 - a0) % (2 * math.pi))
    return arc_points(ux, uy, r, a0, a1, ccw=ccw)


def chain_paths(paths: list[list[Point]], tol: float = JOIN_TOL_MM) -> tuple[list[list[Point]], int]:
    """Join open polylines end to end. Returns (closed loops, how many paths stayed open)."""
    pool = [list(p) for p in paths if len(p) >= 2]
    loops: list[list[Point]] = []
    open_left = 0
    while pool:
        cur = pool.pop()
        grew = True
        while grew and _dist(cur[0], cur[-1]) > tol:
            grew = False
            for i, other in enumerate(pool):
                if _dist(cur[-1], other[0]) <= tol:
                    cur += other[1:]
                elif _dist(cur[-1], other[-1]) <= tol:
                    cur += list(reversed(other))[1:]
                elif _dist(cur[0], other[-1]) <= tol:
                    cur = other[:-1] + cur
                elif _dist(cur[0], other[0]) <= tol:
                    cur = list(reversed(other))[:-1] + cur
                else:
                    continue
                pool.pop(i)
                grew = True
                break
        if _dist(cur[0], cur[-1]) <= tol and len(cur) >= 4:
            loops.append(cur[:-1])
        else:
            open_left += 1
    return loops, open_left


def _clean_loop(pts: list[Point]) -> list[Point]:
    out: list[Point] = []
    for p in pts:
        if not out or _dist(p, out[-1]) > 1e-9:
            out.append(p)
    if len(out) > 1 and _dist(out[0], out[-1]) <= 1e-9:
        out.pop()
    return out


# ---------------------------------------------------------------------- profile
@dataclass
class Profile:
    """Islands of (outer, holes). Each outline is {"type": "polygon", "points": [[x, y], ...]}
    or {"type": "circle", "cx": x, "cy": y, "r": r}, in millimetres."""
    shapes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = ""
    suggested_thickness_mm: float = 3.0

    def to_json(self) -> dict[str, Any]:
        return {"shapes": self.shapes, "source": self.source, "suggested_thickness_mm": self.suggested_thickness_mm}

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Profile":
        return Profile(shapes=d["shapes"], source=d.get("source", ""), suggested_thickness_mm=d.get("suggested_thickness_mm", 3.0))

    def _outlines(self):
        for s in self.shapes:
            yield s["outer"]
            yield from s["holes"]

    def bbox(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for s in self.shapes:
            o = s["outer"]
            if o["type"] == "circle":
                xs += [o["cx"] - o["r"], o["cx"] + o["r"]]
                ys += [o["cy"] - o["r"], o["cy"] + o["r"]]
            else:
                xs += [p[0] for p in o["points"]]
                ys += [p[1] for p in o["points"]]
        if not xs:
            raise ProfileError("The file has no closed shape to make a part from")
        return min(xs), min(ys), max(xs), max(ys)

    def stats(self) -> dict[str, Any]:
        x0, y0, x1, y1 = self.bbox()
        round_holes: dict[float, int] = {}
        other_holes = 0
        for s in self.shapes:
            for h in s["holes"]:
                if h["type"] == "circle":
                    d = round(2 * h["r"], 3)
                    round_holes[d] = round_holes.get(d, 0) + 1
                else:
                    other_holes += 1
        return {
            "width_mm": x1 - x0, "height_mm": y1 - y0, "islands": len(self.shapes),
            "round_holes": [{"diameter_mm": d, "count": n} for d, n in sorted(round_holes.items())],
            "other_cutouts": other_holes,
        }


def _outline_dict(item: dict[str, Any]) -> dict[str, Any]:
    if item["type"] == "circle":
        return {"type": "circle", "cx": item["cx"], "cy": item["cy"], "r": item["r"]}
    return {"type": "polygon", "points": [[round(x, 6), round(y, 6)] for x, y in item["points"]]}


def build_profile(loops: list[list[Point]], circles: list[tuple[float, float, float]], *, source: str,
                  warnings: list[str] | None = None, thickness: float = 3.0) -> Profile:
    """Sort closed loops and circles into islands: something inside one shape is a hole, and
    something inside that hole is a new island (even-odd nesting)."""
    warnings = list(warnings or [])
    items: list[dict[str, Any]] = []
    for lp in loops:
        lp = _clean_loop(lp)
        if len(lp) >= 3 and abs(signed_area(lp)) > 1e-9:
            items.append({"type": "polygon", "points": lp, "area": abs(signed_area(lp))})
    for cx, cy, r in circles:
        if r > 1e-6:
            items.append({"type": "circle", "cx": cx, "cy": cy, "r": r, "area": math.pi * r * r})
    if not items:
        raise ProfileError("The file has no closed shape to make a part from. Outlines must be closed")
    if sum(len(i.get("points", [])) for i in items) > MAX_POINTS:
        raise ProfileError("The drawing is too detailed")
    items.sort(key=lambda i: -i["area"])

    def probe(i: dict[str, Any]) -> Point:
        return (i["cx"], i["cy"]) if i["type"] == "circle" else i["points"][0]

    def contains(outer: dict[str, Any], inner: dict[str, Any]) -> bool:
        if outer["area"] <= inner["area"]:
            return False
        p = probe(inner)
        if outer["type"] == "circle":
            return math.hypot(p[0] - outer["cx"], p[1] - outer["cy"]) < outer["r"]
        return point_in_polygon(p, outer["points"])

    for idx, it in enumerate(items):
        it["parent"] = None
        for j in range(idx - 1, -1, -1):          # the smallest container is the nearest bigger item
            if contains(items[j], it):
                it["parent"] = j
                break
        it["depth"] = 0 if it["parent"] is None else items[it["parent"]]["depth"] + 1

    shapes: list[dict[str, Any]] = []
    island_of: dict[int, dict[str, Any]] = {}
    for idx, it in enumerate(items):
        if it["depth"] % 2 == 0:
            s = {"outer": _outline_dict(it), "holes": []}
            island_of[idx] = s
            shapes.append(s)
    for idx, it in enumerate(items):
        if it["depth"] % 2 == 1:
            island_of[it["parent"]]["holes"].append(_outline_dict(it))
    return Profile(shapes=shapes, warnings=warnings, source=source, suggested_thickness_mm=thickness)


# ------------------------------------------------------------------------- DXF
_DXF_UNITS_TO_MM = {0: 1.0, 1: 25.4, 2: 304.8, 3: 1609344.0, 4: 1.0, 5: 10.0, 6: 1000.0, 7: 1e6, 8: 25.4e-6, 9: 0.0254,
                    10: 914.4, 13: 1e-3, 14: 100.0, 15: 1e4, 16: 1e5, 17: 1e9}
_SKIPPED_DXF = {"TEXT", "MTEXT", "DIMENSION", "HATCH", "POINT", "LEADER", "MLEADER", "ATTDEF", "ATTRIB", "VIEWPORT", "IMAGE", "SOLID", "3DFACE"}


def _dxf_pairs(text: str) -> list[tuple[int, str]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for i in range(0, len(lines) - 1, 2):
        try:
            out.append((int(lines[i].strip()), lines[i + 1].strip()))
        except ValueError:
            raise ProfileError("This does not look like a text DXF file") from None
    return out


def parse_dxf(text: str) -> Profile:
    if text.lstrip().startswith("AutoCAD Binary DXF"):
        raise ProfileError("Binary DXF files are not supported. Save the drawing as an ASCII DXF")
    pairs = _dxf_pairs(text)
    units = 4
    for i, (c, v) in enumerate(pairs[:-1]):
        if c == 9 and v == "$INSUNITS" and pairs[i + 1][0] == 70:
            try:
                units = int(pairs[i + 1][1])
            except ValueError:
                pass
            break
    k = _DXF_UNITS_TO_MM.get(units, 1.0)
    notes: list[str] = []
    if units == 0:
        notes.append("The drawing has no units set, so its numbers are read as millimetres")

    # entity records: split at every group code 0 inside the ENTITIES section
    ents: list[tuple[str, list[tuple[int, str]]]] = []
    in_entities = False
    cur: tuple[str, list[tuple[int, str]]] | None = None
    i = 0
    while i < len(pairs):
        c, v = pairs[i]
        if c == 0 and v == "SECTION" and i + 1 < len(pairs) and pairs[i + 1] == (2, "ENTITIES"):
            in_entities = True
            i += 2
            continue
        if c == 0 and v == "ENDSEC":
            in_entities = False
        if in_entities:
            if c == 0:
                cur = (v, [])
                ents.append(cur)
            elif cur is not None:
                cur[1].append((c, v))
        i += 1
    if not ents:
        raise ProfileError("No drawing entities were found in the DXF")

    paths: list[list[Point]] = []
    loops: list[list[Point]] = []
    circles: list[tuple[float, float, float]] = []
    skipped: dict[str, int] = {}

    def f(data, code, default=0.0, nth=0):
        vals = [float(v) for c, v in data if c == code]
        return vals[nth] if len(vals) > nth else default

    j = 0
    while j < len(ents):
        etype, data = ents[j]
        j += 1
        try:
            if etype == "LINE":
                paths.append([(f(data, 10) * k, f(data, 20) * k), (f(data, 11) * k, f(data, 21) * k)])
            elif etype == "CIRCLE":
                circles.append((f(data, 10) * k, f(data, 20) * k, f(data, 40) * k))
            elif etype == "ARC":
                cx, cy, r = f(data, 10) * k, f(data, 20) * k, f(data, 40) * k
                paths.append(arc_points(cx, cy, r, math.radians(f(data, 50)), math.radians(f(data, 51)), True))
            elif etype in ("LWPOLYLINE", "POLYLINE"):
                verts: list[tuple[float, float, float]] = []
                closed = int(f(data, 70, 0)) & 1 == 1
                if etype == "LWPOLYLINE":
                    xs = [float(v) for c, v in data if c == 10]
                    ys = [float(v) for c, v in data if c == 20]
                    # bulges attach to the vertex that precedes them, so walk the codes in order
                    cur_v = -1
                    bulges: dict[int, float] = {}
                    for c, v in data:
                        if c == 10:
                            cur_v += 1
                        elif c == 42 and cur_v >= 0:
                            bulges[cur_v] = float(v)
                    verts = [(xs[n] * k, ys[n] * k, bulges.get(n, 0.0)) for n in range(min(len(xs), len(ys)))]
                else:
                    while j < len(ents) and ents[j][0] in ("VERTEX", "SEQEND"):
                        vt, vd = ents[j]
                        j += 1
                        if vt == "VERTEX":
                            verts.append((f(vd, 10) * k, f(vd, 20) * k, f(vd, 42, 0.0)))
                if len(verts) < 2:
                    continue
                pts: list[Point] = []
                last = len(verts) if closed else len(verts) - 1
                for n in range(last):
                    p0, p1 = verts[n], verts[(n + 1) % len(verts)]
                    seg = bulge_arc((p0[0], p0[1]), (p1[0], p1[1]), p0[2])
                    pts += seg if not pts else seg[1:]
                (loops if closed else paths).append(pts)
            elif etype in _SKIPPED_DXF or etype in ("ELLIPSE", "SPLINE", "INSERT", "VERTEX", "SEQEND"):
                skipped[etype] = skipped.get(etype, 0) + 1
        except (ValueError, ZeroDivisionError):
            skipped[etype] = skipped.get(etype, 0) + 1

    joined, open_left = chain_paths(paths)
    loops += joined
    for t, n in skipped.items():
        if t in ("ELLIPSE", "SPLINE", "INSERT"):
            notes.append(f"{n} {t.lower()} entit{'y' if n == 1 else 'ies'} skipped, which this reader does not turn into a shape")
    if open_left:
        notes.append(f"{open_left} open path(s) were dropped because their ends do not meet")
    return build_profile(loops, circles, source="dxf", warnings=notes)


# ------------------------------------------------------------------------- SVG
_UNIT_PX = {"px": 1.0, "": 1.0, "pt": 96.0 / 72.0, "pc": 16.0, "mm": 96.0 / 25.4, "cm": 96.0 / 2.54, "in": 96.0}
_PX_MM = 25.4 / 96.0
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _mat_mul(a: list[float], b: list[float]) -> list[float]:
    return [a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5]]


def _parse_transform(s: str | None) -> list[float]:
    m = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in re.findall(_NUM, args)]
        if name == "translate" and v:
            t = [1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0]
        elif name == "scale" and v:
            t = [v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0]
        elif name == "rotate" and v:
            a = math.radians(v[0])
            ca, sa = math.cos(a), math.sin(a)
            t = [ca, sa, -sa, ca, 0, 0]
            if len(v) >= 3:
                t = _mat_mul(_mat_mul([1, 0, 0, 1, v[1], v[2]], t), [1, 0, 0, 1, -v[1], -v[2]])
        elif name == "matrix" and len(v) == 6:
            t = v
        elif name == "skewX" and v:
            t = [1, 0, math.tan(math.radians(v[0])), 1, 0, 0]
        elif name == "skewY" and v:
            t = [1, math.tan(math.radians(v[0])), 0, 1, 0, 0]
        else:
            continue
        m = _mat_mul(m, [float(x) for x in t])
    return m


def _apply(m: list[float], p: Point) -> Point:
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def _bezier(pts: list[Point], steps: int) -> list[Point]:
    out = []
    n = len(pts) - 1
    for s in range(1, steps + 1):
        t = s / steps
        x = y = 0.0
        for i, p in enumerate(pts):
            b = math.comb(n, i) * (t ** i) * ((1 - t) ** (n - i))
            x += b * p[0]
            y += b * p[1]
        out.append((x, y))
    return out


def _svg_arc(p0: Point, rx: float, ry: float, rot: float, large: int, sweep: int, p1: Point) -> list[Point]:
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rot)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx, cy = cp * cxp - sp * cyp + (p0[0] + p1[0]) / 2, sp * cxp + cp * cyp + (p0[1] + p1[1]) / 2
    ang = lambda ux, uy, vx, vy: math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)  # noqa: E731
    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    n = max(8, int(abs(dth) / (math.pi / 36)) + 1)
    return [(cx + rx * math.cos(th1 + dth * i / n) * cp - ry * math.sin(th1 + dth * i / n) * sp,
             cy + rx * math.cos(th1 + dth * i / n) * sp + ry * math.sin(th1 + dth * i / n) * cp) for i in range(1, n + 1)]


def _path_subpaths(d: str) -> list[tuple[list[Point], bool]]:
    """Subpaths of an SVG path as (points, closed), in user units, curves flattened."""
    toks = re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|" + _NUM, d or "")
    subs: list[tuple[list[Point], bool]] = []
    cur: list[Point] = []
    pos = start = (0.0, 0.0)
    cmd = ""
    last_ctrl: Point | None = None
    i = 0

    def num() -> float:
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def flag() -> int:
        nonlocal i
        t = toks[i]
        if len(t) > 1 and t[0] in "01" and not t.startswith(("0.", "1.")):    # compact flags such as "01"
            toks[i] = t[1:]
            return int(t[0])
        i += 1
        return int(float(t))

    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                if cur:
                    subs.append((cur, True))
                cur = []
                pos = start
                last_ctrl = None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        try:
            if c == "M":
                x, y = num(), num()
                pos = (pos[0] + x, pos[1] + y) if rel else (x, y)
                if len(cur) > 1:
                    subs.append((cur, False))
                cur = [pos]
                start = pos
                cmd = "l" if rel else "L"
            elif c == "L":
                x, y = num(), num()
                pos = (pos[0] + x, pos[1] + y) if rel else (x, y)
                cur.append(pos)
            elif c == "H":
                x = num()
                pos = (pos[0] + x if rel else x, pos[1])
                cur.append(pos)
            elif c == "V":
                y = num()
                pos = (pos[0], pos[1] + y if rel else y)
                cur.append(pos)
            elif c in "CS":
                if c == "C":
                    p1 = (num(), num())
                    if rel:
                        p1 = (pos[0] + p1[0], pos[1] + p1[1])
                else:
                    p1 = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_ctrl else pos
                p2 = (num(), num())
                p3 = (num(), num())
                if rel:
                    p2 = (pos[0] + p2[0], pos[1] + p2[1])
                    p3 = (pos[0] + p3[0], pos[1] + p3[1])
                length = _dist(pos, p1) + _dist(p1, p2) + _dist(p2, p3)
                cur += _bezier([pos, p1, p2, p3], max(8, min(200, int(length / 4) + 8)))
                last_ctrl, pos = p2, p3
                continue
            elif c in "QT":
                if c == "Q":
                    p1 = (num(), num())
                    if rel:
                        p1 = (pos[0] + p1[0], pos[1] + p1[1])
                else:
                    p1 = (2 * pos[0] - last_ctrl[0], 2 * pos[1] - last_ctrl[1]) if last_ctrl else pos
                p2 = (num(), num())
                if rel:
                    p2 = (pos[0] + p2[0], pos[1] + p2[1])
                length = _dist(pos, p1) + _dist(p1, p2)
                cur += _bezier([pos, p1, p2], max(8, min(200, int(length / 4) + 8)))
                last_ctrl, pos = p1, p2
                continue
            elif c == "A":
                rx, ry, rot = num(), num(), num()
                large, sweep = flag(), flag()
                x, y = num(), num()
                p1 = (pos[0] + x, pos[1] + y) if rel else (x, y)
                cur += _svg_arc(pos, rx, ry, rot, large, sweep, p1)
                pos = p1
            else:
                i += 1
        except (IndexError, ValueError):
            break
        last_ctrl = None
    if len(cur) > 1:
        subs.append((cur, False))
    return subs


def parse_svg(text: str) -> Profile:
    import xml.etree.ElementTree as ET
    low = text[:4000].lower()
    if "<!doctype" in low or "<!entity" in low:
        raise ProfileError("This SVG has a DOCTYPE or entity declaration, which is not accepted. Re-save it as a plain SVG")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ProfileError("This is not a valid SVG file") from exc
    strip = lambda t: t.rsplit("}", 1)[-1]  # noqa: E731
    if strip(root.tag) != "svg":
        raise ProfileError("This is not an SVG file")
    notes: list[str] = []

    def length(v: str | None) -> tuple[float, str] | None:
        if not v:
            return None
        m = re.match(rf"\s*({_NUM})\s*([a-z%]*)", v)
        return (float(m.group(1)), m.group(2)) if m else None

    w, h = length(root.get("width")), length(root.get("height"))
    vb = [float(x) for x in re.findall(_NUM, root.get("viewBox") or "")]
    scale = _PX_MM
    if w and w[1] != "%" and len(vb) == 4 and vb[2] > 0:
        scale = w[0] * _UNIT_PX.get(w[1], 1.0) * _PX_MM / vb[2]
    elif w and w[1] != "%" and not vb:
        scale = _UNIT_PX.get(w[1], 1.0) * _PX_MM
    elif not w:
        notes.append("The SVG has no width and height, so one unit is read as 1 px (0.2646 mm). Check the size")
    if w and w[1] in ("", "px") and not vb:
        notes.append("The SVG size is in pixels. It is read at 96 pixels per inch. Check the size")

    paths: list[list[Point]] = []
    loops: list[list[Point]] = []
    circles: list[tuple[float, float, float]] = []
    skipped = 0

    def walk(el, mtx: list[float]):
        nonlocal skipped
        tag = strip(el.tag)
        if tag in ("defs", "clipPath", "mask", "symbol", "metadata", "title", "desc", "style", "text", "image", "use"):
            if tag in ("text", "image", "use"):
                skipped += 1
            return
        if "display:none" in (el.get("style") or "").replace(" ", "") or el.get("display") == "none":
            return
        m = _mat_mul(mtx, _parse_transform(el.get("transform")))
        fl = lambda a, d=0.0: float(re.match(_NUM, (el.get(a) or str(d)).strip()).group(0)) if re.match(_NUM, (el.get(a) or str(d)).strip()) else d  # noqa: E731
        T = lambda p: (_apply(m, p)[0] * scale, -_apply(m, p)[1] * scale)  # noqa: E731
        try:
            if tag == "rect":
                x, y, rw, rh = fl("x"), fl("y"), fl("width"), fl("height")
                if rw > 0 and rh > 0:
                    loops.append([T((x, y)), T((x + rw, y)), T((x + rw, y + rh)), T((x, y + rh))])
            elif tag in ("circle", "ellipse"):
                cx, cy = fl("cx"), fl("cy")
                rx = fl("r") if tag == "circle" else fl("rx")
                ry = rx if tag == "circle" else fl("ry")
                uniform = abs(abs(m[0]) - abs(m[3])) < 1e-9 and abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9
                if tag == "circle" and uniform:
                    c = T((cx, cy))
                    circles.append((c[0], c[1], rx * abs(m[0]) * scale))
                elif rx > 0 and ry > 0:
                    loops.append([T((cx + rx * math.cos(a), cy + ry * math.sin(a))) for a in [2 * math.pi * s / 72 for s in range(72)]])
            elif tag in ("polygon", "polyline"):
                v = [float(x) for x in re.findall(_NUM, el.get("points") or "")]
                pts = [T((v[i], v[i + 1])) for i in range(0, len(v) - 1, 2)]
                (loops if tag == "polygon" else paths).append(pts)
            elif tag == "line":
                paths.append([T((fl("x1"), fl("y1"))), T((fl("x2"), fl("y2")))])
            elif tag == "path":
                for pts, closed in _path_subpaths(el.get("d") or ""):
                    tp = [T(p) for p in pts]
                    (loops if closed else paths).append(tp)
        except (ValueError, TypeError):
            skipped += 1
        for child in el:
            walk(child, m)

    walk(root, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    joined, open_left = chain_paths(paths)
    loops += joined
    if skipped:
        notes.append(f"{skipped} element(s) such as text, images or references were skipped")
    if open_left:
        notes.append(f"{open_left} open path(s) were dropped because their ends do not meet")
    return build_profile(loops, circles, source="svg", warnings=notes)


# ----------------------------------------------------------------------- KiCad
def _sexpr(text: str) -> list[Any]:
    toks = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', text)
    stack: list[list[Any]] = [[]]
    for t in toks:
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) < 2:
                raise ProfileError("The KiCad file has unbalanced brackets")
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(t[1:-1] if t.startswith('"') else t)
    if len(stack) != 1:
        raise ProfileError("The KiCad file has unbalanced brackets")
    return stack[0]


def _find(node: list[Any], name: str) -> list[Any] | None:
    for c in node:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def _findall(node: list[Any], name: str) -> list[list[Any]]:
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def _xy(node: list[Any] | None) -> Point | None:
    if node and len(node) >= 3:
        try:
            return float(node[1]), -float(node[2])          # KiCad y points down
        except ValueError:
            return None
    return None


def parse_kicad_pcb(text: str) -> Profile:
    tree = _sexpr(text)
    if not tree or not isinstance(tree[0], list) or not tree[0] or tree[0][0] != "kicad_pcb":
        raise ProfileError("This is not a KiCad board file (.kicad_pcb)")
    board = tree[0]
    thickness = 1.6
    gen = _find(board, "general")
    th = _find(gen, "thickness") if gen else None
    if th and len(th) >= 2:
        try:
            thickness = float(th[1])
        except ValueError:
            pass

    def on_edge(node: list[Any]) -> bool:
        layer = _find(node, "layer")
        return bool(layer and len(layer) > 1 and layer[1] == "Edge.Cuts")

    paths: list[list[Point]] = []
    loops: list[list[Point]] = []
    circles: list[tuple[float, float, float]] = []
    for n in board:
        if not isinstance(n, list) or not n or not isinstance(n[0], str) or not n[0].startswith("gr_") or not on_edge(n):
            continue
        kind = n[0]
        if kind == "gr_line":
            a, b = _xy(_find(n, "start")), _xy(_find(n, "end"))
            if a and b:
                paths.append([a, b])
        elif kind == "gr_arc":
            s, m, e = _xy(_find(n, "start")), _xy(_find(n, "mid")), _xy(_find(n, "end"))
            if s and m and e:
                paths.append(three_point_arc(s, m, e))
            elif s and e and _find(n, "angle"):                  # KiCad 5: start = centre, end = arc start
                ang = float(_find(n, "angle")[1])
                r = _dist(s, e)
                a0 = math.atan2(e[1] - s[1], e[0] - s[0])
                paths.append(arc_points(s[0], s[1], r, a0, a0 - math.radians(ang), ccw=ang < 0))
        elif kind == "gr_circle":
            c, e = _xy(_find(n, "center")), _xy(_find(n, "end"))
            if c and e:
                circles.append((c[0], c[1], _dist(c, e)))
        elif kind == "gr_rect":
            a, b = _xy(_find(n, "start")), _xy(_find(n, "end"))
            if a and b:
                loops.append([a, (b[0], a[1]), b, (a[0], b[1])])
        elif kind == "gr_poly":
            pts_node = _find(n, "pts")
            pts = [p for p in (_xy(x) for x in _findall(pts_node or [], "xy")) if p]
            if len(pts) >= 3:
                loops.append(pts)
    joined, open_left = chain_paths(paths)
    loops += joined
    notes = [f"Board thickness is {thickness:g} mm"] if th else ["The board thickness is not set in the file, so 1.6 mm is used"]
    if open_left:
        notes.append(f"{open_left} open edge path(s) were dropped. Close the board outline in KiCad")
    base = build_profile(loops, circles, source="kicad", warnings=notes, thickness=thickness)

    # Drilled holes: vias and through-hole pads inside the board
    outer = [s["outer"] for s in base.shapes]
    drills: list[tuple[float, float, float]] = []
    for v in _findall(board, "via"):
        at, dr = _xy(_find(v, "at")), _find(v, "drill")
        if at and dr and len(dr) >= 2:
            try:
                drills.append((at[0], at[1], float(dr[1])))
            except ValueError:
                pass
    slots = 0
    for fp in _findall(board, "footprint") + _findall(board, "module"):
        fat = _find(fp, "at")
        try:
            fx, fy = float(fat[1]), float(fat[2]) if fat else (0.0, 0.0)
            rot = math.radians(float(fat[3])) if fat and len(fat) > 3 else 0.0
        except (ValueError, IndexError, TypeError):
            continue
        for pad in _findall(fp, "pad"):
            dr = _find(pad, "drill")
            pat = _find(pad, "at")
            if not dr or len(dr) < 2 or not pat:
                continue
            if dr[1] == "oval":
                slots += 1
                continue
            try:
                d = float(dr[1])
                px, py = float(pat[1]), float(pat[2])
            except (ValueError, IndexError):
                continue
            wx = fx + px * math.cos(rot) + py * math.sin(rot)      # footprint turn, y down
            wy = fy - px * math.sin(rot) + py * math.cos(rot)
            drills.append((wx, -wy, d))
    holes_added = 0
    for x, y, d in drills:
        if d <= 0.05:
            continue
        for s in base.shapes:
            o = s["outer"]
            inside = (math.hypot(x - o["cx"], y - o["cy"]) < o["r"]) if o["type"] == "circle" else point_in_polygon((x, y), [tuple(p) for p in o["points"]])
            if inside:
                s["holes"].append({"type": "circle", "cx": round(x, 6), "cy": round(y, 6), "r": d / 2.0})
                holes_added += 1
                break
    if holes_added:
        base.warnings.append(f"{holes_added} drilled hole(s) from vias and pads were added")
    if slots:
        base.warnings.append(f"{slots} slotted hole(s) were skipped")
    base.warnings.append("Components and copper are not modelled. This is the bare board")
    return base


# ---------------------------------------------------------------------- Gerber
def _gerber_number(raw: str, fmt: tuple[int, int, str]) -> float:
    ii, dd, zero = fmt
    sign = -1.0 if raw.startswith("-") else 1.0
    digits = raw.lstrip("+-")
    if "." in digits:
        return sign * float(digits)
    if zero == "T":                                 # trailing zeros omitted: pad on the right
        digits = digits.ljust(ii + dd, "0")
    return sign * int(digits or "0") / (10 ** dd)


def parse_gerber(text: str, filename: str = "") -> tuple[list[list[Point]], tuple[float, float, float, float] | None, bool, list[str]]:
    """Returns (closed loops, extents of everything drawn, is_outline, warnings). mm, y up."""
    fmt = (2, 4, "L")
    unit = 1.0
    notes: list[str] = []
    is_outline = bool(re.search(r"%TF\.FileFunction,(?:Profile|Outline)", text, re.I)) or bool(
        re.search(r"(edge[._-]?cuts|\.gko$|\.gm1$|\.gml$|outline|profile)", filename, re.I))
    pos = (0.0, 0.0)
    mode = "G01"
    region = False
    cur_path: list[Point] = []
    paths: list[list[Point]] = []
    regions: list[list[Point]] = []
    extents: list[Point] = []
    skipped_aper = 0

    def start_path(p: Point):
        nonlocal cur_path
        if len(cur_path) > 1:
            (regions if region else paths).append(cur_path)
        cur_path = [p]

    blocks = re.split(r"\*", text.replace("\r", "").replace("\n", ""))
    for blk in blocks:
        b = blk.strip()
        if not b:
            continue
        if b.startswith("%"):
            b = b.lstrip("%").rstrip("%")
        m = re.match(r"FSL?([LT])?A?X(\d)(\d)Y(\d)(\d)", b) or re.match(r"FS([LT])?([AI])X(\d)(\d)Y(\d)(\d)", b)
        if b.startswith("FS"):
            mm = re.match(r"FS([LT]?)([AI]?)X(\d)(\d)Y(\d)(\d)", b)
            if mm:
                fmt = (int(mm.group(3)), int(mm.group(4)), mm.group(1) or "L")
            continue
        if b.startswith("MOMM") or b == "G71":
            unit = 1.0
            continue
        if b.startswith("MOIN") or b == "G70":
            unit = 25.4
            continue
        if b.startswith(("AD", "AM", "TF", "TA", "TO", "TD", "LP", "LM", "LR", "LS", "SR", "OF", "IP", "IN")):
            if b.startswith("AD"):
                skipped_aper += 1
            continue
        # coordinate block, possibly with G codes in front
        for g in re.findall(r"G(\d+)", b):
            gi = int(g)
            if gi in (1, 2, 3):
                mode = f"G0{gi}"
            elif gi == 36:
                start_path(pos) if cur_path and len(cur_path) > 1 else None
                region = True
                cur_path = []
            elif gi == 37:
                if len(cur_path) > 2:
                    regions.append(cur_path)
                cur_path = []
                region = False
        co = dict(re.findall(r"([XYIJ])([-+]?[\d.]+)", b))
        d = re.search(r"D0?([123])$", b)
        if not co and not d:
            continue
        x = _gerber_number(co["X"], fmt) * unit if "X" in co else pos[0]
        y = _gerber_number(co["Y"], fmt) * unit if "Y" in co else pos[1]
        new = (x, y)
        dn = int(d.group(1)) if d else 1
        if dn == 2:
            start_path(new)
        elif dn == 1:
            if not cur_path:
                cur_path = [pos]
            if mode == "G01":
                cur_path.append(new)
            else:
                i_off = _gerber_number(co.get("I", "0"), fmt) * unit
                j_off = _gerber_number(co.get("J", "0"), fmt) * unit
                cx, cy = pos[0] + i_off, pos[1] + j_off
                r = math.hypot(pos[0] - cx, pos[1] - cy)
                a0, a1 = math.atan2(pos[1] - cy, pos[0] - cx), math.atan2(y - cy, x - cx)
                cur_path += arc_points(cx, cy, r, a0, a1, ccw=(mode == "G03"))[1:]
        elif dn == 3:
            extents.append(new)
        extents.append(new)
        pos = new
    if len(cur_path) > 1:
        (regions if region else paths).append(cur_path)
    joined, open_left = chain_paths(paths)
    loops = joined + [r[:-1] if _dist(r[0], r[-1]) <= JOIN_TOL_MM else r for r in regions if len(r) >= 3]
    if open_left and is_outline:
        notes.append(f"{open_left} open outline path(s) were dropped because their ends do not meet")
    ext = None
    if extents:
        xs, ys = [p[0] for p in extents], [p[1] for p in extents]
        ext = (min(xs), min(ys), max(xs), max(ys))
    return loops, ext, is_outline, notes


def parse_excellon(text: str) -> list[tuple[float, float, float]]:
    """Drill hits as (x, y, diameter) in mm. Needs explicit decimal coordinates (as KiCad and most
    CAM tools write them), or a FILE format line saying so."""
    metric = True
    tools: dict[str, float] = {}
    cur: float | None = None
    hits: list[tuple[float, float, float]] = []
    header = True
    saw_integer = False
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.upper().startswith(("METRIC", "M71")):
            metric = True
            continue
        if line.upper().startswith(("INCH", "M72")):
            metric = False
            continue
        if line == "%" or line == "M95":
            header = False
            continue
        m = re.match(r"T(\d+)C([\d.]+)", line)
        if m:
            tools[m.group(1).lstrip("0") or "0"] = float(m.group(2)) * (1.0 if metric else 25.4)
            continue
        m = re.match(r"T(\d+)$", line)
        if m and not (header and False):
            cur = tools.get(m.group(1).lstrip("0") or "0")
            header = False
            continue
        m = re.match(r"X([-+]?[\d.]+)Y([-+]?[\d.]+)", line)
        if m and cur:
            if "." not in m.group(1) or "." not in m.group(2):
                saw_integer = True
                continue
            k = 1.0 if metric else 25.4
            hits.append((float(m.group(1)) * k, float(m.group(2)) * k, cur))
    if saw_integer and not hits:
        raise ProfileError("This drill file does not use decimal coordinates, which is not supported yet")
    return hits


def parse_gerber_bundle(files: dict[str, bytes]) -> Profile:
    """A board from a set of Gerber and drill files (the contents of a fabrication zip)."""
    outline: list[list[Point]] | None = None
    ext_all: list[tuple[float, float, float, float]] = []
    holes: list[tuple[float, float, float]] = []
    notes: list[str] = []
    layers = 0
    for name, data in files.items():
        lower = name.lower()
        text = data.decode("utf-8", "ignore")
        if lower.endswith((".drl", ".xln", ".exc", ".ncd")) or (lower.endswith(".txt") and "M48" in text[:200]):
            try:
                holes += parse_excellon(text)
            except ProfileError as exc:
                notes.append(f"{name}: {exc}")
            continue
        if not (lower.endswith((".gbr", ".ger", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gko", ".gm1", ".gml", ".gtp", ".gbp", ".g1", ".g2", ".g3", ".g4"))
                or "%FS" in text[:2000]):
            continue
        layers += 1
        loops, ext, is_outline, n = parse_gerber(text, name)
        notes += [f"{name}: {x}" for x in n]
        if is_outline and loops and outline is None:
            outline = loops
        if ext:
            ext_all.append(ext)
    if outline is None:
        if not ext_all:
            raise ProfileError("No Gerber layers were found in the zip")
        x0, y0 = min(e[0] for e in ext_all), min(e[1] for e in ext_all)
        x1, y1 = max(e[2] for e in ext_all), max(e[3] for e in ext_all)
        outline = [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]
        notes.append("No board outline layer was found, so a rectangle around everything drawn is used")
    prof = build_profile(outline, [], source="gerber", warnings=notes, thickness=1.6)
    added = 0
    for x, y, d in holes:
        for s in prof.shapes:
            o = s["outer"]
            if o["type"] == "polygon" and point_in_polygon((x, y), [tuple(p) for p in o["points"]]):
                s["holes"].append({"type": "circle", "cx": round(x, 6), "cy": round(y, 6), "r": d / 2.0})
                added += 1
                break
    if added:
        prof.warnings.append(f"{added} drilled hole(s) added from the drill file")
    prof.warnings.append("Board thickness is not in Gerber files, so 1.6 mm is used. Copper and components are not modelled")
    return prof


def read_zip(data: bytes, *, max_files: int = 100, max_total: int = 50 * 1024 * 1024, max_each: int = 20 * 1024 * 1024) -> dict[str, bytes]:
    """The files in a zip, with limits so a zip bomb cannot fill memory."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ProfileError("That zip file is damaged") from exc
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > max_files:
        raise ProfileError(f"The zip has more than {max_files} files")
    if sum(i.file_size for i in infos) > max_total or any(i.file_size > max_each for i in infos):
        raise ProfileError("The zip is too large once unpacked")
    out: dict[str, bytes] = {}
    for i in infos:
        name = i.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/"):
            continue
        out[name.rsplit("/", 1)[-1]] = zf.read(i)
    return out


# --------------------------------------------------------------------- dispatch
def parse_profile_file(kind: str, data: bytes, filename: str = "") -> Profile:
    """kind is one of dxf, svg, kicad, gerber, drill, gerber_zip."""
    if kind == "gerber_zip":
        return parse_gerber_bundle(read_zip(data))
    text = data.decode("utf-8", "ignore")
    if kind == "dxf":
        return parse_dxf(text)
    if kind == "svg":
        return parse_svg(text)
    if kind == "kicad":
        return parse_kicad_pcb(text)
    if kind == "gerber":
        return parse_gerber_bundle({filename or "layer.gbr": data})
    if kind == "drill":
        raise ProfileError("A drill file alone has no board outline. Upload the Gerber outline together with it in a zip")
    raise ProfileError("That file type is not a drawing")
