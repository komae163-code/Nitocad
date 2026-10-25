"""
Assembly mates: place a part relative to an earlier part instead of by raw coordinates.

An LLM is poor at arithmetic ("bearing z = shaft length minus ..."), and a wrong coordinate
silently becomes a floating or overlapping part. A mate states the intent ("sit on top of the
plate", "concentric with the shaft, 20 mm from its end") and deterministic code computes the
position from the real geometry.

Pure Python (no cadquery): the solver takes a bbox provider, so production code passes real
CadQuery bounding boxes and tests pass exact fake boxes.

Mate types (v1, deliberately small and exact):
  face      {"type":"face","to":A,"face":"+Z","this_face":"-Z","gap":0,"align":{"x":"center"},"offset":[0,0,0]}
            Put one face plane of this part against one face plane of A, `gap` mm away along A's face
            normal; the two tangent axes are aligned (center|min|max|none) plus optional offset.
  coaxial   {"type":"coaxial","to":A,"axis":"Z","along":"min","offset":0}
            This part's axis coincides with A's axis (both treated as bodies of revolution about the
            world axis `axis`); along the axis the chosen ends/centers line up, plus offset mm.

Limits, stated on purpose: bounding boxes are exact only for axis-aligned geometry, so both the moving
and the target part must be rotated in multiples of 90 degrees; otherwise give explicit positions.
Mates are solved in list order, so a mate may only reference an earlier part (no cycles possible).

Rotation convention matches assembly.generate_assembly: world = position + Rx(rx) * Ry(ry) * Rz(rz) * local.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable

MAX_DIM = 20_000.0
_AXES = {"X": 0, "Y": 1, "Z": 2}
_FACES = {f"{s}{a}": (i, 1 if s == "+" else -1) for a, i in _AXES.items() for s in "+-"}
_ALIGN = ("center", "min", "max", "none")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")

AABB = tuple[float, float, float, float, float, float]  # xmin, ymin, zmin, xmax, ymax, zmax


class MateError(ValueError):
    pass


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def is_quarter_turn(rotation: tuple[float, float, float] | list[float]) -> bool:
    return all(abs(r / 90.0 - round(r / 90.0)) < 1e-9 for r in rotation)


def rot_matrix(rx: float, ry: float, rz: float) -> list[list[float]]:
    """Rx * Ry * Rz (degrees), rounded so exact quarter turns give exact 0/±1 entries."""
    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    cx, sx = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    cy, sy = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    cz, sz = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    rx_m = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    ry_m = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    rz_m = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    m = mul(mul(rx_m, ry_m), rz_m)
    return [[round(v, 12) + 0.0 for v in row] for row in m]


def world_aabb(local: AABB, rotation: tuple[float, float, float],
               position: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> AABB:
    """AABB of the rotated-then-translated local box (exact for quarter-turn rotations)."""
    r = rot_matrix(*rotation)
    xs, ys, zs = [], [], []
    for x in (local[0], local[3]):
        for y in (local[1], local[4]):
            for z in (local[2], local[5]):
                p = [r[i][0] * x + r[i][1] * y + r[i][2] * z + position[i] for i in range(3)]
                xs.append(p[0]); ys.append(p[1]); zs.append(p[2])
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _lo(b: AABB, i: int) -> float:
    return b[i]


def _hi(b: AABB, i: int) -> float:
    return b[i + 3]


def _mid(b: AABB, i: int) -> float:
    return (b[i] + b[i + 3]) / 2


# -------------------------------------------------------------- validation
def validate_mate(mate: Any, label: str, errors: list[str],
                  earlier: dict[str, dict[str, Any]], rotation: tuple[float, float, float]) -> dict[str, Any] | None:
    """Structural validation. `earlier` maps already-placed part names to their normalized parts."""
    if not isinstance(mate, dict):
        errors.append(f"{label}: mate must be an object.")
        return None
    n_before = len(errors)
    mtype = mate.get("type")
    target = mate.get("to")
    if mtype not in ("face", "coaxial"):
        errors.append(f"{label}: mate type must be \"face\" or \"coaxial\", got {mtype!r}.")
        return None
    allowed = ({"type", "to", "face", "this_face", "gap", "align", "offset"} if mtype == "face"
               else {"type", "to", "axis", "along", "offset"})
    extra = set(mate) - allowed
    if extra:
        errors.append(f"{label}: unknown mate key(s) {sorted(extra)} for {mtype}.")
    if not isinstance(target, str) or target not in earlier:
        errors.append(f"{label}: mate target {target!r} must be an EARLIER part "
                      f"({', '.join(earlier) if earlier else 'none placed yet'}).")
    else:
        if not is_quarter_turn(rotation) or not is_quarter_turn(earlier[target].get("rotation", (0, 0, 0))):
            errors.append(f"{label}: mates need both parts rotated in multiples of 90 degrees "
                          "(bounding boxes are only exact then); use explicit positions otherwise.")
    out: dict[str, Any] = {"type": mtype, "to": target}

    if mtype == "face":
        face = mate.get("face")
        if face not in _FACES:
            errors.append(f"{label}: face must be one of {', '.join(_FACES)}, got {face!r}.")
            return None
        this_face = mate.get("this_face", ("-" if face[0] == "+" else "+") + face[1])
        if this_face not in _FACES:
            errors.append(f"{label}: this_face must be one of {', '.join(_FACES)}, got {this_face!r}.")
            return None
        if _FACES[this_face][0] != _FACES[face][0]:
            errors.append(f"{label}: face and this_face must be on the same axis (got {face} and {this_face}).")
        gap = mate.get("gap", 0)
        if not (_num(gap) and abs(gap) <= MAX_DIM):
            errors.append(f"{label}: gap must be a number of mm within ±{MAX_DIM:g}.")
            gap = 0
        normal = _FACES[face][0]
        tangents = [i for i in range(3) if i != normal]
        align_in = mate.get("align", {})
        align = {a: "center" for a in "xyz"}
        for i in range(3):
            if i == normal:
                align["xyz"[i]] = "none"
        if not isinstance(align_in, dict):
            errors.append(f"{label}: align must be an object like {{\"x\": \"min\"}}.")
        else:
            for k, v in align_in.items():
                if k not in ("x", "y", "z") or "xyz".index(k) not in tangents:
                    errors.append(f"{label}: align key {k!r} must be a tangent axis of the mated face.")
                elif v not in _ALIGN:
                    errors.append(f"{label}: align value {v!r} must be one of {', '.join(_ALIGN)}.")
                else:
                    align[k] = v
        off = mate.get("offset", [0, 0, 0])
        if not (isinstance(off, (list, tuple)) and len(off) == 3 and all(_num(c) and abs(c) <= MAX_DIM for c in off)):
            errors.append(f"{label}: offset must be [dx, dy, dz] in mm.")
            off = [0, 0, 0]
        elif abs(off[normal]) > 1e-12:
            errors.append(f"{label}: offset must have no component along the face normal; use gap for that.")
        out.update(face=face, this_face=this_face, gap=float(gap), align=align, offset=[float(c) for c in off])
    else:
        axis = mate.get("axis")
        if axis not in _AXES:
            errors.append(f"{label}: axis must be \"X\", \"Y\" or \"Z\", got {axis!r}.")
            return None
        along = mate.get("along", "center")
        if along not in ("center", "min", "max"):
            errors.append(f"{label}: along must be center, min or max.")
            along = "center"
        off = mate.get("offset", 0)
        if not (_num(off) and abs(off) <= MAX_DIM):
            errors.append(f"{label}: offset must be a number of mm within ±{MAX_DIM:g}.")
            off = 0
        out.update(axis=axis, along=along, offset=float(off))
    return out if len(errors) == n_before else None


# ------------------------------------------------------------------ solving
def solve_mate(mate: dict[str, Any], moving_local: AABB, rotation: tuple[float, float, float],
               target_world: AABB) -> tuple[float, float, float]:
    """Position (assembly frame) for the moving part so the mate holds."""
    b0 = world_aabb(moving_local, rotation)  # moving part's box at position (0,0,0)
    pos = [0.0, 0.0, 0.0]
    if mate["type"] == "face":
        ni, ns = _FACES[mate["face"]]
        _, ts = _FACES[mate["this_face"]]
        plane = (_hi(target_world, ni) if ns > 0 else _lo(target_world, ni)) + ns * mate["gap"]
        pos[ni] = plane - (_hi(b0, ni) if ts > 0 else _lo(b0, ni))
        for k in range(3):
            if k == ni:
                continue
            mode = mate["align"]["xyz"[k]]
            if mode == "center":
                pos[k] = _mid(target_world, k) - _mid(b0, k)
            elif mode == "min":
                pos[k] = _lo(target_world, k) - _lo(b0, k)
            elif mode == "max":
                pos[k] = _hi(target_world, k) - _hi(b0, k)
            pos[k] += mate["offset"][k]
    else:
        a = _AXES[mate["axis"]]
        for k in range(3):
            if k != a:
                pos[k] = _mid(target_world, k) - _mid(b0, k)
        ref = {"center": _mid(target_world, a) - _mid(b0, a),
               "min": _lo(target_world, a) - _lo(b0, a),
               "max": _hi(target_world, a) - _hi(b0, a)}[mate["along"]]
        pos[a] = ref + mate["offset"]
    return (pos[0], pos[1], pos[2])


def resolve_mates(parts: list[dict[str, Any]],
                  bbox_fn: Callable[[dict[str, Any]], AABB]) -> list[dict[str, Any]]:
    """Return copies of `parts` with `position` filled in for every part that has a `mate`.
    Parts without a mate keep their absolute position. Raises MateError on a bad reference."""
    placed: dict[str, AABB] = {}
    out: list[dict[str, Any]] = []
    for part in parts:
        p = dict(part)
        rot = tuple(p.get("rotation", (0.0, 0.0, 0.0)))
        mate = p.get("mate")
        local = bbox_fn(p)
        if mate:
            target = placed.get(mate["to"])
            if target is None:
                raise MateError(f"part {p['name']!r}: mate target {mate['to']!r} is not placed before it")
            p["position"] = solve_mate(mate, local, rot, target)
        placed[p["name"]] = world_aabb(local, rot, tuple(p.get("position", (0.0, 0.0, 0.0))))
        out.append(p)
    return out


MATE_HELP = (
    "Instead of position, a part may give a \"mate\" that places it against an EARLIER part (prefer mates for "
    "anything that touches or lines up; compute no coordinates yourself). "
    "{\"type\":\"face\",\"to\":\"base\",\"face\":\"+Z\",\"gap\":0} puts this part's opposite face against the "
    "target's +Z face (optional \"this_face\", \"align\":{\"x\":\"center|min|max|none\"} for the tangent axes, "
    "\"offset\":[dx,dy,dz] tangent only). {\"type\":\"coaxial\",\"to\":\"shaft\",\"axis\":\"Z\",\"along\":"
    "\"min|max|center\",\"offset\":20} makes this part concentric with the target about the world axis and lines "
    "up the chosen ends plus offset mm along +axis. Mates need both parts rotated in multiples of 90 degrees; the "
    "first part has no mate. Parts keep a normal \"position\" when they have no mate."
)
