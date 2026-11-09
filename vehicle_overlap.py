"""
Overlap check for an expanded vehicle, with no CAD kernel.

assembly_gate only models bodies of revolution about Z, and skips anything rotated, so it says nothing about a
car's panels. This module models the shapes a vehicle spec is actually made of and finds parts that share volume
by sampling points inside the overlap of each pair's bounding box.

    report = find_overlaps(expanded["parts"])
    report["overlaps"]      [{"a", "b", "volume_mm3"}]  pairs that really share material
    report["unmodelled"]    part names with a shape this module has no model for (never silently "clean")

Shapes it knows (placed with position, then rotation about X, Y, Z in that order, as assembly.py does):
  feature_tree  extrude (polygon, rect, circle on XY / XZ / YZ) and revolve about X or Y, with add and cut modes
  shaft, tube, spacer, bearing, washer, pulley, sprocket   cylinders or rings about local Z
  gear          a disc out to the ROOT circle, which is material that is certainly there, so a meshing pair does
                not read as an overlap
  flat_plate, simple_box   boxes centred on the origin (a hollow simple_box is open at the top)

It is a check on the plan, not on the real solids: teeth, keyways, fillets and holes in plates are ignored, and a
thin overlap smaller than the sampling step can be missed. The post-build interference check in assembly_checks
still has the last word.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

MAX_SAMPLES = 3_000_000
MIN_STEP_MM = 0.5
MAX_AXIS_SAMPLES = 240
REPORT_MIN_MM3 = 20.0          # smaller than this is sampling noise at a touching face


def _rot(rx: float, ry: float, rz: float) -> np.ndarray:
    a, b, c = (math.radians(v) for v in (rx, ry, rz))
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    Rx = np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]])
    Ry = np.array([[cb, 0, sb], [0, 1, 0], [-sb, 0, cb]])
    Rz = np.array([[cc, -sc, 0], [sc, cc, 0], [0, 0, 1]])
    return Rx @ Ry @ Rz


def _in_poly(u: np.ndarray, v: np.ndarray, pts: list[list[float]]) -> np.ndarray:
    inside = np.zeros(u.shape, dtype=bool)
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if y1 == y2:
            continue
        cond = ((y1 > v) != (y2 > v)) & (u < (x2 - x1) * (v - y1) / (y2 - y1) + x1)
        inside ^= cond
    return inside


def _profile_mask(prof: dict[str, Any], u: np.ndarray, v: np.ndarray) -> np.ndarray:
    cx, cy = prof.get("center", [0, 0])
    t = prof["type"]
    if t == "polygon":
        return _in_poly(u - cx, v - cy, prof["points"])
    if t == "rect":
        return (np.abs(u - cx) < prof["width"] / 2) & (np.abs(v - cy) < prof["height"] / 2)
    if t == "circle":
        return (u - cx) ** 2 + (v - cy) ** 2 < (prof["diameter"] / 2) ** 2
    raise ValueError(f"profile {t!r} not modelled")


def _feature_mask(f: dict[str, Any], P: np.ndarray) -> np.ndarray:
    x, y, z = P[:, 0], P[:, 1], P[:, 2]
    op = f["op"]
    if op == "extrude":
        plane, off = f.get("plane", "XY"), float(f.get("offset", 0))
        u, v, w = {"XY": (x, y, z), "XZ": (x, z, -y), "YZ": (y, z, x)}[plane]
        d = float(f["distance"])
        if f.get("symmetric"):
            lo, hi = off - d / 2, off + d / 2
        elif f.get("reverse"):
            lo, hi = off - d, off
        else:
            lo, hi = off, off + d
        return (w > lo) & (w < hi) & _profile_mask(f["profile"], u, v)
    if op == "revolve":
        if float(f.get("angle", 360)) < 359.9:
            raise ValueError("partial revolve not modelled")
        if f.get("axis", "y") == "y":
            return _profile_mask(f["profile"], np.hypot(x, z), y)
        return _profile_mask(f["profile"], x, np.hypot(y, z))
    raise ValueError(f"feature op {op!r} not modelled")


def _ring(P: np.ndarray, r_in: float, r_out: float, z0: float, z1: float) -> np.ndarray:
    r2 = P[:, 0] ** 2 + P[:, 1] ** 2
    return (r2 < r_out ** 2) & (r2 >= r_in ** 2) & (P[:, 2] > z0) & (P[:, 2] < z1)


def _local_mask(ptype: str, prm: dict[str, Any], P: np.ndarray) -> np.ndarray:
    g = prm.get
    if ptype == "feature_tree":
        m = np.zeros(len(P), dtype=bool)
        for i, f in enumerate(prm["features"]):
            fm = _feature_mask(f, P)
            mode = f.get("mode") or ("new" if i == 0 else "add")
            m = (m | fm) if mode in ("new", "add") else (m & ~fm)
        return m
    if ptype == "shaft":
        return _ring(P, 0, g("diameter_mm", 10) / 2, 0, g("length_mm", 50))
    if ptype == "tube":
        if g("shape", "round") != "round":
            raise ValueError("non-round tube not modelled")
        o = g("outer_diameter_mm", 20)
        return _ring(P, o / 2 - g("wall_thickness_mm", 2), o / 2, 0, g("length_mm", 100))
    if ptype == "spacer":
        return _ring(P, g("inner_diameter_mm", 5) / 2, g("outer_diameter_mm", 10) / 2, 0, g("length_mm", 10))
    if ptype == "bearing":
        return _ring(P, g("inner_diameter_mm", 10) / 2, g("outer_diameter_mm", 20) / 2, 0, g("width_mm", 5))
    if ptype == "washer":
        return _ring(P, g("inner_diameter_mm", 5) / 2, g("outer_diameter_mm", 10) / 2, 0, g("thickness_mm", 1))
    if ptype == "gear":
        m, n = g("module", 2.0), g("teeth", 20)
        return _ring(P, g("bore_diameter_mm", 5) / 2, m * (n / 2 - 1.25), 0, g("thickness_mm", 10))
    if ptype in ("pulley", "sprocket"):
        od = g("outer_diameter_mm") or 0
        if not od:
            raise ValueError(f"{ptype} size not modelled")
        return _ring(P, g("bore_diameter_mm", 5) / 2, od / 2, 0, g("thickness_mm", 10))
    if ptype in ("flat_plate", "simple_box"):
        L, W = g("length_mm", 100), g("width_mm", 80)
        H = g("thickness_mm", 5) if ptype == "flat_plate" else g("height_mm", 50)
        box = (np.abs(P[:, 0]) < L / 2) & (np.abs(P[:, 1]) < W / 2) & (np.abs(P[:, 2]) < H / 2)
        if ptype == "simple_box" and (g("hollow") or g("has_lid")):
            w = g("wall_thickness_mm", 3.0)
            cav = ((np.abs(P[:, 0]) < L / 2 - w) & (np.abs(P[:, 1]) < W / 2 - w)
                   & (P[:, 2] > -H / 2 + w))
            return box & ~cav
        return box
    raise ValueError(f"part type {ptype!r} not modelled")


_CORNERS = np.array([[sx, sy, sz] for sx in (0, 1) for sy in (0, 1) for sz in (0, 1)], dtype=float)


def _local_box(ptype: str, prm: dict[str, Any]) -> tuple[np.ndarray, np.ndarray] | None:
    """A box that surely contains the part, in local coordinates."""
    g = prm.get
    if ptype in ("shaft", "tube", "spacer", "bearing", "washer", "gear", "pulley", "sprocket"):
        if ptype == "shaft":
            r, z = g("diameter_mm", 10) / 2, g("length_mm", 50)
        elif ptype == "tube":
            r, z = g("outer_diameter_mm", 20) / 2, g("length_mm", 100)
        elif ptype == "spacer":
            r, z = g("outer_diameter_mm", 10) / 2, g("length_mm", 10)
        elif ptype == "bearing":
            r, z = g("outer_diameter_mm", 20) / 2, g("width_mm", 5)
        elif ptype == "washer":
            r, z = g("outer_diameter_mm", 10) / 2, g("thickness_mm", 1)
        elif ptype == "gear":
            r, z = g("module", 2.0) * (g("teeth", 20) / 2 - 1.25), g("thickness_mm", 10)
        else:
            r, z = (g("outer_diameter_mm") or 0) / 2, g("thickness_mm", 10)
        return np.array([-r, -r, 0.0]), np.array([r, r, z])
    if ptype in ("flat_plate", "simple_box"):
        L, W = g("length_mm", 100), g("width_mm", 80)
        H = g("thickness_mm", 5) if ptype == "flat_plate" else g("height_mm", 50)
        return np.array([-L / 2, -W / 2, -H / 2]), np.array([L / 2, W / 2, H / 2])
    if ptype == "feature_tree":
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for i, f in enumerate(prm["features"]):
            if (f.get("mode") or ("new" if i == 0 else "add")) not in ("new", "add"):
                continue
            if f["op"] == "extrude":
                prof = f["profile"]
                cx, cy = prof.get("center", [0, 0])
                if prof["type"] == "polygon":
                    us = [p[0] + cx for p in prof["points"]]
                    vs = [p[1] + cy for p in prof["points"]]
                elif prof["type"] == "rect":
                    us, vs = [cx - prof["width"] / 2, cx + prof["width"] / 2], [cy - prof["height"] / 2, cy + prof["height"] / 2]
                else:
                    r = prof["diameter"] / 2
                    us, vs = [cx - r, cx + r], [cy - r, cy + r]
                off, d = float(f.get("offset", 0)), float(f["distance"])
                if f.get("symmetric"):
                    w0, w1 = off - d / 2, off + d / 2
                elif f.get("reverse"):
                    w0, w1 = off - d, off
                else:
                    w0, w1 = off, off + d
                plane = f.get("plane", "XY")
                if plane == "XY":
                    a, b = [min(us), min(vs), w0], [max(us), max(vs), w1]
                elif plane == "XZ":
                    a, b = [min(us), -w1, min(vs)], [max(us), -w0, max(vs)]
                else:
                    a, b = [w0, min(us), min(vs)], [w1, max(us), max(vs)]
            elif f["op"] == "revolve":
                prof = f["profile"]
                if prof["type"] != "polygon":
                    return None
                cx, cy = prof.get("center", [0, 0])
                us = [p[0] + cx for p in prof["points"]]
                vs = [p[1] + cy for p in prof["points"]]
                if f.get("axis", "y") == "y":
                    R = max(abs(u) for u in us)
                    a, b = [-R, min(vs), -R], [R, max(vs), R]
                else:
                    R = max(abs(v) for v in vs)
                    a, b = [min(us), -R, -R], [max(us), R, R]
            else:
                return None
            lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        return (lo, hi) if np.isfinite(lo).all() else None
    return None


def find_overlaps(parts: list[dict[str, Any]], *, skip_pairs: Any = None,
                  min_volume_mm3: float = REPORT_MIN_MM3) -> dict[str, Any]:
    """Pairs of parts that share volume. `skip_pairs(a, b) -> bool` leaves out pairs that are meant to touch."""
    placed: list[tuple[str, str, dict[str, Any], np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
    unmodelled: list[str] = []
    for p in parts:
        name, ptype, prm = p["name"], p.get("part_type"), p.get("parameters") or {}
        pos, rot = p.get("position"), p.get("rotation") or [0, 0, 0]
        if pos is None:
            unmodelled.append(name)
            continue
        try:
            box = _local_box(ptype, prm)
            _local_mask(ptype, prm, np.zeros((1, 3)))          # fails early for a shape with no model
        except (ValueError, KeyError, TypeError):
            box = None
        if box is None:
            unmodelled.append(name)
            continue
        R = _rot(*rot)
        lo, hi = box
        pts = (lo + (hi - lo) * _CORNERS) @ R.T + np.array(pos, dtype=float)
        placed.append((name, ptype, prm, R, np.array(pos, dtype=float), pts.min(axis=0), pts.max(axis=0)))

    found: list[dict[str, Any]] = []
    for i in range(len(placed)):
        na, ta, pa, Ra, oa, loa, hia = placed[i]
        for j in range(i + 1, len(placed)):
            nb, tb, pb, Rb, ob, lob, hib = placed[j]
            lo, hi = np.maximum(loa, lob), np.minimum(hia, hib)
            ext = hi - lo
            if (ext <= 1e-6).any() or (skip_pairs and skip_pairs(na, nb)):
                continue
            n = np.clip(np.ceil(ext / MIN_STEP_MM), 1, MAX_AXIS_SAMPLES).astype(int)
            while n.prod() > MAX_SAMPLES:
                n[int(np.argmax(n))] = max(1, int(n.max() * 0.8))
            axes = [lo[k] + (np.arange(n[k]) + 0.5) * ext[k] / n[k] for k in range(3)]
            G = np.stack(np.meshgrid(*axes, indexing="ij"), axis=-1).reshape(-1, 3)
            ma = _local_mask(ta, pa, (G - oa) @ Ra)
            if not ma.any():
                continue
            mb = _local_mask(tb, pb, (G[ma] - ob) @ Rb)
            hits = int(mb.sum())
            vol = hits * float(np.prod(ext / n))
            if vol >= min_volume_mm3:
                found.append({"a": na, "b": nb, "volume_mm3": round(vol, 1)})
    found.sort(key=lambda r: -r["volume_mm3"])
    return {"overlaps": found, "unmodelled": unmodelled, "modelled": len(placed)}
