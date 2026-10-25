"""
Survey point files: parse text into clean (x, y, z) points.

Accepted layouts (comma, tab, semicolon or space separated; # or // comments):

  with a header row   names are matched, so any column order works:
                        x / e / east / easting         -> x
                        y / n / north / northing       -> y
                        z / elev / elevation / rl / h  -> z
                        id / p / pt / point / name     -> point id (optional)
                        code / desc / description      -> feature code (optional)
  without a header    3 columns = x y z
                      4 columns = id x y z
                      5+ columns = id x y z code

x is easting and y is northing, both in the units given (metres or feet).
Everything is converted to metres.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from .errors import CivilInputError

FT_TO_M = 0.3048
MAX_POINTS_DEFAULT = 3000
DUPLICATE_TOL_M = 1e-6

_X = {"x", "e", "east", "easting", "eastings"}
_Y = {"y", "n", "north", "northing", "northings"}
_Z = {"z", "elev", "elevation", "rl", "h", "height", "level"}
_ID = {"id", "p", "pt", "point", "name", "no", "number"}
_CODE = {"code", "desc", "description"}


@dataclass
class SurveyPoint:
    id: str
    x: float
    y: float
    z: float
    code: str = ""


@dataclass
class SurveyResult:
    points: list[SurveyPoint]
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _is_float(tok: str) -> bool:
    try:
        v = float(tok)
    except ValueError:
        return False
    return math.isfinite(v)


def _split(line: str, delim: str | None) -> list[str]:
    if delim is None:
        return line.split()
    return [t.strip() for t in line.split(delim)]


def _detect_delimiter(line: str) -> str | None:
    for d in (",", "\t", ";"):
        if d in line:
            return d
    return None


def parse_points(text: str, *, units: str = "m", max_points: int = MAX_POINTS_DEFAULT) -> SurveyResult:
    if units not in ("m", "ft"):
        raise CivilInputError("units must be 'm' or 'ft'")
    factor = FT_TO_M if units == "ft" else 1.0

    lines = []
    for n, raw in enumerate((text or "").splitlines(), start=1):
        s = raw.strip().lstrip("\ufeff")
        if not s or s.startswith("#") or s.startswith("//"):
            continue
        lines.append((n, s))
    if not lines:
        raise CivilInputError("The survey file is empty")

    delim = _detect_delimiter(lines[0][1])
    first_tokens = _split(lines[0][1], delim)
    has_header = not any(_is_float(t) for t in first_tokens)

    col = {"x": 0, "y": 1, "z": 2, "id": None, "code": None}
    body = lines
    if has_header:
        names = [t.lower().strip() for t in first_tokens]
        found: dict[str, int] = {}
        for i, nm in enumerate(names):
            for key, group in (("x", _X), ("y", _Y), ("z", _Z), ("id", _ID), ("code", _CODE)):
                if nm in group and key not in found:
                    found[key] = i
        if not {"x", "y", "z"} <= found.keys():
            raise CivilInputError(
                "The header row needs x (or easting), y (or northing) and z (or elevation) columns"
            )
        col = {"x": found["x"], "y": found["y"], "z": found["z"],
               "id": found.get("id"), "code": found.get("code")}
        body = lines[1:]
    else:
        width = len(first_tokens)
        if width < 3:
            raise CivilInputError("Each survey line needs at least x, y and z")
        if width == 3:
            col = {"x": 0, "y": 1, "z": 2, "id": None, "code": None}
        elif width == 4:
            col = {"x": 1, "y": 2, "z": 3, "id": 0, "code": None}
        else:
            col = {"x": 1, "y": 2, "z": 3, "id": 0, "code": 4}

    warnings: list[str] = []
    points: list[SurveyPoint] = []
    bad_lines: list[int] = []
    need = max(v for v in col.values() if v is not None) + 1

    for n, s in body:
        toks = _split(s, delim)
        if len(toks) < need or not all(_is_float(toks[col[k]]) for k in ("x", "y", "z")):
            bad_lines.append(n)
            continue
        pid = toks[col["id"]] if col["id"] is not None and col["id"] < len(toks) else str(len(points) + 1)
        code = toks[col["code"]] if col["code"] is not None and col["code"] < len(toks) else ""
        points.append(SurveyPoint(
            id=pid or str(len(points) + 1),
            x=float(toks[col["x"]]) * factor,
            y=float(toks[col["y"]]) * factor,
            z=float(toks[col["z"]]) * factor,
            code=code,
        ))
        if len(points) > max_points:
            raise CivilInputError(f"More than {max_points} survey points. Thin the data or split the site")

    if bad_lines:
        shown = ", ".join(str(n) for n in bad_lines[:5])
        more = f" and {len(bad_lines) - 5} more" if len(bad_lines) > 5 else ""
        warnings.append(f"Skipped {len(bad_lines)} line(s) that were not numbers or had too few columns (line {shown}{more})")
    if len(points) < 3:
        raise CivilInputError("At least 3 valid survey points are needed to make a surface")

    # Duplicates in plan: keep the first, warn if the heights disagree.
    seen: dict[tuple[int, int], SurveyPoint] = {}
    unique: list[SurveyPoint] = []
    dropped = conflicting = 0
    scale = 1.0 / DUPLICATE_TOL_M
    for p in points:
        key = (round(p.x * scale), round(p.y * scale))
        if key in seen:
            dropped += 1
            if abs(seen[key].z - p.z) > 1e-3:
                conflicting += 1
            continue
        seen[key] = p
        unique.append(p)
    if dropped:
        msg = f"Dropped {dropped} duplicate point(s) at the same position"
        if conflicting:
            msg += f", {conflicting} with a different height (kept the first)"
        warnings.append(msg)
    points = unique
    if len(points) < 3:
        raise CivilInputError("At least 3 points at different positions are needed")

    zs = [p.z for p in points]
    med = statistics.median(zs)
    mad = statistics.median(abs(z - med) for z in zs)
    if mad > 0:
        outliers = [p for p in points if abs(p.z - med) > 10 * 1.4826 * mad]
        if outliers:
            ids = ", ".join(p.id for p in outliers[:5])
            warnings.append(f"{len(outliers)} point(s) are far from the typical height, check them: {ids}")

    xs = [p.x for p in points]
    ys = [p.y for p in points]
    stats = {
        "points": len(points),
        "x_min": min(xs), "x_max": max(xs),
        "y_min": min(ys), "y_max": max(ys),
        "z_min": min(zs), "z_max": max(zs),
        "bbox_area_m2": (max(xs) - min(xs)) * (max(ys) - min(ys)),
    }
    return SurveyResult(points=points, warnings=warnings, stats=stats)
