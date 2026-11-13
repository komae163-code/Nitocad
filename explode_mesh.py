"""
Exploded view from the REAL parts, for the studio's Explode slider.

The slider used to cut the one merged preview mesh into "islands" by which triangles touch. That cannot work for an
assembly: parts that touch fuse into one island, and a part with two separate pieces falls into two. This module takes
each part's own triangles instead (so the pieces are exactly the parts in the parts list), works out how far each part
must move to clear the others (assembly_exploded.explode), and returns everything the viewer needs.

    out = build(meshes, directions=..., fixed=...)          # pure: numpy only, no CAD kernel
    meshes = kernel_part_meshes(parts_spec)                  # needs CadQuery: {name: (vertices, triangles)}

Coordinates are millimetres in the CAD frame (X long, Y lateral, Z up), shifted so the whole assembly is centred on the
origin: the viewer applies the same rotateX(-90 deg) it applies to the STL preview, so the two line up.
"""
from __future__ import annotations

import base64
from typing import Any, Iterable

import assembly_exploded as AE
import part_naming

MAX_TRIANGLES = 450_000          # above this the mesh is tessellated again more coarsely
MAX_FACTOR = 8.0


def _np():
    import numpy as np
    return np


def _boxes(meshes: dict[str, tuple[Any, Any]]) -> dict[str, dict]:
    np = _np()
    out = {}
    for name, (v, _t) in meshes.items():
        a = np.asarray(v, dtype=float).reshape(-1, 3)
        out[name] = {"min": tuple(float(x) for x in a.min(axis=0)), "max": tuple(float(x) for x in a.max(axis=0))}
    return out


def _encode(verts: Any, tris: Any, centre: Iterable[float]) -> str:
    """Triangle soup (9 float32 per triangle, centred) as base64."""
    np = _np()
    v = np.asarray(verts, dtype=np.float64).reshape(-1, 3) - np.asarray(list(centre), dtype=np.float64)
    f = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
    soup = v[f].astype(np.float32)
    return base64.b64encode(soup.tobytes()).decode("ascii")


def build(meshes: dict[str, tuple[Any, Any]], *, directions: dict[str, Iterable[float]] | None = None,
          fixed: str | None = None, intended_contact: Iterable[tuple[str, str]] = ()) -> dict[str, Any]:
    """Viewer JSON: {"ok", "diag", "parts": [{"name", "tris", "pos", "off"}], "warnings", "sequence"}.
    `off` is how far the part moves, in mm, at the end of the slider; the viewer scales it by the slider value."""
    np = _np()
    meshes = {n: m for n, m in meshes.items() if len(m[1]) > 0}
    if len(meshes) < 2:
        return {"ok": False, "errors": ["an exploded view needs at least two parts"]}
    boxes = _boxes(meshes)
    dirs = {k: v for k, v in (directions or {}).items() if k in boxes}
    res = AE.explode(boxes, fixed=fixed if fixed in boxes else None, directions=dirs,
                     intended_contact=[tuple(x) for x in intended_contact if len(x) == 2], max_factor=MAX_FACTOR)
    if not res["ok"]:
        return {"ok": False, "errors": res["errors"]}
    lo = [min(b["min"][i] for b in boxes.values()) for i in range(3)]
    hi = [max(b["max"][i] for b in boxes.values()) for i in range(3)]
    centre = [(lo[i] + hi[i]) / 2 for i in range(3)]
    diag = float(np.sqrt(sum((hi[i] - lo[i]) ** 2 for i in range(3)))) or 1.0
    parts = []
    for name, (v, t) in meshes.items():
        off = res["offsets"].get(name, (0.0, 0.0, 0.0))
        parts.append({"name": name, "label": part_naming.readable(name), "tris": int(len(t)), "pos": _encode(v, t, centre),
                      "off": [round(float(x), 3) for x in off]})
    warnings = []
    if res["unseparated"]:
        warnings.append("still overlapping when pulled apart: " + ", ".join("/".join(p) for p in res["unseparated"][:6]))
    if res["no_direction"]:
        warnings.append("no pull direction for: " + ", ".join(res["no_direction"][:8]))
    return {"ok": True, "diag": diag, "parts": parts, "warnings": warnings, "sequence": res.get("sequence", []),
            "factor": res["factor"]}


def kernel_part_meshes(parts_spec: list[dict[str, Any]]) -> tuple[dict[str, tuple[Any, Any]], list[str]]:
    """Tessellate every part of a placed parts list with CadQuery. Returns ({name: (vertices, triangles)}, notes).
    Raises ImportError when CadQuery is not installed."""
    import assembly
    asm = assembly.generate_assembly(parts_spec)
    shapes = assembly.get_assembly_parts(asm)
    notes: list[str] = []
    wanted = {str(p.get("name")) for p in parts_spec}
    got = {n for n, _s in shapes}
    for n in sorted(wanted - got):
        notes.append(f"{n} could not be built and is not shown")

    boxes = [sh.BoundingBox() for _n, sh in shapes]
    extent = max(max(b.xmax for b in boxes) - min(b.xmin for b in boxes),
                 max(b.ymax for b in boxes) - min(b.ymin for b in boxes),
                 max(b.zmax for b in boxes) - min(b.zmin for b in boxes)) if boxes else 1.0

    def tess(scale: float) -> dict[str, tuple[Any, Any]]:
        tol = max(0.1, extent * 0.0007 * scale)            # 0.3 mm on a 450 mm model, 2.8 mm on a 4 m car
        out: dict[str, tuple[Any, Any]] = {}
        for name, shape in shapes:
            verts, tris = shape.tessellate(tol, 0.3)
            if verts and tris:
                out[name] = ([(float(p.x), float(p.y), float(p.z)) for p in verts], [tuple(t) for t in tris])
        return out

    meshes = tess(1.0)
    if sum(len(t) for _v, t in meshes.values()) > MAX_TRIANGLES:
        meshes = tess(4.0)
        notes.append("shown in a coarser mesh to keep it light")
    return meshes, notes
