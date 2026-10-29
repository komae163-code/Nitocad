"""
Editing an attached file: the list of edits, how they are checked, how they read in plain
words, and the rule-based reader that turns "make it 150 mm long" or "add a 6 mm hole at the
center" into an edit when no AI model is selected.

Pure Python, no CAD kernel, so it can be used by the chat planner and tested alone.

A part made from an attached file is the stored file (see import_store.py) plus an ordered
list of edits. The first edit is applied to the file as it came in, the next to the result,
and so on, so the same list always gives the same part. Edits are dictionaries:

  {"op": "scale",    "factor": 2}
  {"op": "scale_to", "axis": "largest" | "x" | "y" | "z", "mm": 150}
  {"op": "hole",     "diameter_mm": 6, "x": 10, "y": 20, "depth_mm": null, "face": "top" | "bottom"}
  {"op": "fillet",   "radius_mm": 2, "edges": "all" | "vertical" | "top" | "bottom"}
  {"op": "chamfer",  "size_mm": 1, "edges": "all" | "vertical" | "top" | "bottom"}
  {"op": "translate", "x": 0, "y": 0, "z": 0}
  {"op": "rotate",   "axis": "x" | "y" | "z", "degrees": 90}
  {"op": "mirror",   "plane": "xy" | "yz" | "xz"}
  {"op": "ground"}   mesh only: rest the lowest point on z = 0
  {"op": "center"}   mesh only: put the middle of the part over x = 0, y = 0

x and y in a hole are millimetres from the middle of the part's footprint.
Meshes (STL, OBJ) take only the moves: scale, scale_to, translate, rotate, mirror, ground and
center. Solids (STEP, IGES, BREP) and shapes extruded from drawings (DXF, SVG, KiCad, Gerber, traced pictures)
take scale, scale_to, hole, fillet, chamfer, translate, rotate and mirror. Drawings also have a
thickness.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any

BREP_KINDS = {"step", "iges", "brep"}
PROFILE_KINDS = {"dxf", "svg", "kicad", "gerber", "image"}   # "image": a traced picture (raster_trace.py)
MESH_KINDS = {"stl", "obj"}
ALL_KINDS = BREP_KINDS | PROFILE_KINDS | MESH_KINDS
MAX_EDITS = 40

SOLID_OPS = {"scale", "scale_to", "hole", "fillet", "chamfer", "translate", "rotate", "mirror"}
MESH_OPS = {"scale", "scale_to", "translate", "rotate", "mirror", "ground", "center"}
EDGE_SETS = ("all", "vertical", "top", "bottom")
THICKNESS_RANGE = (0.1, 500.0)


def ops_for(kind: str) -> set[str]:
    return MESH_OPS if kind in MESH_KINDS else SOLID_OPS


def _num(e: dict, key: str, lo: float, hi: float, i: int, default: float | None = None) -> float:
    v = e.get(key, default)
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"edit {i}: {key} must be a number") from None
    if not math.isfinite(f) or not lo <= f <= hi:
        raise ValueError(f"edit {i}: {key} must be between {lo:g} and {hi:g}")
    return f


def clean_edits(edits: Any, kind: str) -> list[dict[str, Any]]:
    """The edit list in canonical form, or ValueError with a plain message."""
    if edits is None:
        return []
    if not isinstance(edits, list):
        raise ValueError("edits must be a list")
    if len(edits) > MAX_EDITS:
        raise ValueError(f"at most {MAX_EDITS} edits")
    allowed = ops_for(kind)
    out: list[dict[str, Any]] = []
    for i, e in enumerate(edits, start=1):
        if not isinstance(e, dict):
            raise ValueError(f"edit {i} must be an object")
        op = str(e.get("op") or "").lower()
        if op not in allowed:
            nice = "a mesh file" if kind in MESH_KINDS else "this kind of file"
            raise ValueError(f"edit {i}: '{op or 'none'}' is not available for {nice}")
        if op == "scale":
            out.append({"op": op, "factor": _num(e, "factor", 0.001, 1000, i)})
        elif op == "scale_to":
            axis = str(e.get("axis") or "largest").lower()
            if axis not in ("largest", "x", "y", "z"):
                raise ValueError(f"edit {i}: axis must be largest, x, y or z")
            out.append({"op": op, "axis": axis, "mm": _num(e, "mm", 0.01, 100000, i)})
        elif op == "hole":
            depth = e.get("depth_mm")
            face = str(e.get("face") or "top").lower()
            if face not in ("top", "bottom"):
                raise ValueError(f"edit {i}: face must be top or bottom")
            out.append({"op": op, "diameter_mm": _num(e, "diameter_mm", 0.1, 1000, i),
                        "x": _num(e, "x", -100000, 100000, i, 0), "y": _num(e, "y", -100000, 100000, i, 0),
                        "depth_mm": None if depth in (None, "", "through") else _num(e, "depth_mm", 0.1, 100000, i),
                        "face": face})
        elif op in ("fillet", "chamfer"):
            key = "radius_mm" if op == "fillet" else "size_mm"
            edges = str(e.get("edges") or "all").lower()
            if edges not in EDGE_SETS:
                raise ValueError(f"edit {i}: edges must be all, vertical, top or bottom")
            out.append({"op": op, key: _num(e, key, 0.05, 1000, i), "edges": edges})
        elif op == "translate":
            out.append({"op": op, **{k: _num(e, k, -1e6, 1e6, i, 0) for k in ("x", "y", "z")}})
        elif op == "rotate":
            axis = str(e.get("axis") or "z").lower()
            if axis not in ("x", "y", "z"):
                raise ValueError(f"edit {i}: axis must be x, y or z")
            out.append({"op": op, "axis": axis, "degrees": _num(e, "degrees", -360, 360, i)})
        elif op == "mirror":
            plane = str(e.get("plane") or "").lower()
            if plane not in ("xy", "yz", "xz"):
                raise ValueError(f"edit {i}: plane must be xy, yz or xz")
            out.append({"op": op, "plane": plane})
        else:                                         # ground, center
            out.append({"op": op})
    return out


def clean_import_params(params: dict[str, Any]) -> dict[str, Any]:
    """The parameters of an imported part, checked. import_id, kind and filename are copied
    through, and an AI model must not invent them."""
    iid = str(params.get("import_id") or "")
    if not re.fullmatch(r"[0-9a-f]{8,64}", iid):
        raise ValueError("import_id is missing or not valid. Attach the file again")
    kind = str(params.get("kind") or "").lower()
    if kind not in ALL_KINDS:
        raise ValueError("kind is not a file kind that can be edited")
    out: dict[str, Any] = {"import_id": iid, "kind": kind,
                           "filename": " ".join(str(params.get("filename") or "file").split())[:120]}
    if kind in PROFILE_KINDS:
        try:
            t = float(params.get("thickness_mm", 3.0))
        except (TypeError, ValueError):
            raise ValueError("thickness_mm must be a number") from None
        lo, hi = THICKNESS_RANGE
        if not math.isfinite(t) or not lo <= t <= hi:
            raise ValueError(f"thickness_mm must be between {lo:g} and {hi:g}")
        out["thickness_mm"] = t
    out["edits"] = clean_edits(params.get("edits"), kind)
    return out


# ------------------------------------------------------------------ in words
def describe_edit(e: dict[str, Any]) -> str:
    op = e["op"]
    if op == "scale":
        return f"Scale by {e['factor']:g}"
    if op == "scale_to":
        side = {"largest": "the largest side", "x": "the length (x)", "y": "the width (y)", "z": "the height (z)"}[e["axis"]]
        return f"Scale so {side} is {e['mm']:g} mm"
    if op == "hole":
        depth = "through" if e.get("depth_mm") is None else f"{e['depth_mm']:g} mm deep"
        return f"{e['diameter_mm']:g} mm hole at x {e['x']:g}, y {e['y']:g} ({depth}, from the {e['face']})"
    if op == "fillet":
        return f"Round {e['edges']} edges with a {e['radius_mm']:g} mm fillet"
    if op == "chamfer":
        return f"Chamfer {e['edges']} edges by {e['size_mm']:g} mm"
    if op == "translate":
        return f"Move by x {e['x']:g}, y {e['y']:g}, z {e['z']:g} mm"
    if op == "rotate":
        return f"Rotate {e['degrees']:g} degrees about {e['axis']}"
    if op == "mirror":
        return f"Mirror across the {e['plane']} plane"
    return {"ground": "Rest the part on z = 0", "center": "Center the part over x = 0, y = 0"}.get(op, op)


HELP_TEXT = ("Tell me what to change. For example: \"scale it to 150 mm long\", \"make it 5 mm thick\" "
             "(for drawings), \"add a 6 mm hole at x 10 y 20\", \"add a 6 mm hole at the center\", "
             "\"fillet the edges 2 mm\", \"chamfer 1 mm\", \"rotate it 90 degrees\", or \"undo\".")

# ------------------------------------------------------------- rules reader
_NUM = r"(-?\d+(?:\.\d+)?)"
_LEN = rf"{_NUM}\s*(mm|cm|m|in|inch|inches)\b"
_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, "inches": 25.4}


def _mm(value: str, unit: str) -> float:
    return float(value) * _TO_MM[unit]


def _edge_set(t: str) -> str:
    for word in ("vertical", "top", "bottom"):
        if word in t:
            return word
    return "all"


def apply_import_edit_rules(text: str, spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Turn plain words into edits on an imported part's spec. Returns (new spec, what changed).
    Nothing changes when the words are not understood, and the caller then asks again."""
    new = copy.deepcopy(spec)
    params = new.setdefault("parameters", {})
    kind = params.get("kind", "step")
    edits: list[dict[str, Any]] = list(params.get("edits") or [])
    t = " " + text.lower().strip() + " "
    changes: list[str] = []

    if re.search(r"\b(undo|revert|remove the last (?:change|edit)|take that back)\b", t):
        if edits:
            gone = edits.pop()
            changes.append(f"removed the last edit ({describe_edit(gone).lower()})")
        params["edits"] = edits
        return new, changes

    if kind in PROFILE_KINDS:
        m = re.search(rf"(?:thickness(?: of| to| is)?|extrude(?: it)? to|set (?:the )?thickness(?: to)?)\s*{_LEN}", t) \
            or re.search(rf"{_LEN}\s*thick\b", t)
        if m:
            v = _mm(m.group(1), m.group(2))
            lo, hi = THICKNESS_RANGE
            if lo <= v <= hi:
                params["thickness_mm"] = v
                changes.append(f"thickness {v:g} mm")

    m = re.search(rf"\b(?:scale|resize|make (?:it|this)|set (?:the )?size)\b[^.]*?\bto\s*{_LEN}\s*(long|wide|tall|high|across)?", t) \
        or re.search(rf"\b(?:make (?:it|this|the part|the plate)|scale (?:it|this|the part))\s*{_LEN}\s*(long|wide|tall|high|across)\b", t)
    if m:
        axis = {"long": "x", "wide": "y", "tall": "z", "high": "z"}.get(m.group(3) or "", "largest")
        mm = _mm(m.group(1), m.group(2))
        edits.append({"op": "scale_to", "axis": axis, "mm": mm})
        changes.append(describe_edit(edits[-1]).lower())
    else:
        m = re.search(rf"\b(?:scale|enlarge|grow|shrink|resize)\b[^.]*?{_NUM}\s*(x|times|%|percent)", t) \
            or re.search(rf"\b(?:scale|enlarge|grow|shrink|resize)\b[^.]*?\bby\s*{_NUM}\b(?!\s*(?:mm|cm|m|in|inch)\b)()", t)
        if m:
            n = float(m.group(1))
            factor = n / 100.0 if m.group(2) in ("%", "percent") else n
            if 0.001 <= factor <= 1000:
                edits.append({"op": "scale", "factor": factor})
                changes.append(describe_edit(edits[-1]).lower())

    if "hole" in t and kind not in MESH_KINDS:
        d = re.search(rf"{_LEN}\s*(?:diameter\s*)?(?:round\s*)?hole", t) or re.search(rf"hole[^.]*?(?:diameter|dia|of|with)\s*{_LEN}", t)
        if d:
            at = re.search(rf"\bat\s*\(?\s*(?:x\s*[=:]?\s*)?{_NUM}\s*(?:mm)?\s*[, ]\s*(?:and\s*)?(?:y\s*[=:]?\s*)?{_NUM}", t) \
                or re.search(rf"\bx\s*[=:]?\s*{_NUM}\s*(?:mm)?[, ]+(?:and\s*)?y\s*[=:]?\s*{_NUM}", t)
            centre = re.search(r"\b(?:in|at) the (?:center|centre|middle)\b|\bcentered\b|\bcentred\b", t)
            if at or centre:
                depth = re.search(rf"{_LEN}\s*deep", t)
                edits.append({"op": "hole", "diameter_mm": _mm(d.group(1), d.group(2)),
                              "x": float(at.group(1)) if at else 0.0, "y": float(at.group(2)) if at else 0.0,
                              "depth_mm": _mm(depth.group(1), depth.group(2)) if depth else None,
                              "face": "bottom" if re.search(r"\bfrom (?:the )?(?:bottom|underneath|below)\b", t) else "top"})
                changes.append(describe_edit(edits[-1]).lower())

    if kind not in MESH_KINDS:
        m = re.search(rf"\b(?:fillet|round(?: off)?)\b[^.]*?{_LEN}", t)
        if m:
            edits.append({"op": "fillet", "radius_mm": _mm(m.group(1), m.group(2)), "edges": _edge_set(t)})
            changes.append(describe_edit(edits[-1]).lower())
        m = re.search(rf"\b(?:chamfer|bevel)\b[^.]*?{_LEN}", t)
        if m:
            edits.append({"op": "chamfer", "size_mm": _mm(m.group(1), m.group(2)), "edges": _edge_set(t)})
            changes.append(describe_edit(edits[-1]).lower())

    m = re.search(rf"\brotate\b[^.]*?{_NUM}\s*(?:°|deg|degrees)", t)
    if m:
        ax = re.search(r"\b(?:about|around|along)?\s*(?:the )?\b([xyz])[- ]?axis|\babout ([xyz])\b|\baround ([xyz])\b", t)
        axis = next((g for g in (ax.groups() if ax else ()) if g), "z")
        edits.append({"op": "rotate", "axis": axis, "degrees": float(m.group(1))})
        changes.append(describe_edit(edits[-1]).lower())

    m = re.search(rf"\b(?:move|shift|translate|slide)\b[^.]*?{_LEN}[^.]*?\b(?:in|along|on|to)\s*(?:the )?([xyz])\b", t)
    if m:
        edits.append({"op": "translate", **{k: 0.0 for k in "xyz"}, m.group(3): _mm(m.group(1), m.group(2))})
        changes.append(describe_edit(edits[-1]).lower())

    if re.search(r"\bmirror\b", t):
        pl = re.search(r"\b(xy|yz|xz)\b", t)
        if pl:
            edits.append({"op": "mirror", "plane": pl.group(1)})
            changes.append(describe_edit(edits[-1]).lower())

    if kind in MESH_KINDS:
        if re.search(r"\b(?:ground it|on the ground|sit on|rest on|put it on z|flat on the floor)\b", t):
            edits.append({"op": "ground"})
            changes.append("rest the part on z = 0")
        if re.search(r"\bcent(?:er|re)(?: it| the part)?\b(?! hole)", t) and "hole" not in t:
            edits.append({"op": "center"})
            changes.append("center the part")

    if len(edits) > MAX_EDITS:
        edits = edits[-MAX_EDITS:]
    params["edits"] = edits
    return new, changes
