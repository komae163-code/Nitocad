"""
Reference match score: how much a generated part overlaps a reference file (STEP, IGES, BREP, STL or OBJ).

    match % = shared volume / combined volume   (intersection over union, 100 = the same solid)

How it works, and what it does not do:

  * Both solids are triangle meshes (a STEP is tessellated first, in an isolated process, see
    match_upload). Each is turned into voxels by casting vertical rays through a shared grid and filling
    between entry and exit, so the shared volume is a count of cells that are inside both.
  * The score is APPROXIMATE. `cell_mm` in the result is the grid step; the error is about one cell on
    every surface. The grid is sized so the thinnest wall has at least 8 cells across it.
  * The reference is aligned by its bounding-box centre, and turned in 90 degree steps (24 of them) to the
    way that scores best. It is not rotated by any other angle: a part drawn at 30 degrees scores low, and
    the result says that.
  * Both files are read as millimetres. If the reference looks like inches, centimetres or metres the
    result says so instead of silently rescaling.
  * The shared volume here is counted on a grid, not by the CAD kernel's boolean. A boolean is exact but
    fails on dirty uploaded solids and has no time bound; the grid works the same for STEP and STL and
    always finishes. An exact refinement for two clean B-rep solids can be added behind the same result.

Pure numpy. No CadQuery is needed to score two meshes.
"""
from __future__ import annotations

import itertools
import math
import os
import tempfile
from typing import Any

import numpy as np

MAX_TRIANGLES = 400_000
FINE_CELLS = 1_500_000        # target grid cells for the final score
COARSE_CELLS = 60_000         # target grid cells when ranking orientations
HARD_CELLS = 6_000_000        # never build a grid bigger than this
MAX_RAY_HITS = 8_000_000      # ray-triangle candidate pairs per pass
MIN_CELLS_ACROSS_THIN = 8
UNIT_FACTORS = ((25.4, "inches"), (10.0, "centimetres"), (1000.0, "metres"))


class MatchError(ValueError):
    """The files cannot be scored. The message is written for the person."""


# ---------------------------------------------------------------- orientations ---

def rotations() -> list[np.ndarray]:
    """The 24 ways to turn a solid in 90 degree steps (signed axis permutations with determinant +1)."""
    out = []
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            m = np.zeros((3, 3))
            for row, (col, s) in enumerate(zip(perm, signs)):
                m[row, col] = s
            if round(np.linalg.det(m)) == 1:
                out.append(m)
    return out


ROTATIONS = rotations()


# --------------------------------------------------------------------- voxels ---

def _bbox(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    flat = tris.reshape(-1, 3)
    return flat.min(axis=0), flat.max(axis=0)


def voxelize(tris: np.ndarray, lo: np.ndarray, step: float, shape: tuple[int, int, int]) -> np.ndarray:
    """Boolean grid (nx, ny, nz) of cells whose centre is inside the closed mesh `tris` (n, 3, 3).

    A vertical ray is cast through each cell column (offset by a hair so it never runs along an edge shared
    by two triangles). Entries and exits are paired in order and the cells between them are filled.
    Raises MatchError when the mesh is not closed (rays that enter without leaving)."""
    nx, ny, nz = shape
    ncols = nx * ny
    # sample point of column (i, j); the tiny offsets are irrational-ish so no ray lands on a shared edge
    ex, ey = step * 0.000731, step * 0.000417
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    xs = np.stack([a[:, 0], b[:, 0], c[:, 0]], axis=1)
    ys = np.stack([a[:, 1], b[:, 1], c[:, 1]], axis=1)
    ix0 = np.clip(np.ceil((xs.min(axis=1) - lo[0] - ex) / step - 0.5).astype(np.int64), 0, nx)
    ix1 = np.clip(np.floor((xs.max(axis=1) - lo[0] - ex) / step - 0.5).astype(np.int64), -1, nx - 1)
    iy0 = np.clip(np.ceil((ys.min(axis=1) - lo[1] - ey) / step - 0.5).astype(np.int64), 0, ny)
    iy1 = np.clip(np.floor((ys.max(axis=1) - lo[1] - ey) / step - 0.5).astype(np.int64), -1, ny - 1)
    w = np.maximum(ix1 - ix0 + 1, 0)
    h = np.maximum(iy1 - iy0 + 1, 0)
    count = w * h
    total = int(count.sum())
    if total == 0:
        return np.zeros(shape, dtype=bool)
    if total > MAX_RAY_HITS:
        raise MatchError("The model is too detailed to compare. Try a simpler file.")
    tri = np.repeat(np.arange(len(tris)), count)
    start = np.cumsum(count) - count
    k = np.arange(total) - np.repeat(start, count)
    ww = w[tri]
    ci = ix0[tri] + k % ww
    cj = iy0[tri] + k // ww
    px = lo[0] + (ci + 0.5) * step + ex
    py = lo[1] + (cj + 0.5) * step + ey

    ta, tb, tc = a[tri], b[tri], c[tri]
    d1x, d1y = tb[:, 0] - ta[:, 0], tb[:, 1] - ta[:, 1]
    d2x, d2y = tc[:, 0] - ta[:, 0], tc[:, 1] - ta[:, 1]
    det = d1x * d2y - d1y * d2x
    ok = np.abs(det) > 1e-14                      # a triangle seen edge-on has no footprint to hit
    det_safe = np.where(ok, det, 1.0)
    qx, qy = px - ta[:, 0], py - ta[:, 1]
    u = (qx * d2y - qy * d2x) / det_safe
    v = (d1x * qy - d1y * qx) / det_safe
    hit = ok & (u >= 0) & (v >= 0) & (u + v < 1)
    z = ta[:, 2] + u * (tb[:, 2] - ta[:, 2]) + v * (tc[:, 2] - ta[:, 2])
    keys = (ci[hit] * ny + cj[hit]).astype(np.int64)
    zs = z[hit]
    if keys.size == 0:
        return np.zeros(shape, dtype=bool)
    order = np.lexsort((zs, keys))
    keys, zs = keys[order], zs[order]
    # position of each crossing within its own column: even = entering, odd = leaving
    first = np.r_[True, keys[1:] != keys[:-1]]
    col_start = np.maximum.accumulate(np.where(first, np.arange(len(keys)), 0))
    pos = np.arange(len(keys)) - col_start
    per_col = np.bincount(keys, minlength=ncols)
    odd_cols = int(((per_col % 2) == 1).sum())
    if odd_cols > max(2, 0.02 * int((per_col > 0).sum())):
        raise MatchError("One of the models is not a closed solid (it has gaps), so its volume cannot be compared.")
    keep = (per_col[keys] % 2 == 0)               # a stray odd column is dropped, not guessed at
    keys, zs, pos = keys[keep], zs[keep], pos[keep]
    enter = pos % 2 == 0
    z_in, z_out, kk = zs[enter], zs[~enter][: enter.sum()], keys[enter]
    if len(z_out) != len(z_in):
        raise MatchError("One of the models is not a closed solid, so its volume cannot be compared.")
    k0 = np.clip(np.ceil((z_in - lo[2]) / step - 0.5).astype(np.int64), 0, nz)
    k1 = np.clip(np.floor((z_out - lo[2]) / step - 0.5).astype(np.int64), -1, nz - 1)
    live = k1 >= k0
    diff = np.zeros(ncols * (nz + 1), dtype=np.int32)
    np.add.at(diff, kk[live] * (nz + 1) + k0[live], 1)
    np.add.at(diff, kk[live] * (nz + 1) + k1[live] + 1, -1)
    occ = np.cumsum(diff.reshape(ncols, nz + 1), axis=1)[:, :nz] > 0
    return occ.reshape(nx, ny, nz)


def _grid(lo: np.ndarray, hi: np.ndarray, thin: float, cells: int) -> tuple[float, tuple[int, int, int], np.ndarray]:
    """Cell size and grid for the box lo..hi: about `cells` cells, fine enough across the thinnest wall."""
    ext = np.maximum(hi - lo, 1e-6)
    step = (float(np.prod(ext)) / cells) ** (1 / 3)
    if thin > 0:
        step = min(step, thin / MIN_CELLS_ACROSS_THIN)
    while True:
        shape = tuple(int(math.ceil(e / step)) + 2 for e in ext)
        if shape[0] * shape[1] * shape[2] <= HARD_CELLS:
            break
        step *= 1.15
    origin = lo - (np.array(shape) * step - ext) / 2.0   # centre the box in the grid
    return step, shape, origin


def open_edge_fraction(tris: np.ndarray) -> float:
    """Share of edges that belong to only one triangle. A closed solid has none. The rays only catch gaps in
    faces that face up or down, so this catches a missing side wall too. A tessellated STEP can show a
    sliver of these where curved faces meet, which is why the caller allows a little."""
    key = np.round(tris.reshape(-1, 3) * 1e3).astype(np.int64)
    _, ids = np.unique(key, axis=0, return_inverse=True)
    ids = np.asarray(ids).reshape(-1, 3)
    edges = np.sort(np.vstack([ids[:, [0, 1]], ids[:, [1, 2]], ids[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return float((counts == 1).sum()) / float(len(counts))


MAX_OPEN_EDGES = 0.02


def _thin(tris: np.ndarray) -> float:
    lo, hi = _bbox(tris)
    d = hi - lo
    d = d[d > 1e-9]
    return float(d.min()) if d.size else 0.0


def overlap(a: np.ndarray, b: np.ndarray, cells: int, thin: float) -> dict[str, float]:
    """Voxel volumes of a, b and their intersection, in mm3. Both meshes are already in the same frame."""
    lo = np.minimum(_bbox(a)[0], _bbox(b)[0])
    hi = np.maximum(_bbox(a)[1], _bbox(b)[1])
    step, shape, origin = _grid(lo, hi, thin, cells)
    va = voxelize(a, origin, step, shape)
    vb = voxelize(b, origin, step, shape)
    cell = step ** 3
    return {"a": float(va.sum()) * cell, "b": float(vb.sum()) * cell, "common": float((va & vb).sum()) * cell, "cell_mm": step}


# ------------------------------------------------------------------ comparison ---

def _signed_volume(tris: np.ndarray) -> float:
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    return abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0))


def _placed(ref: np.ndarray, rot: np.ndarray, centre: np.ndarray) -> np.ndarray:
    """The reference turned by `rot` about its own bounding-box centre, then moved onto `centre`."""
    lo, hi = _bbox(ref)
    mid = (lo + hi) / 2.0
    return (ref - mid) @ rot.T + centre


def _dim_error(dims_ref: np.ndarray, rot: np.ndarray, dims_gen: np.ndarray) -> float:
    turned = np.abs(rot) @ dims_ref
    return float(np.max(np.abs(turned - dims_gen) / np.maximum(dims_gen, 1e-6)))


def _verdict(pct: float) -> str:
    if pct >= 90:
        return "Very close"
    if pct >= 70:
        return "Close"
    if pct >= 40:
        return "Partly matches"
    return "Different"


def _unit_hint(dims_ref: np.ndarray, dims_gen: np.ndarray) -> str | None:
    """Say so when the reference would match if it were in other units. It is never rescaled for the score."""
    g, r = np.sort(dims_gen), np.sort(dims_ref)

    def close(x):
        return bool(np.max(np.abs(x - g) / np.maximum(g, 1e-6)) <= 0.05)

    if close(r):
        return None
    for factor, name in UNIT_FACTORS:
        if close(r * factor):
            return f"The reference looks like it is drawn in {name}, but both files are read as millimetres. Re-export it in millimetres to compare."
        if close(r / factor):
            return f"The reference is about {factor:g} times larger than your part, as if it were exported in the wrong units. Re-export it in millimetres to compare."
    return None


def compare(gen: np.ndarray, ref: np.ndarray) -> dict[str, Any]:
    """Score the generated mesh `gen` against the reference mesh `ref` (both (n, 3, 3), millimetres)."""
    for name, t in (("generated part", gen), ("reference", ref)):
        if t.ndim != 3 or t.shape[1:] != (3, 3) or len(t) == 0:
            raise MatchError(f"The {name} has no surface to compare.")
        if len(t) > MAX_TRIANGLES:
            raise MatchError(f"The {name} has more than {MAX_TRIANGLES:,} triangles. Try a simpler file.")
    for name, t in (("generated part", gen), ("reference", ref)):
        if open_edge_fraction(t) > MAX_OPEN_EDGES:
            raise MatchError(f"The {name} is not a closed solid (it has gaps), so its volume cannot be compared.")
    glo, ghi = _bbox(gen)
    rlo, rhi = _bbox(ref)
    dims_g, dims_r = ghi - glo, rhi - rlo
    centre = (glo + ghi) / 2.0
    thin = min(_thin(gen), _thin(ref))

    # rank the orientations cheaply, keep the ones whose sizes line up, then score the best two finely
    errs = sorted(((_dim_error(dims_r, r, dims_g), i) for i, r in enumerate(ROTATIONS)), key=lambda t: t[0])
    close = [i for e, i in errs if e <= max(0.08, 2.0 / max(float(dims_g.max()), 1.0))]
    candidates = close or [i for _, i in errs[:3]]
    ranked = []
    for i in candidates:
        o = overlap(gen, _placed(ref, ROTATIONS[i], centre), COARSE_CELLS, thin)
        union = o["a"] + o["b"] - o["common"]
        ranked.append((o["common"] / union if union > 0 else 0.0, i))
    ranked.sort(reverse=True)
    best = None
    for _, i in ranked[:2]:
        o = overlap(gen, _placed(ref, ROTATIONS[i], centre), FINE_CELLS, thin)
        union = o["a"] + o["b"] - o["common"]
        iou = o["common"] / union if union > 0 else 0.0
        if best is None or iou > best[0]:
            best = (iou, i, o)
    iou, idx, o = best
    pct = round(100.0 * iou, 1)
    turned = np.abs(ROTATIONS[idx]) @ dims_r
    vg, vr = _signed_volume(gen), _signed_volume(ref)
    notes = ["Approximate: counted on a grid of %.2f mm cells. Both files are read as millimetres." % o["cell_mm"],
             "The reference is lined up by its bounding-box centre and turned in 90 degree steps only."]
    hint = _unit_hint(dims_r, dims_g)
    if hint:
        notes.insert(0, hint)
    if pct < 40 and not hint:
        notes.append("A low score can also mean the reference is drawn at an angle, which this does not correct.")
    return {
        "match_percent": pct, "verdict": _verdict(pct), "method": "voxel overlap",
        "cell_mm": round(o["cell_mm"], 3),
        "generated_volume_mm3": round(vg, 1), "reference_volume_mm3": round(vr, 1),
        "overlap_volume_mm3": round(o["common"], 1),
        "volume_ratio": round(vg / vr, 3) if vr > 0 else None,
        "size_generated_mm": [round(float(x), 2) for x in dims_g],
        "size_reference_mm": [round(float(x), 2) for x in turned],
        "size_difference_mm": [round(float(x), 2) for x in (dims_g - turned)],
        "orientation_index": int(idx), "aligned_by": "bounding box centre", "notes": notes,
    }


def sentence(result: dict) -> str:
    """One plain line for the chat."""
    s = f"{result['verdict']}: {result['match_percent']:g}% of the combined volume is shared with your reference."
    if result.get("volume_ratio"):
        s += f" Your part has {result['volume_ratio']:.2f} times the reference's volume."
    return s


# ------------------------------------------------------- file reading (child process) ---

def _brep_file_to_tris(path: str, kind: str) -> np.ndarray:
    import file_analysis as fa
    shape = fa._load_shape(path, kind)
    if not shape.Solids():
        raise MatchError("That file has surfaces but no solid, so it has no volume to compare.")
    bb = shape.BoundingBox()
    diag = math.sqrt(bb.xlen ** 2 + bb.ylen ** 2 + bb.zlen ** 2) or 1.0
    verts, tris = shape.tessellate(max(diag / 600.0, 0.01), 0.3)
    if not verts or not tris:
        raise MatchError("That file has no surface to compare.")
    v = np.array([(p.x, p.y, p.z) for p in verts], dtype=np.float64)
    return v[np.array(tris, dtype=np.int64)]


def match_upload(kind: str, data: bytes, gen_stl: bytes) -> dict[str, Any]:
    """Runs in an isolated child process (file_analysis.run_isolated), so a hostile or huge file cannot hang
    the server. `kind` is step, iges, brep, stl or obj as file_analysis.sniff_kind reports it."""
    import meshes
    try:
        gen = meshes.parse_stl(gen_stl)
        if kind in ("step", "iges", "brep"):
            fd, path = tempfile.mkstemp(suffix={"step": ".step", "iges": ".igs", "brep": ".brep"}[kind])
            try:
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                ref = _brep_file_to_tris(path, kind)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        elif kind == "stl":
            ref = meshes.parse_stl(data)
        elif kind == "obj":
            ref = meshes.parse_obj(data)
        else:
            raise MatchError("Only STEP, IGES, BREP, STL and OBJ files can be compared.")
    except meshes.MeshError as exc:
        raise MatchError(str(exc)) from exc
    return compare(gen, ref)
