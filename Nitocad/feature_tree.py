"""
Feature-tree DSL: a short, ordered list of sketch-based features (extrude, revolve,
loft, pattern, mirror) that builds one solid. This is how NitoCAD makes parts that no
fixed template covers (a wing, a wall with openings, a custom housing) while keeping
the rule used everywhere else: the LLM proposes, deterministic code decides.

This module is PURE PYTHON (no cadquery): strict schema validation, bounds, reference
integrity, geometric pre-checks, and the NACA airfoil generator. feature_builder.py
executes a validated tree with CadQuery.

Units: millimetres and degrees. Sketch planes (the same conventions as CadQuery):
  XY: sketch x -> X, sketch y -> Y, extrudes toward +Z
  XZ: sketch x -> X, sketch y -> Z, extrudes toward -Y
  YZ: sketch x -> Y, sketch y -> Z, extrudes toward +X
`offset` moves the sketch plane along its extrude direction.

Example (plate with a 4-hole pattern):
  {"features": [
    {"id": "plate", "op": "extrude", "plane": "XY",
     "profile": {"type": "rect", "width": 100, "height": 60}, "distance": 8},
    {"id": "hole", "op": "extrude", "plane": "XY", "mode": "cut",
     "profile": {"type": "circle", "diameter": 6, "center": [-40, -20]}, "distance": 8},
    {"id": "holes", "op": "pattern", "feature": "hole", "kind": "linear",
     "count": 2, "spacing": [80, 0, 0]}]}
(a cut sketch starts at or before the near face and runs `distance` past the far face; `reverse: true` extrudes
against the plane direction, e.g. to cut downward from a top face).

Sweep (pipes, tubes, wiring channels): a profile is swept along a 3D polyline path, with an optional
bend_radius that rounds every corner with a tangent arc. The profile is drawn in its own 2D frame and is
placed automatically perpendicular to the first path segment at the first path point (see profile_frame).

NOT in v1 (deliberately): fillets/chamfers (OCCT edge treatments can silently produce
degenerate solids, see cad_templates/_safe_ops.py), shells, threads, curved (spline) sweep paths.
"""
from __future__ import annotations

import math
import re
from typing import Any

MAX_FEATURES = 60
MAX_DIM = 20_000.0          # mm: any single dimension / coordinate
MAX_POINTS = 500            # polygon vertices
MAX_PATTERN = 100           # instances in one pattern
MAX_TOOL_INSTANCES = 400    # total tool bodies across the whole tree (bounds boolean count)
MAX_SECTIONS = 12
PLANES = ("XY", "XZ", "YZ")
MODES = ("new", "add", "cut", "intersect")
BASE_OPS = ("extrude", "revolve", "loft", "sweep")
MAX_PATH_POINTS = 100
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_NACA_RE = re.compile(r"^\d{4}$")

_PROFILE_KEYS = {
    "rect": {"width", "height"},
    "circle": {"diameter"},
    "ellipse": {"width", "height"},
    "slot": {"length", "width", "angle"},
    "polygon": {"points"},
    "regular_polygon": {"sides", "diameter"},
    "airfoil": {"naca", "chord"},
}
_COMMON_PROFILE_KEYS = {"type", "center", "holes"}


# ---------------------------------------------------------------- numerics
def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _pos(x: Any) -> bool:
    return _num(x) and 0 < x <= MAX_DIM


def _coord(x: Any) -> bool:
    return _num(x) and abs(x) <= MAX_DIM


def _vec(v: Any, n: int) -> list[float] | None:
    if isinstance(v, (list, tuple)) and len(v) == n and all(_coord(c) for c in v):
        return [float(c) for c in v]
    return None


# ----------------------------------------------------------------- airfoil
def airfoil_points(naca: str, chord: float, n: int = 40) -> list[tuple[float, float]]:
    """Closed NACA 4-digit outline, leading edge at (0, 0), trailing edge at (chord, 0).
    Upper surface LE->TE then lower surface TE->LE, 2n points, closed trailing edge,
    cosine spacing (dense near the leading edge). Same point count for every call with the
    same n, which keeps lofts between different airfoils topologically identical."""
    if not _NACA_RE.match(str(naca)):
        raise ValueError(f"NACA code must be 4 digits, got {naca!r}")
    m, p, t = int(naca[0]) / 100.0, int(naca[1]) / 10.0, int(naca[2:]) / 100.0
    upper: list[tuple[float, float]] = []
    lower: list[tuple[float, float]] = []
    for i in range(n + 1):
        x = (1 - math.cos(math.pi * i / n)) / 2
        yt = 5 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2
                      + 0.2843 * x ** 3 - 0.1036 * x ** 4)  # closed trailing edge
        if m == 0 or p == 0:
            yc, dyc = 0.0, 0.0
        elif x < p:
            yc, dyc = m / p ** 2 * (2 * p * x - x * x), 2 * m / p ** 2 * (p - x)
        else:
            yc = m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x * x)
            dyc = 2 * m / (1 - p) ** 2 * (p - x)
        th = math.atan(dyc)
        upper.append((x - yt * math.sin(th), yc + yt * math.cos(th)))
        lower.append((x + yt * math.sin(th), yc - yt * math.cos(th)))
    pts = upper + lower[n - 1:0:-1]  # drop the duplicated TE and LE points
    return [(px * chord, py * chord) for px, py in pts]


# ---------------------------------------------------------------- profiles
def _segments_intersect(a, b, c, d) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def _polygon_problems(pts: list[list[float]]) -> str | None:
    n = len(pts)
    for i in range(n):
        if math.dist(pts[i], pts[(i + 1) % n]) < 1e-6:
            return "has duplicate consecutive points"
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_intersect(a, b, pts[j], pts[(j + 1) % n]):
                return "is self-intersecting"
    area2 = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    if abs(area2) < 1e-9:
        return "has zero area"
    return None


def profile_bbox(p: dict[str, Any]) -> tuple[float, float, float, float]:
    """Axis-aligned bounds (xmin, ymin, xmax, ymax) of a NORMALIZED profile (conservative for slots)."""
    cx, cy = p["center"]
    t = p["type"]
    if t in ("rect", "ellipse"):
        return cx - p["width"] / 2, cy - p["height"] / 2, cx + p["width"] / 2, cy + p["height"] / 2
    if t in ("circle", "regular_polygon"):
        r = p["diameter"] / 2
        return cx - r, cy - r, cx + r, cy + r
    if t == "slot":
        r = p["length"] / 2
        return cx - r, cy - r, cx + r, cy + r
    pts = airfoil_points(p["naca"], p["chord"]) if t == "airfoil" else p["points"]
    xs, ys = [q[0] + cx for q in pts], [q[1] + cy for q in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _profile(p: Any, label: str, errors: list[str], holes_ok: bool) -> dict[str, Any] | None:
    if not isinstance(p, dict):
        errors.append(f"{label}: profile must be an object.")
        return None
    t = p.get("type")
    if t not in _PROFILE_KEYS:
        errors.append(f"{label}: profile type {t!r} is not supported. Use one of: {', '.join(_PROFILE_KEYS)}.")
        return None
    extra = set(p) - _PROFILE_KEYS[t] - _COMMON_PROFILE_KEYS
    if extra:
        errors.append(f"{label}: unknown profile key(s) {sorted(extra)} for {t}.")
    ok = True
    out: dict[str, Any] = {"type": t}
    center = _vec(p.get("center", [0, 0]), 2)
    if center is None:
        errors.append(f"{label}: center must be [x, y] in mm (±{MAX_DIM:g}).")
        ok, center = False, [0.0, 0.0]
    out["center"] = center

    for key in ("width", "height", "diameter", "length", "chord"):
        if key in _PROFILE_KEYS[t]:
            if not _pos(p.get(key)):
                errors.append(f"{label}: {key} must be a number in (0, {MAX_DIM:g}] mm.")
                ok = False
            else:
                out[key] = float(p[key])
    if t == "slot":
        angle = p.get("angle", 0)
        if not (_num(angle) and abs(angle) <= 360):
            errors.append(f"{label}: angle must be a number of degrees within ±360.")
            ok = False
        else:
            out["angle"] = float(angle)
        if ok and out["length"] <= out["width"]:
            errors.append(f"{label}: slot length must exceed its width.")
            ok = False
    if t == "regular_polygon":
        sides = p.get("sides")
        if not (isinstance(sides, int) and not isinstance(sides, bool) and 3 <= sides <= 64):
            errors.append(f"{label}: sides must be an integer from 3 to 64.")
            ok = False
        else:
            out["sides"] = sides
    if t == "airfoil":
        naca = p.get("naca")
        if not (isinstance(naca, str) and _NACA_RE.match(naca)):
            errors.append(f"{label}: naca must be a 4-digit string like \"2412\".")
            ok = False
        elif not (1 <= int(naca[2:]) <= 30):
            errors.append(f"{label}: NACA thickness (last two digits) must be 01 to 30.")
            ok = False
        else:
            out["naca"] = naca
    if t == "polygon":
        pts_in = p.get("points")
        if not (isinstance(pts_in, list) and 3 <= len(pts_in) <= MAX_POINTS):
            errors.append(f"{label}: points must be a list of 3 to {MAX_POINTS} [x, y] pairs.")
            ok = False
        else:
            pts = [_vec(q, 2) for q in pts_in]
            if any(q is None for q in pts):
                errors.append(f"{label}: every point must be [x, y] within ±{MAX_DIM:g} mm.")
                ok = False
            else:
                problem = _polygon_problems(pts)  # type: ignore[arg-type]
                if problem:
                    errors.append(f"{label}: polygon {problem}.")
                    ok = False
                out["points"] = pts

    holes_in = p.get("holes")
    if holes_in is not None:
        if not holes_ok:
            errors.append(f"{label}: holes are not allowed in this feature.")
            ok = False
        elif not isinstance(holes_in, list) or not holes_in:
            errors.append(f"{label}: holes must be a non-empty list of profiles.")
            ok = False
        elif ok:
            outer = profile_bbox(out)
            holes = []
            for k, h in enumerate(holes_in):
                hn = _profile(h, f"{label}.holes[{k}]", errors, holes_ok=False)
                if hn is None:
                    ok = False
                    continue
                hb = profile_bbox(hn)
                if not (hb[0] > outer[0] and hb[1] > outer[1] and hb[2] < outer[2] and hb[3] < outer[3]):
                    errors.append(f"{label}.holes[{k}]: must lie strictly inside the outer profile.")
                    ok = False
                holes.append(hn)
            out["holes"] = holes
    return out if ok else None


def _plane(p: Any, label: str, errors: list[str]) -> str:
    if p not in PLANES:
        errors.append(f"{label}: plane must be one of {', '.join(PLANES)}, got {p!r}.")
        return "XY"
    return p


def _offset(v: Any, label: str, errors: list[str], key: str = "offset") -> float:
    if not _coord(v):
        errors.append(f"{label}: {key} must be a number within ±{MAX_DIM:g} mm.")
        return 0.0
    return float(v)


def _check_keys(f: dict, allowed: set[str], label: str, errors: list[str]) -> None:
    extra = set(f) - allowed
    if extra:
        errors.append(f"{label}: unknown key(s) {sorted(extra)}.")


# --------------------------------------------------------------- 3D paths
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _addv(a, b, k=1.0):
    return (a[0] + b[0] * k, a[1] + b[1] * k, a[2] + b[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _len(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def profile_frame(direction) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(x_dir, y_dir) of the sketch frame for a sweep profile placed perpendicular to `direction`.
    x is global +Z projected onto the profile plane (global +X when the path starts along +-Z), y = direction x x.
    A path that starts along +Z therefore gets the same x->X, y->Y frame as the XY sketch plane."""
    d = _unit(direction)
    ref = (0.0, 0.0, 1.0) if abs(d[2]) < 0.99 else (1.0, 0.0, 0.0)
    x = _unit(_sub(ref, (d[0] * _dot(ref, d), d[1] * _dot(ref, d), d[2] * _dot(ref, d))))
    return x, _cross(d, x)


def _seg_seg_dist(p1, q1, p2, q2) -> float:
    """Minimum distance between two 3D segments."""
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    eps = 1e-12
    if a <= eps and e <= eps:
        return _len(r)
    if a <= eps:
        s_, t_ = 0.0, _clamp(f / e, 0.0, 1.0)
    else:
        c = _dot(d1, r)
        if e <= eps:
            t_, s_ = 0.0, _clamp(-c / a, 0.0, 1.0)
        else:
            b = _dot(d1, d2)
            denom = a * e - b * b
            s_ = _clamp((b * f - c * e) / denom, 0.0, 1.0) if denom > eps else 0.0
            t_ = (b * s_ + f) / e
            if t_ < 0.0:
                t_, s_ = 0.0, _clamp(-c / a, 0.0, 1.0)
            elif t_ > 1.0:
                t_, s_ = 1.0, _clamp((b - c) / a, 0.0, 1.0)
    return _len(_sub(_addv(p1, d1, s_), _addv(p2, d2, t_)))


def expand_path(points, bend_radius: float):
    """Turn a 3D polyline into line and tangent-arc segments.

    Returns (segments, errors). A segment is ("line", start, end) or
    ("arc", start, mid, end, radius, turn_angle_rad). With bend_radius = 0 corners stay sharp.
    Corners that are collinear are passed through; a corner that turns back on itself
    (>179 deg) or a radius that does not fit between neighbouring corners is an error."""
    pts = [tuple(float(c) for c in p) for p in points]
    n = len(pts)
    errors: list[str] = []
    dirs, lens = [], []
    for i in range(n - 1):
        d = _sub(pts[i + 1], pts[i])
        length = _len(d)
        if length < 1e-6:
            return [], [f"path point {i + 1} duplicates point {i}"]
        dirs.append(_unit(d))
        lens.append(length)

    tangent = [0.0] * n
    arcs: dict[int, tuple] = {}
    for i in range(1, n - 1):
        u1, u2 = dirs[i - 1], dirs[i]
        phi = math.acos(_clamp(_dot(u1, u2), -1.0, 1.0))  # turn angle at this corner
        if phi < 1e-6:
            continue
        if phi > math.radians(179):
            errors.append(f"path turns back on itself at point {i} (a hairpin); add an intermediate point")
            continue
        if bend_radius <= 0:
            continue
        t = bend_radius * math.tan(phi / 2)
        a_pt = _addv(pts[i], u1, -t)
        b_pt = _addv(pts[i], u2, t)
        n_in = _unit(_sub(u2, (u1[0] * _dot(u1, u2), u1[1] * _dot(u1, u2), u1[2] * _dot(u1, u2))))
        centre = _addv(a_pt, n_in, bend_radius)
        bis = _unit(_addv(_sub(a_pt, centre), _sub(b_pt, centre)))
        mid = _addv(centre, bis, bend_radius)
        tangent[i] = t
        arcs[i] = (a_pt, mid, b_pt, bend_radius, phi)

    for i in range(n - 1):
        if tangent[i] + tangent[i + 1] > lens[i] + 1e-9:
            errors.append(
                f"bend_radius {bend_radius:g} does not fit between path points {i} and {i + 1}: the two bends need "
                f"{tangent[i] + tangent[i + 1]:.1f} mm of that {lens[i]:.1f} mm segment; use a smaller radius or a longer segment")
    if errors:
        return [], errors

    segments: list[tuple] = []
    for i in range(n - 1):
        start = arcs[i][2] if i in arcs else pts[i]
        end = arcs[i + 1][0] if (i + 1) in arcs else pts[i + 1]
        if _len(_sub(end, start)) > 1e-9:
            segments.append(("line", start, end))
        if (i + 1) in arcs:
            a_pt, mid, b_pt, rad, phi = arcs[i + 1]
            segments.append(("arc", a_pt, mid, b_pt, rad, phi))
    return segments, []


def path_length(segments) -> float:
    total = 0.0
    for seg in segments:
        total += _len(_sub(seg[2], seg[1])) if seg[0] == "line" else seg[4] * seg[5]
    return total


# ------------------------------------------------------------- validation
def validate_feature_tree(spec: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Returns (normalized_features, errors). The tree is only trustworthy if errors is empty."""
    errors: list[str] = []
    raw = spec.get("features") if isinstance(spec, dict) else spec
    if not isinstance(raw, list) or not raw:
        return [], ['Provide {"features": [...]} with at least one feature.']
    if len(raw) > MAX_FEATURES:
        errors.append(f"Too many features ({len(raw)}); the limit is {MAX_FEATURES}.")
        raw = raw[:MAX_FEATURES]

    out: list[dict[str, Any]] = []
    base_modes: dict[str, str] = {}   # id -> mode, for extrude/revolve/loft only
    all_ids: set[str] = set()
    instances = 0

    for i, f in enumerate(raw):
        label = f"features[{i}]"
        if not isinstance(f, dict):
            errors.append(f"{label} must be an object.")
            continue
        fid = f.get("id")
        if not isinstance(fid, str) or not _ID_RE.match(fid):
            errors.append(f"{label}.id must be lowercase snake_case, got {fid!r}.")
            fid = f"feature_{i}"
        elif fid in all_ids:
            errors.append(f"{label}.id {fid!r} is used twice.")
        all_ids.add(fid)
        label = f"feature {fid!r}"
        op = f.get("op")
        norm: dict[str, Any] = {"id": fid, "op": op}

        if op in BASE_OPS:
            default_mode = "new" if i == 0 else "add"
            mode = f.get("mode", default_mode)
            if mode not in MODES:
                errors.append(f"{label}: mode must be one of {', '.join(MODES)}.")
                mode = default_mode
            if i == 0 and mode != "new":
                errors.append(f"{label}: the first feature must have mode \"new\".")
            if i > 0 and mode == "new":
                errors.append(f"{label}: only the first feature may use mode \"new\"; use add, cut or intersect.")
            norm["mode"] = mode
            base_modes[fid] = mode
            instances += 1

        if op == "extrude":
            _check_keys(f, {"id", "op", "plane", "offset", "profile", "distance", "symmetric", "reverse", "mode"}, label, errors)
            norm["plane"] = _plane(f.get("plane", "XY"), label, errors)
            norm["offset"] = _offset(f.get("offset", 0), label, errors)
            norm["profile"] = _profile(f.get("profile"), f"{label}.profile", errors, holes_ok=True)
            if not _pos(f.get("distance")):
                errors.append(f"{label}: distance must be a number in (0, {MAX_DIM:g}] mm.")
            norm["distance"] = float(f["distance"]) if _pos(f.get("distance")) else 0.0
            sym = f.get("symmetric", False)
            if not isinstance(sym, bool):
                errors.append(f"{label}: symmetric must be true or false.")
            norm["symmetric"] = bool(sym)
            rev = f.get("reverse", False)
            if not isinstance(rev, bool):
                errors.append(f"{label}: reverse must be true or false.")
            norm["reverse"] = bool(rev)
            if norm["symmetric"] and norm["reverse"]:
                errors.append(f"{label}: symmetric and reverse cannot both be set.")

        elif op == "revolve":
            _check_keys(f, {"id", "op", "plane", "offset", "profile", "axis", "angle", "mode"}, label, errors)
            norm["plane"] = _plane(f.get("plane", "XY"), label, errors)
            norm["offset"] = _offset(f.get("offset", 0), label, errors)
            prof = _profile(f.get("profile"), f"{label}.profile", errors, holes_ok=False)
            norm["profile"] = prof
            axis = f.get("axis", "y")
            if axis not in ("x", "y"):
                errors.append(f"{label}: axis must be \"x\" or \"y\" (a sketch axis through the origin).")
                axis = "y"
            norm["axis"] = axis
            angle = f.get("angle", 360)
            if not (_num(angle) and 0 < angle <= 360):
                errors.append(f"{label}: angle must be in (0, 360] degrees.")
                angle = 360
            norm["angle"] = float(angle)
            if prof is not None:
                b = profile_bbox(prof)
                lo = b[1] if axis == "x" else b[0]  # side of the axis the profile must stay on
                if lo < -1e-9:
                    errors.append(f"{label}: the profile crosses the revolve axis; keep it entirely on one side "
                                  f"({'y' if axis == 'x' else 'x'} >= 0).")

        elif op == "loft":
            _check_keys(f, {"id", "op", "sections", "ruled", "mode"}, label, errors)
            secs_in = f.get("sections")
            secs: list[dict[str, Any]] = []
            if not (isinstance(secs_in, list) and 2 <= len(secs_in) <= MAX_SECTIONS):
                errors.append(f"{label}: sections must be a list of 2 to {MAX_SECTIONS} sections.")
            else:
                for k, s in enumerate(secs_in):
                    sl = f"{label}.sections[{k}]"
                    if not isinstance(s, dict):
                        errors.append(f"{sl} must be an object.")
                        continue
                    _check_keys(s, {"plane", "offset", "profile"}, sl, errors)
                    secs.append({
                        "plane": _plane(s.get("plane", "XY"), sl, errors),
                        "offset": _offset(s.get("offset", 0), sl, errors),
                        "profile": _profile(s.get("profile"), f"{sl}.profile", errors, holes_ok=False),
                    })
                if len({s["plane"] for s in secs}) > 1:
                    errors.append(f"{label}: all sections must use the same plane.")
                offs = [s["offset"] for s in secs]
                if any(b <= a for a, b in zip(offs, offs[1:])):
                    errors.append(f"{label}: section offsets must be strictly increasing.")
                types = {s["profile"]["type"] for s in secs if s["profile"]}
                if len(types) > 1:
                    errors.append(f"{label}: all sections must use the same profile type (got {sorted(types)}) "
                                  "so the loft is topologically clean.")
                if "airfoil" in types or "polygon" in types:
                    counts = {len(s["profile"]["points"]) for s in secs
                              if s["profile"] and s["profile"]["type"] == "polygon"}
                    if len(counts) > 1:
                        errors.append(f"{label}: polygon sections must have the same number of points.")
            norm["sections"] = secs
            ruled = f.get("ruled", False)
            if not isinstance(ruled, bool):
                errors.append(f"{label}: ruled must be true or false.")
            norm["ruled"] = bool(ruled)

        elif op == "sweep":
            _check_keys(f, {"id", "op", "profile", "path", "bend_radius", "mode"}, label, errors)
            prof = _profile(f.get("profile"), f"{label}.profile", errors, holes_ok=True)
            norm["profile"] = prof
            path_in = f.get("path")
            pts: list[list[float]] | None = None
            if not (isinstance(path_in, list) and 2 <= len(path_in) <= MAX_PATH_POINTS):
                errors.append(f"{label}: path must be a list of 2 to {MAX_PATH_POINTS} [x, y, z] points.")
            else:
                pv = [_vec(q, 3) for q in path_in]
                if any(q is None for q in pv):
                    errors.append(f"{label}: every path point must be [x, y, z] within ±{MAX_DIM:g} mm.")
                else:
                    pts = pv  # type: ignore[assignment]
            norm["path"] = pts or []
            br = f.get("bend_radius", 0)
            if not (_num(br) and 0 <= br <= MAX_DIM):
                errors.append(f"{label}: bend_radius must be a number of mm in [0, {MAX_DIM:g}] (0 = sharp corners).")
                br = 0
            norm["bend_radius"] = float(br)
            r_prof = None
            if prof is not None:
                x0, y0, x1, y1 = profile_bbox(prof)
                r_prof = math.hypot(max(abs(x0), abs(x1)), max(abs(y0), abs(y1)))
                if norm["bend_radius"] > 0 and norm["bend_radius"] <= r_prof:
                    errors.append(f"{label}: bend_radius ({norm['bend_radius']:g}) must exceed the profile's outer radius "
                                  f"({r_prof:.1f}) or the swept body folds through itself at the bends.")
            if pts:
                _, perrs = expand_path(pts, norm["bend_radius"])
                errors.extend(f"{label}: {e}" for e in perrs)
                if not perrs and r_prof:
                    for a in range(len(pts) - 1):
                        for b in range(a + 2, len(pts) - 1):
                            gap = _seg_seg_dist(tuple(pts[a]), tuple(pts[a + 1]), tuple(pts[b]), tuple(pts[b + 1]))
                            if gap < 2 * r_prof:
                                errors.append(f"{label}: path segments {a} and {b} come within {gap:.1f} mm of each other; "
                                              f"the swept body (outer radius {r_prof:.1f}) would intersect itself.")

        elif op == "pattern":
            _check_keys(f, {"id", "op", "feature", "kind", "count", "spacing", "axis", "axis_point", "angle"},
                        label, errors)
            ref = f.get("feature")
            if ref not in base_modes:
                errors.append(f"{label}: feature {ref!r} must be an earlier extrude, revolve or loft.")
            elif base_modes[ref] == "new":
                errors.append(f"{label}: cannot pattern the first (\"new\") feature; pattern an add/cut/intersect feature.")
            norm["feature"] = ref
            kind = f.get("kind")
            if kind not in ("linear", "circular"):
                errors.append(f"{label}: kind must be \"linear\" or \"circular\".")
            norm["kind"] = kind
            count = f.get("count")
            if not (isinstance(count, int) and not isinstance(count, bool) and 2 <= count <= MAX_PATTERN):
                errors.append(f"{label}: count must be an integer from 2 to {MAX_PATTERN}.")
                count = 2
            norm["count"] = count
            instances += count - 1
            if kind == "linear":
                sp = _vec(f.get("spacing"), 3)
                if sp is None or not any(abs(c) > 1e-9 for c in sp):
                    errors.append(f"{label}: spacing must be a non-zero [dx, dy, dz] in mm.")
                    sp = [1.0, 0.0, 0.0]
                norm["spacing"] = sp
            elif kind == "circular":
                axis = f.get("axis", "Z")
                if axis not in ("X", "Y", "Z"):
                    errors.append(f"{label}: axis must be \"X\", \"Y\" or \"Z\".")
                    axis = "Z"
                ap = _vec(f.get("axis_point", [0, 0, 0]), 3)
                if ap is None:
                    errors.append(f"{label}: axis_point must be [x, y, z] in mm.")
                    ap = [0.0, 0.0, 0.0]
                angle = f.get("angle", 360)
                if not (_num(angle) and 0 < angle <= 360):
                    errors.append(f"{label}: angle must be in (0, 360] degrees.")
                    angle = 360
                norm.update(axis=axis, axis_point=ap, angle=float(angle))

        elif op == "mirror":
            _check_keys(f, {"id", "op", "feature", "plane", "offset"}, label, errors)
            ref = f.get("feature")
            if ref not in base_modes:
                errors.append(f"{label}: feature {ref!r} must be an earlier extrude, revolve or loft.")
            elif base_modes[ref] == "new":
                errors.append(f"{label}: cannot mirror the first (\"new\") feature; mirror an add/cut/intersect feature.")
            norm["feature"] = ref
            norm["plane"] = _plane(f.get("plane", "YZ"), label, errors)
            norm["offset"] = _offset(f.get("offset", 0), label, errors)
            instances += 1

        else:
            errors.append(f"{label}: op {op!r} is not supported. Use one of: extrude, revolve, loft, sweep, pattern, mirror.")
        out.append(norm)

    if raw and isinstance(raw[0], dict) and raw[0].get("op") in ("pattern", "mirror"):
        errors.append("The first feature must create geometry (extrude, revolve or loft).")
    if instances > MAX_TOOL_INSTANCES:
        errors.append(f"Too many tool bodies ({instances}); the limit is {MAX_TOOL_INSTANCES}.")
    return out, errors


DSL_HELP = (
    "Part type feature_tree builds ONE solid from parameters {\"features\": [...]} (use it for parts no other "
    "type covers: wings, walls with openings, housings). Units mm/degrees. Sketch planes: XY (x->X, y->Y, extrudes "
    "+Z), XZ (x->X, y->Z, extrudes -Y), YZ (x->Y, y->Z, extrudes +X); \"offset\" shifts the plane along its "
    "extrude direction. Each feature has a unique snake_case \"id\". The first feature must create geometry "
    "(mode \"new\"); later ones use mode \"add\" (default), \"cut\" or \"intersect\". A cut sketch should start at or "
    "before the near face and run past the far face; extrude has symmetric? and reverse? (against the plane direction).\n"
    "Features: sweep {profile, path:[[x,y,z],...], bend_radius?} sweeps the profile (drawn about its own origin, "
    "placed automatically perpendicular to the first path segment at the first path point; holes allowed, so a "
    "tube is a circle with a hole) along a 3D polyline; bend_radius rounds every corner and must exceed the "
    "profile's outer radius; segments must be long enough to fit the bends; extrude {plane, offset, profile, distance, symmetric?, reverse?}; revolve {plane, offset, profile, "
    "axis \"x\"|\"y\", angle?} (profile stays on one side of the axis); loft {sections:[{plane, offset, profile}, ...] "
    "(same plane, increasing offsets, same profile type), ruled?}; pattern {feature, kind \"linear\" "
    "{spacing:[dx,dy,dz]} | \"circular\" {axis \"X\"|\"Y\"|\"Z\", axis_point?, angle?}, count}; mirror {feature, plane, offset?}.\n"
    "Profiles: rect {width,height}, circle {diameter}, ellipse {width,height}, slot {length,width,angle?}, polygon "
    "{points:[[x,y],...]}, regular_polygon {sides,diameter}, airfoil {naca:\"2412\", chord} (leading edge at the "
    "center, chord along +x), all with optional center [x,y]; rect/circle/polygon etc. accept holes:[profiles] "
    "(not in revolve or loft), each strictly inside the outer.\n"
    "Example wall with a window: {\"features\":[{\"id\":\"wall\",\"op\":\"extrude\",\"plane\":\"XY\",\"profile\":"
    "{\"type\":\"rect\",\"width\":4000,\"height\":2600,\"center\":[2000,1300],\"holes\":[{\"type\":\"rect\","
    "\"width\":1200,\"height\":1000,\"center\":[2000,1500]}]},\"distance\":200}]}.\n"
    "Example bent pipe (50 mm OD, 3 mm wall, two 90 degree bends): {\"features\":[{\"id\":\"pipe\",\"op\":"
    "\"sweep\",\"profile\":{\"type\":\"circle\",\"diameter\":50,\"holes\":[{\"type\":\"circle\",\"diameter\":44}]},"
    "\"path\":[[0,0,0],[0,0,300],[200,0,300],[200,0,600]],\"bend_radius\":100}]}.\n"
    "Example tapered wing: {\"features\":[{\"id\":\"wing\",\"op\":\"loft\",\"sections\":[{\"plane\":\"XZ\","
    "\"offset\":0,\"profile\":{\"type\":\"airfoil\",\"naca\":\"2412\",\"chord\":1600}},{\"plane\":\"XZ\","
    "\"offset\":5000,\"profile\":{\"type\":\"airfoil\",\"naca\":\"2412\",\"chord\":900,\"center\":[350,0]}}]}]}"
)
