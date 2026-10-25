"""
Triangle meshes (STL and OBJ): read, measure, move and write back out.

A mesh is a pile of triangles, not a solid with faces and edges, so this engine only does what
can be done to triangles without guessing: scale, rotate, move, mirror, rest on the ground,
center, and write a binary STL. Holes, fillets and chamfers are not possible on a mesh. To get
those, rebuild the shape as a solid (STEP) in another tool or describe it here as a new part.

Triangles are held as one (n, 3, 3) float array: n triangles, 3 corners, x y z in millimetres.
numpy only, no CAD kernel.
"""
from __future__ import annotations

import math
import re
import struct
from typing import Any

import numpy as np

MAX_TRIANGLES = 300_000


class MeshError(ValueError):
    """The mesh cannot be read or changed. The message is safe to show."""


def _check(tris: np.ndarray) -> np.ndarray:
    if tris.ndim != 3 or tris.shape[1:] != (3, 3) or len(tris) == 0:
        raise MeshError("The mesh has no triangles")
    if len(tris) > MAX_TRIANGLES:
        raise MeshError(f"The mesh has more than {MAX_TRIANGLES:,} triangles. Simplify it first")
    if not np.isfinite(tris).all():
        raise MeshError("The mesh has invalid (not a number) coordinates")
    return tris.astype(np.float64)


def parse_stl(data: bytes) -> np.ndarray:
    # Binary STL: 80 byte header, a count, then 50 bytes per triangle. Some binary files start
    # with the word "solid", so the size is what decides.
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * n == len(data):
            if n > MAX_TRIANGLES:
                raise MeshError(f"The mesh has more than {MAX_TRIANGLES:,} triangles. Simplify it first")
            arr = np.frombuffer(data, dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]), count=n, offset=84)
            return _check(arr["v"].reshape(-1, 3, 3).astype(np.float64))
    text = data.decode("utf-8", "ignore")
    if "vertex" not in text:
        raise MeshError("This does not look like an STL file")
    nums = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
    try:
        v = np.array(nums, dtype=np.float64)
    except ValueError:
        raise MeshError("The STL file has unreadable coordinates") from None
    if len(v) % 3:
        raise MeshError("The STL file is cut short")
    return _check(v.reshape(-1, 3, 3))


def parse_obj(data: bytes) -> np.ndarray:
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[int, int, int]] = []
    for line in data.decode("utf-8", "ignore").splitlines():
        if line.startswith("v "):
            p = line.split()
            try:
                verts.append((float(p[1]), float(p[2]), float(p[3])))
            except (IndexError, ValueError):
                raise MeshError("The OBJ file has an unreadable vertex") from None
        elif line.startswith("f "):
            idx = []
            for tok in line.split()[1:]:
                try:
                    i = int(tok.split("/")[0])
                except ValueError:
                    raise MeshError("The OBJ file has an unreadable face") from None
                idx.append(i - 1 if i > 0 else len(verts) + i)
            for k in range(1, len(idx) - 1):            # fan triangulation
                tris.append((idx[0], idx[k], idx[k + 1]))
        if len(tris) > MAX_TRIANGLES * 2:
            raise MeshError(f"The mesh has more than {MAX_TRIANGLES:,} triangles. Simplify it first")
    if not verts or not tris:
        raise MeshError("This does not look like an OBJ file with faces")
    v = np.array(verts, dtype=np.float64)
    f = np.array(tris, dtype=np.int64)
    if f.min() < 0 or f.max() >= len(v):
        raise MeshError("The OBJ file refers to a vertex that does not exist")
    return _check(v[f])


def write_stl(tris: np.ndarray, name: str = "nitocad") -> bytes:
    """Binary STL with a normal for every triangle."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    n = np.where(ln > 0, n / np.where(ln > 0, ln, 1), 0)
    rec = np.zeros(len(tris), dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))
    rec["n"] = n
    rec["v"] = tris.reshape(-1, 9)
    return name.encode("ascii", "ignore")[:80].ljust(80, b" ") + struct.pack("<I", len(tris)) + rec.tobytes()


def bbox(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    flat = tris.reshape(-1, 3)
    return flat.min(axis=0), flat.max(axis=0)


def stats(tris: np.ndarray) -> dict[str, Any]:
    lo, hi = bbox(tris)
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    area = float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())
    # Closed if every edge is shared by exactly two triangles
    key = np.round(tris.reshape(-1, 3) * 1e4).astype(np.int64)
    _, ids = np.unique(key, axis=0, return_inverse=True)
    ids = np.asarray(ids).reshape(-1, 3)
    edges = np.sort(np.vstack([ids[:, [0, 1]], ids[:, [1, 2]], ids[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    watertight = bool((counts == 2).all())
    volume = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
    return {"triangles": int(len(tris)), "size_mm": [float(x) for x in (hi - lo)], "surface_area_mm2": area,
            "watertight": watertight, "volume_mm3": abs(volume) if watertight else None}


# ------------------------------------------------------------------- edits
def _axis_matrix(axis: str, degrees: float) -> np.ndarray:
    t = math.radians(degrees)
    c, s = math.cos(t), math.sin(t)
    return {"x": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
            "y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def apply_edits(tris: np.ndarray, edits: list[dict[str, Any]]) -> np.ndarray:
    """Apply checked edits (see import_ops.clean_edits) in order and return new triangles."""
    t = tris.copy()
    for e in edits:
        op = e["op"]
        if op == "scale":
            t = t * float(e["factor"])
        elif op == "scale_to":
            lo, hi = bbox(t)
            size = hi - lo
            dim = float(size.max()) if e["axis"] == "largest" else float(size["xyz".index(e["axis"])])
            if dim <= 1e-12:
                raise MeshError("The part has no size along that direction, so it cannot be scaled to a size")
            t = t * (float(e["mm"]) / dim)
        elif op == "translate":
            t = t + np.array([e["x"], e["y"], e["z"]], dtype=np.float64)
        elif op == "rotate":
            t = t @ _axis_matrix(e["axis"], float(e["degrees"])).T
        elif op == "mirror":
            sign = np.array({"xy": [1, 1, -1], "yz": [-1, 1, 1], "xz": [1, -1, 1]}[e["plane"]], dtype=np.float64)
            t = (t * sign)[:, [0, 2, 1]]               # swapping two corners keeps the triangles facing outward
        elif op == "ground":
            t = t - np.array([0, 0, bbox(t)[0][2]])
        elif op == "center":
            lo, hi = bbox(t)
            t = t - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 0])
        else:
            raise MeshError(f"'{op}' cannot be done on a mesh")
    return _check(t)
