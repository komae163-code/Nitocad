"""
Analytic envelope geometry: an OCCT-independent way to MEASURE distances
between placed parts.

Each part supplies an envelope - a small CSG tree of cylinders, boxes and
convex prisms with exact signed-distance functions (sdf < 0 inside). From
that we can compute the minimum clearance between two placed parts (negative
= penetration depth) in pure Python, instantly, with no CadQuery.

WHAT THIS IS: a cross-check that re-derives each part's volume envelope from
its parameters. It lets clearances be measured where OCCT is not available,
and gives the tolerance stack-up and kinematic sweep something smooth to
differentiate. WHAT THIS IS NOT: the real solid. Envelopes simplify
(vented rotors are solid rings, tapered studs are cylinders of the base
diameter, bolt holes are omitted) and are tested against the real OCCT volumes
in tests/test_assembly_geometry.py. Distances come from surface sampling (default
3 mm) followed by local pattern-search refinement, so they are accurate to
roughly +/-0.05 mm near the closest approach and can miss a sliver thinner
than the sampling pitch elsewhere. OCCT's boolean check remains the gate.
"""
from __future__ import annotations

import math
from typing import Iterator, Sequence

import mating as M
from mating import Frame, Transform, Vec, dot

_EPS_SURFACE = 0.02


def _local(T: Transform | None, p: Vec) -> Vec:
    if T is None:
        return p
    d = (p[0] - T.t[0], p[1] - T.t[1], p[2] - T.t[2])
    return (dot(M.col(T.R, 0), d), dot(M.col(T.R, 1), d), dot(M.col(T.R, 2), d))


def _world(T: Transform | None, p: Vec) -> Vec:
    return p if T is None else T.point(p)


def _bbox_of_points(pts: Sequence[Vec]) -> tuple[Vec, Vec]:
    return ((min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts)),
            (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)))


def _box_corners(lo: Vec, hi: Vec) -> list[Vec]:
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


class Shape:
    def sdf(self, p: Vec) -> float:  # pragma: no cover - interface
        raise NotImplementedError

    def bbox(self) -> tuple[Vec, Vec]:  # pragma: no cover
        raise NotImplementedError

    def raw_samples(self, h: float) -> Iterator[Vec]:  # pragma: no cover
        raise NotImplementedError

    def samples(self, h: float) -> list[Vec]:
        """Points on THIS shape's surface (candidate points filtered by the
        full sdf, so surfaces buried inside a union or removed by a
        subtraction are dropped). Cached per shape and pitch."""
        cache = self.__dict__.setdefault("_sample_cache", {})
        if h not in cache:
            cache[h] = [p for p in self.raw_samples(h) if abs(self.sdf(p)) <= _EPS_SURFACE]
        return cache[h]


class Cylinder(Shape):
    """Canonical axis Z, z in [0, h], radius r; placed by T."""

    def __init__(self, r: float, h: float, T: Transform | None = None):
        if r <= 0 or h <= 0:
            raise ValueError(f"bad cylinder r={r} h={h}")
        self.r, self.h, self.T = float(r), float(h), T

    def sdf(self, p: Vec) -> float:
        x, y, z = _local(self.T, p)
        d0 = math.hypot(x, y) - self.r
        d1 = abs(z - self.h / 2) - self.h / 2
        if d0 <= 0 and d1 <= 0:
            return max(d0, d1)
        return math.hypot(max(d0, 0.0), max(d1, 0.0))

    def bbox(self):
        pts = [_world(self.T, c) for c in _box_corners((-self.r, -self.r, 0.0), (self.r, self.r, self.h))]
        return _bbox_of_points(pts)

    def raw_samples(self, h: float) -> Iterator[Vec]:
        n_ang = max(12, int(2 * math.pi * self.r / h))
        n_z = max(1, int(self.h / h))
        for i in range(n_ang):
            a = 2 * math.pi * i / n_ang
            c, s = math.cos(a), math.sin(a)
            for k in range(n_z + 1):
                yield _world(self.T, (self.r * c, self.r * s, self.h * k / n_z))
        n_r = max(1, int(self.r / h))
        for k in range(n_r + 1):
            rr = self.r * k / n_r
            m = max(1, int(2 * math.pi * rr / h)) if rr > 0 else 1
            for i in range(m):
                a = 2 * math.pi * i / m
                for z in (0.0, self.h):
                    yield _world(self.T, (rr * math.cos(a), rr * math.sin(a), z))


class Box(Shape):
    """Axis-aligned in local coordinates: [lo, hi]; placed by T."""

    def __init__(self, lo: Vec, hi: Vec, T: Transform | None = None):
        if any(hi[i] <= lo[i] for i in range(3)):
            raise ValueError(f"bad box {lo} {hi}")
        self.lo, self.hi, self.T = lo, hi, T

    def sdf(self, p: Vec) -> float:
        q = _local(self.T, p)
        d = [max(self.lo[i] - q[i], q[i] - self.hi[i]) for i in range(3)]
        out = math.sqrt(sum(max(x, 0.0) ** 2 for x in d))
        return out if out > 0 else max(d)

    def bbox(self):
        return _bbox_of_points([_world(self.T, c) for c in _box_corners(self.lo, self.hi)])

    def raw_samples(self, h: float) -> Iterator[Vec]:
        n = [max(1, int((self.hi[i] - self.lo[i]) / h)) for i in range(3)]
        for ax in range(3):
            u, v = [i for i in range(3) if i != ax]
            for face in (self.lo[ax], self.hi[ax]):
                for i in range(n[u] + 1):
                    for j in range(n[v] + 1):
                        q = [0.0] * 3
                        q[ax] = face
                        q[u] = self.lo[u] + (self.hi[u] - self.lo[u]) * i / n[u]
                        q[v] = self.lo[v] + (self.hi[v] - self.lo[v]) * j / n[v]
                        yield _world(self.T, (q[0], q[1], q[2]))


class Prism(Shape):
    """Convex polygon (XY, counter- or clockwise) extruded z0..z1; placed by T."""

    def __init__(self, pts: Sequence[tuple[float, float]], z0: float, z1: float, T: Transform | None = None):
        if len(pts) < 3 or z1 <= z0:
            raise ValueError("bad prism")
        # normalise to counter-clockwise
        area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
        self.pts = list(pts) if area > 0 else list(reversed(pts))
        self.z0, self.z1, self.T = z0, z1, T

    def _d2(self, x: float, y: float) -> float:
        """Signed distance to the convex polygon (negative inside)."""
        n = len(self.pts)
        inside = True
        best = float("inf")
        for i in range(n):
            ax, ay = self.pts[i]
            bx, by = self.pts[(i + 1) % n]
            ex, ey = bx - ax, by - ay
            L2 = ex * ex + ey * ey
            t = max(0.0, min(1.0, ((x - ax) * ex + (y - ay) * ey) / L2)) if L2 > 0 else 0.0
            d = math.hypot(x - (ax + t * ex), y - (ay + t * ey))
            best = min(best, d)
            if ex * (y - ay) - ey * (x - ax) < 0:
                inside = False
        return -best if inside else best

    def sdf(self, p: Vec) -> float:
        x, y, z = _local(self.T, p)
        d0 = self._d2(x, y)
        zc, hz = (self.z0 + self.z1) / 2, (self.z1 - self.z0) / 2
        d1 = abs(z - zc) - hz
        if d0 <= 0 and d1 <= 0:
            return max(d0, d1)
        return math.hypot(max(d0, 0.0), max(d1, 0.0))

    def bbox(self):
        xs, ys = [q[0] for q in self.pts], [q[1] for q in self.pts]
        return _bbox_of_points([_world(self.T, c) for c in
                                _box_corners((min(xs), min(ys), self.z0), (max(xs), max(ys), self.z1))])

    def raw_samples(self, h: float) -> Iterator[Vec]:
        n_z = max(1, int((self.z1 - self.z0) / h))
        n = len(self.pts)
        for i in range(n):
            ax, ay = self.pts[i]
            bx, by = self.pts[(i + 1) % n]
            m = max(1, int(math.hypot(bx - ax, by - ay) / h))
            for j in range(m + 1):
                x, y = ax + (bx - ax) * j / m, ay + (by - ay) * j / m
                for k in range(n_z + 1):
                    yield _world(self.T, (x, y, self.z0 + (self.z1 - self.z0) * k / n_z))
        xs, ys = [q[0] for q in self.pts], [q[1] for q in self.pts]
        nx, ny = max(1, int((max(xs) - min(xs)) / h)), max(1, int((max(ys) - min(ys)) / h))
        for i in range(nx + 1):
            for j in range(ny + 1):
                x, y = min(xs) + (max(xs) - min(xs)) * i / nx, min(ys) + (max(ys) - min(ys)) * j / ny
                if self._d2(x, y) <= 0:
                    for z in (self.z0, self.z1):
                        yield _world(self.T, (x, y, z))


class Tube(Shape):
    """Hollow cylinder, canonical axis Z, z in [0, h], r_in <= rho <= r_out.
    The profile is rotationally symmetric, so the signed distance is EXACT
    (distance to the (rho, z) rectangle) - unlike Subtract, whose max(base,
    -tool) formula only bounds the distance from below inside a through-hole."""

    def __init__(self, r_out: float, r_in: float, h: float, T: Transform | None = None):
        if r_out <= r_in or r_in < 0 or h <= 0:
            raise ValueError(f"bad tube r_out={r_out} r_in={r_in} h={h}")
        self.ro, self.ri, self.h, self.T = float(r_out), float(r_in), float(h), T

    def sdf(self, p: Vec) -> float:
        x, y, z = _local(self.T, p)
        rho = math.hypot(x, y)
        dr = max(self.ri - rho, rho - self.ro)
        dz = abs(z - self.h / 2) - self.h / 2
        if dr <= 0 and dz <= 0:
            return max(dr, dz)
        return math.hypot(max(dr, 0.0), max(dz, 0.0))

    def bbox(self):
        pts = [_world(self.T, c) for c in _box_corners((-self.ro, -self.ro, 0.0), (self.ro, self.ro, self.h))]
        return _bbox_of_points(pts)

    def raw_samples(self, h: float) -> Iterator[Vec]:
        for r in {self.ro, self.ri} - {0.0}:
            n_ang = max(12, int(2 * math.pi * r / h))
            n_z = max(1, int(self.h / h))
            for i in range(n_ang):
                a = 2 * math.pi * i / n_ang
                for k in range(n_z + 1):
                    yield _world(self.T, (r * math.cos(a), r * math.sin(a), self.h * k / n_z))
        n_r = max(1, int((self.ro - self.ri) / h))
        for k in range(n_r + 1):
            rr = self.ri + (self.ro - self.ri) * k / n_r
            m = max(1, int(2 * math.pi * rr / h))
            for i in range(m):
                a = 2 * math.pi * i / m
                for z in (0.0, self.h):
                    yield _world(self.T, (rr * math.cos(a), rr * math.sin(a), z))


class Union(Shape):
    def __init__(self, children: Sequence[Shape]):
        self.children = list(children)

    def sdf(self, p: Vec) -> float:
        return min(c.sdf(p) for c in self.children)

    def bbox(self):
        bs = [c.bbox() for c in self.children]
        return (tuple(min(b[0][i] for b in bs) for i in range(3)), tuple(max(b[1][i] for b in bs) for i in range(3)))  # type: ignore[return-value]

    def raw_samples(self, h: float) -> Iterator[Vec]:
        for c in self.children:
            yield from c.raw_samples(h)


class Subtract(Shape):
    def __init__(self, base: Shape, tools: Sequence[Shape]):
        self.base, self.tools = base, list(tools)

    def sdf(self, p: Vec) -> float:
        d = self.base.sdf(p)
        for t in self.tools:
            d = max(d, -t.sdf(p))
        return d

    def bbox(self):
        return self.base.bbox()

    def raw_samples(self, h: float) -> Iterator[Vec]:
        yield from self.base.raw_samples(h)
        for t in self.tools:
            yield from t.raw_samples(h)


class Placed(Shape):
    """A part envelope placed in the world by a rigid transform. Caches the
    sampled surface (samples are computed once in part coordinates)."""

    def __init__(self, shape: Shape, T: Transform):
        self.shape, self.T = shape, T
        self._cache: dict[float, list[Vec]] = {}

    def sdf(self, p: Vec) -> float:
        return self.shape.sdf(_local(self.T, p))

    def bbox(self):
        lo, hi = self.shape.bbox()
        return _bbox_of_points([self.T.point(c) for c in _box_corners(lo, hi)])

    def raw_samples(self, h: float) -> Iterator[Vec]:
        return iter(self.samples(h))

    def samples(self, h: float) -> list[Vec]:
        if h not in self._cache:
            self._cache[h] = [self.T.point(q) for q in self.shape.samples(h)]
        return self._cache[h]


# --- constructors in part-local coordinates ------------------------------------------

def cyl_z(r: float, h: float, z0: float = 0.0, cx: float = 0.0, cy: float = 0.0) -> Cylinder:
    return Cylinder(r, h, Transform(M.IDENTITY, (cx, cy, z0)))


def annulus_z(r_out: float, r_in: float, h: float, z0: float = 0.0) -> Shape:
    if r_in <= 0:
        return cyl_z(r_out, h, z0)
    return Tube(r_out, r_in, h, Transform(M.IDENTITY, (0.0, 0.0, z0)))


def tube_axis(r_out: float, r_in: float, length: float, start: Vec, direction: Vec) -> Tube:
    f = Frame(start, direction)
    R = M.outer_sum([(f.basis()[0], (1.0, 0.0, 0.0)), (f.basis()[1], (0.0, 1.0, 0.0)), (f.basis()[2], (0.0, 0.0, 1.0))])
    return Tube(r_out, r_in, length, Transform(R, f.origin))


def cyl_axis(r: float, length: float, start: Vec, direction: Vec) -> Cylinder:
    f = Frame(start, direction)
    R = M.outer_sum([(f.basis()[0], (1.0, 0.0, 0.0)), (f.basis()[1], (0.0, 1.0, 0.0)), (f.basis()[2], (0.0, 0.0, 1.0))])
    return Cylinder(r, length, Transform(R, f.origin))


def box_ranges(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Box:
    return Box((x0, y0, z0), (x1, y1, z1))


def prism_xy(pts: Sequence[tuple[float, float]], z0: float, z1: float) -> Prism:
    return Prism(pts, z0, z1)


# --- measurement ------------------------------------------------------------------------

def _grad(S: Shape, p: Vec, e: float = 1e-3) -> Vec:
    return ((S.sdf((p[0] + e, p[1], p[2])) - S.sdf((p[0] - e, p[1], p[2]))) / (2 * e),
            (S.sdf((p[0], p[1] + e, p[2])) - S.sdf((p[0], p[1] - e, p[2]))) / (2 * e),
            (S.sdf((p[0], p[1], p[2] + e)) - S.sdf((p[0], p[1], p[2] - e))) / (2 * e))


def _project(S: Shape, p: Vec) -> Vec | None:
    """Newton-project p onto S's surface. Returns None if it does not
    converge: a point that is not actually on the surface must never be
    used to claim a distance."""
    for _ in range(10):
        d = S.sdf(p)
        if abs(d) < 1e-6:
            return p
        g = _grad(S, p)
        n = math.sqrt(dot(g, g))
        if n < 1e-9:
            return None
        p = (p[0] - d * g[0] / n, p[1] - d * g[1] / n, p[2] - d * g[2] / n)
    return p if abs(S.sdf(p)) < 1e-4 else None


_DIRS = [d for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
                     (1, 1, 0), (-1, 1, 0), (1, -1, 0), (-1, -1, 0), (1, 0, 1), (-1, 0, 1), (1, 0, -1), (-1, 0, -1),
                     (0, 1, 1), (0, -1, 1), (0, 1, -1), (0, -1, -1))]


def _refine(S: Shape, O: Shape, p: Vec, step: float) -> tuple[float, Vec]:
    """Slide a point along S's surface to minimise O's sdf there. Only
    points verified to lie on S's surface are ever considered."""
    start = _project(S, p)
    if start is None:
        return O.sdf(p), p
    p, best = start, O.sdf(start)
    s = step
    while s > 0.01:
        improved = False
        for d in _DIRS:
            n = math.sqrt(dot(d, d))
            q = _project(S, (p[0] + s * d[0] / n, p[1] + s * d[1] / n, p[2] + s * d[2] / n))
            if q is None:
                continue
            v = O.sdf(q)
            if v < best - 1e-9:
                best, p, improved = v, q, True
        if not improved:
            s *= 0.5
    return best, p


def clearance(A: Shape, B: Shape, h: float = 3.0, cutoff: float = 25.0, refine: bool = True) -> tuple[float, tuple[Vec, Vec] | None]:
    """Minimum distance between two shapes (negative = penetration depth).
    Pairs whose bounding boxes are farther apart than `cutoff` return the
    bounding-box gap (a lower bound) without sampling."""
    la, ha = A.bbox()
    lb, hb = B.bbox()
    gap = max(max(lb[i] - ha[i], la[i] - hb[i]) for i in range(3))
    if gap > cutoff:
        return gap, None
    best = (float("inf"), None, None)  # d, point, which shape owns the point
    for owner, other, which in ((A, B, "A"), (B, A, "B")):
        # a sample farther than `cutoff` from the other shape's bounding box
        # cannot be the closest approach: skip its (expensive) sdf evaluation
        olo, ohi = other.bbox()
        lo = (olo[0] - cutoff, olo[1] - cutoff, olo[2] - cutoff)
        hi = (ohi[0] + cutoff, ohi[1] + cutoff, ohi[2] + cutoff)
        for p in owner.samples(h):
            if (p[0] < lo[0] or p[0] > hi[0] or p[1] < lo[1] or p[1] > hi[1] or p[2] < lo[2] or p[2] > hi[2]):
                continue
            d = other.sdf(p)
            if d < best[0]:
                best = (d, p, which)
    d, p, which = best
    if p is None:
        return max(gap, cutoff), None
    owner, other = (A, B) if which == "A" else (B, A)
    if refine:
        d2, p2 = _refine(owner, other, p, h)
        if d2 < d:
            d, p = d2, p2
    return d, (p, p)


def volume(shape: Shape, step: float = 4.0) -> float:
    """Grid estimate of the enclosed volume (cell-centre sampling). Used to
    check an envelope against the real OCCT solid."""
    lo, hi = shape.bbox()
    nx, ny, nz = (max(1, int((hi[i] - lo[i]) / step)) for i in range(3))
    dx, dy, dz = ((hi[i] - lo[i]) / n for i, n in enumerate((nx, ny, nz)))
    inside = 0
    for i in range(nx):
        x = lo[0] + (i + 0.5) * dx
        for j in range(ny):
            y = lo[1] + (j + 0.5) * dy
            for k in range(nz):
                if shape.sdf((x, y, lo[2] + (k + 0.5) * dz)) < 0:
                    inside += 1
    return inside * dx * dy * dz
