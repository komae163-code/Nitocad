"""
Terrain surface: a Delaunay triangulated irregular network (TIN) built from
survey points, with height lookup, profiles along a line, and an STL export.

The triangulation fills the convex hull of the points. A concave site boundary
gets triangles across the gap, so pass max_edge_m to drop triangles with a long
edge (they are the ones bridging empty ground). Heights between points are
linear inside each triangle, which is the standard TIN assumption.
"""
from __future__ import annotations

import math

import numpy as np

from .errors import CivilInputError


def _triangulate(xy: np.ndarray) -> np.ndarray:
    """Bowyer-Watson. xy is (n, 2) already centred and scaled to about unit size.
    Returns an (m, 3) int array of vertex indices."""
    n = len(xy)
    big = 1.0e4
    verts = np.vstack([xy, [[-big, -big], [big, -big], [0.0, big]]])
    s0, s1, s2 = n, n + 1, n + 2

    tri = np.array([[s0, s1, s2]], dtype=np.int64)

    def circ(t: np.ndarray):
        a, b, c = verts[t[:, 0]], verts[t[:, 1]], verts[t[:, 2]]
        d = 2.0 * (a[:, 0] * (b[:, 1] - c[:, 1]) + b[:, 0] * (c[:, 1] - a[:, 1]) + c[:, 0] * (a[:, 1] - b[:, 1]))
        d = np.where(np.abs(d) < 1e-18, 1e-18, d)
        a2 = (a ** 2).sum(1)
        b2 = (b ** 2).sum(1)
        c2 = (c ** 2).sum(1)
        ux = (a2 * (b[:, 1] - c[:, 1]) + b2 * (c[:, 1] - a[:, 1]) + c2 * (a[:, 1] - b[:, 1])) / d
        uy = (a2 * (c[:, 0] - b[:, 0]) + b2 * (a[:, 0] - c[:, 0]) + c2 * (b[:, 0] - a[:, 0])) / d
        r2 = (a[:, 0] - ux) ** 2 + (a[:, 1] - uy) ** 2
        return ux, uy, r2

    cx, cy, r2 = circ(tri)

    for i in range(n):
        p = verts[i]
        inside = (cx - p[0]) ** 2 + (cy - p[1]) ** 2 < r2 * (1.0 - 1e-12)
        if not inside.any():
            continue
        bad = tri[inside]
        # Boundary of the hole = edges used by exactly one bad triangle.
        edges = np.vstack([bad[:, [0, 1]], bad[:, [1, 2]], bad[:, [2, 0]]])
        key = np.sort(edges, axis=1)
        uniq, inv, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        inv = np.asarray(inv).reshape(-1)
        boundary = edges[counts[inv] == 1]
        new = np.column_stack([boundary[:, 0], boundary[:, 1], np.full(len(boundary), i, dtype=np.int64)])
        keep = ~inside
        tri = np.vstack([tri[keep], new])
        ncx, ncy, nr2 = circ(new)
        cx = np.concatenate([cx[keep], ncx])
        cy = np.concatenate([cy[keep], ncy])
        r2 = np.concatenate([r2[keep], nr2])

    mask = (tri < n).all(axis=1)
    return tri[mask]


class Terrain:
    def __init__(self, points: list[tuple[float, float, float]], *, max_edge_m: float | None = None):
        if len(points) < 3:
            raise CivilInputError("At least 3 points are needed to make a surface")
        arr = np.asarray(points, dtype=float)
        if not np.isfinite(arr).all():
            raise CivilInputError("Survey points must be finite numbers")
        self.points = arr
        centre = arr[:, :2].mean(axis=0)
        span = float(np.ptp(arr[:, :2], axis=0).max())
        if span <= 0:
            raise CivilInputError("All survey points are at the same position")
        xy = (arr[:, :2] - centre) / span
        tris = _triangulate(xy)
        if len(tris) == 0:
            raise CivilInputError("The points are in a straight line, so they cannot make a surface")

        if max_edge_m is not None and max_edge_m > 0:
            tris = self._drop_long(tris, float(max_edge_m))
            if len(tris) == 0:
                raise CivilInputError("max_edge_m removed every triangle, use a larger value")
        self.triangles = tris
        self._build_index()

    def _drop_long(self, tris: np.ndarray, max_edge: float) -> np.ndarray:
        p = self.points[:, :2]
        keep = []
        for t in tris:
            a, b, c = p[t[0]], p[t[1]], p[t[2]]
            e = max(np.hypot(*(a - b)), np.hypot(*(b - c)), np.hypot(*(c - a)))
            keep.append(e <= max_edge)
        return tris[np.asarray(keep, dtype=bool)]

    def _build_index(self) -> None:
        p = self.points[:, :2]
        tri_pts = p[self.triangles]                       # (m, 3, 2)
        self._tmin = tri_pts.min(axis=1)
        self._tmax = tri_pts.max(axis=1)
        w = (self._tmax - self._tmin)
        self._cell = float(max(np.median(w.max(axis=1)) * 2.0, 1e-6))
        self._x0 = float(self._tmin[:, 0].min())
        self._y0 = float(self._tmin[:, 1].min())
        grid: dict[tuple[int, int], list[int]] = {}
        for k in range(len(self.triangles)):
            i0 = int((self._tmin[k, 0] - self._x0) // self._cell)
            i1 = int((self._tmax[k, 0] - self._x0) // self._cell)
            j0 = int((self._tmin[k, 1] - self._y0) // self._cell)
            j1 = int((self._tmax[k, 1] - self._y0) // self._cell)
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    grid.setdefault((i, j), []).append(k)
        self._grid = grid

    # ------------------------------------------------------------------ query
    def z_at(self, x: float, y: float) -> float | None:
        """Height at (x, y), or None when the point is outside the surface."""
        key = (int((x - self._x0) // self._cell), int((y - self._y0) // self._cell))
        for k in self._grid.get(key, ()):
            t = self.triangles[k]
            a, b, c = self.points[t[0]], self.points[t[1]], self.points[t[2]]
            det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(det) < 1e-12:
                continue
            l1 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / det
            l2 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / det
            l3 = 1.0 - l1 - l2
            eps = -1e-9
            if l1 >= eps and l2 >= eps and l3 >= eps:
                return float(l1 * a[2] + l2 * b[2] + l3 * c[2])
        return None

    def profile(self, polyline: list[tuple[float, float]], step: float) -> list[dict]:
        """Ground heights along a polyline at a fixed step, with the distance along it."""
        if step <= 0:
            raise CivilInputError("step must be above zero")
        out = []
        run = 0.0
        for (x0, y0), (x1, y1) in zip(polyline, polyline[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            if seg == 0:
                continue
            n = max(1, int(math.ceil(seg / step)))
            for i in range(n + (1 if (x1, y1) == polyline[-1] else 0)):
                t = min(i / n, 1.0)
                x, y = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
                out.append({"distance": run + t * seg, "x": x, "y": y, "z": self.z_at(x, y)})
            run += seg
        return out

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        p = self.points
        tri = p[self.triangles]
        a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
        plan = 0.5 * np.abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))
        n = np.cross(b - a, c - a)
        surface = 0.5 * np.linalg.norm(n, axis=1)
        return {
            "points": int(len(p)),
            "triangles": int(len(self.triangles)),
            "plan_area_m2": float(plan.sum()),
            "surface_area_m2": float(surface.sum()),
            "z_min": float(p[:, 2].min()),
            "z_max": float(p[:, 2].max()),
            "x_min": float(p[:, 0].min()), "x_max": float(p[:, 0].max()),
            "y_min": float(p[:, 1].min()), "y_max": float(p[:, 1].max()),
        }

    def to_ascii_stl(self, name: str = "terrain", z_exaggeration: float = 1.0) -> str:
        """The surface as a thin sheet of triangles for viewing. Not a solid."""
        out = [f"solid {name}"]
        p = self.points
        for t in self.triangles:
            a, b, c = (np.array([p[i][0], p[i][1], p[i][2] * z_exaggeration]) for i in t)
            n = np.cross(b - a, c - a)
            ln = float(np.linalg.norm(n))
            n = n / ln if ln > 0 else np.array([0.0, 0.0, 1.0])
            if n[2] < 0:               # keep every facet facing up
                b, c, n = c, b, -n
            out.append(f" facet normal {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}")
            out.append("  outer loop")
            for v in (a, b, c):
                out.append(f"   vertex {v[0]:.4f} {v[1]:.4f} {v[2]:.4f}")
            out.append("  endloop")
            out.append(" endfacet")
        out.append(f"endsolid {name}")
        return "\n".join(out)
