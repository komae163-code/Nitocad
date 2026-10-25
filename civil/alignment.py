"""
Road alignments.

Horizontal: a polyline of PI points (points of intersection) with a circular
curve radius at each interior PI (0 for a sharp corner). Stations are the
distance along the alignment in metres, starting at 0.

Vertical: PVIs (points of vertical intersection) given as (station, elevation,
curve length). Each curve is a symmetric parabola. The first and last PVI have
no curve.

Coordinates are x = easting, y = northing. Bearings are azimuths in degrees,
clockwise from north.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from .errors import CivilInputError

_EPS = 1e-9


@dataclass
class _Line:
    s0: float
    length: float
    x0: float
    y0: float
    dx: float          # unit direction
    dy: float


@dataclass
class _Arc:
    s0: float
    length: float
    cx: float
    cy: float
    radius: float
    a0: float          # start angle of the radius vector from the centre
    sign: int          # +1 turning left (counter-clockwise), -1 turning right


class HorizontalAlignment:
    def __init__(self, pis: list[tuple[float, float]], radii: list[float] | None = None):
        if len(pis) < 2:
            raise CivilInputError("A horizontal alignment needs at least 2 points")
        n = len(pis)
        radii = list(radii) if radii is not None else [0.0] * n
        if len(radii) != n:
            raise CivilInputError("Give one radius per PI (use 0 for no curve; the first and last are ignored)")
        if any(r < 0 or not math.isfinite(r) for r in radii):
            raise CivilInputError("Curve radii must be zero or positive")
        for (x0, y0), (x1, y1) in zip(pis, pis[1:]):
            if math.hypot(x1 - x0, y1 - y0) < 1e-6:
                raise CivilInputError("Two consecutive PI points are at the same position")
        self.pis = [(float(x), float(y)) for x, y in pis]
        self.radii = radii
        self.curves: list[dict] = []
        self._elements: list[_Line | _Arc] = []
        self._build()

    @staticmethod
    def _unit(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy)
        return dx / d, dy / d, d

    def _build(self) -> None:
        pis, radii = self.pis, self.radii
        n = len(pis)
        # Per-PI curve geometry first, so neighbouring tangents can be checked.
        info: list[dict | None] = [None] * n
        for i in range(1, n - 1):
            u1x, u1y, _ = self._unit(pis[i - 1], pis[i])
            u2x, u2y, _ = self._unit(pis[i], pis[i + 1])
            cross = u1x * u2y - u1y * u2x
            dot = max(-1.0, min(1.0, u1x * u2x + u1y * u2y))
            delta = math.atan2(abs(cross), dot)
            if radii[i] > 0 and delta > 1e-6:
                if delta > math.pi - 1e-6:
                    raise CivilInputError(f"PI {i + 1} turns back on itself, a curve cannot fit")
                t = radii[i] * math.tan(delta / 2.0)
                info[i] = {"pi": i, "radius": radii[i], "delta": delta, "tangent": t,
                           "length": radii[i] * delta, "sign": 1 if cross > 0 else -1}
        for i in range(n - 1):
            seg = math.hypot(pis[i + 1][0] - pis[i][0], pis[i + 1][1] - pis[i][1])
            used = (info[i]["tangent"] if info[i] else 0.0) + (info[i + 1]["tangent"] if info[i + 1] else 0.0)
            if used > seg + 1e-6:
                raise CivilInputError(
                    f"The curves at PI {i + 1} and PI {i + 2} overlap: their tangents add to "
                    f"{used:.2f} m on a {seg:.2f} m straight. Reduce a radius or move a PI"
                )

        s = 0.0
        cur = pis[0]
        for i in range(1, n):
            ux, uy, seg_len = self._unit(pis[i - 1], pis[i])
            c = info[i] if i < n - 1 else None
            line_end = (pis[i][0] - ux * c["tangent"], pis[i][1] - uy * c["tangent"]) if c else pis[i]
            ll = math.hypot(line_end[0] - cur[0], line_end[1] - cur[1])
            if ll > 1e-9:
                self._elements.append(_Line(s, ll, cur[0], cur[1], ux, uy))
                s += ll
            if c:
                sign = c["sign"]
                nx, ny = -uy * sign, ux * sign                 # towards the centre
                cx, cy = line_end[0] + nx * c["radius"], line_end[1] + ny * c["radius"]
                a0 = math.atan2(line_end[1] - cy, line_end[0] - cx)
                self._elements.append(_Arc(s, c["length"], cx, cy, c["radius"], a0, sign))
                self.curves.append({
                    "pi": i + 1, "radius": c["radius"],
                    "deflection_deg": math.degrees(c["delta"]),
                    "direction": "left" if sign > 0 else "right",
                    "tangent": c["tangent"], "length": c["length"],
                    "tc_station": s, "ct_station": s + c["length"],
                })
                s += c["length"]
                u2x, u2y, _ = self._unit(pis[i], pis[i + 1])
                cur = (pis[i][0] + u2x * c["tangent"], pis[i][1] + u2y * c["tangent"])
            else:
                cur = pis[i]
        self.length = s
        self._starts = [e.s0 for e in self._elements]

    def _element(self, station: float):
        k = bisect.bisect_right(self._starts, station) - 1
        return self._elements[max(0, min(k, len(self._elements) - 1))]

    def point_at(self, station: float) -> dict:
        if station < -1e-6 or station > self.length + 1e-6:
            raise CivilInputError(f"Station {station:.2f} is outside the alignment (0 to {self.length:.2f})")
        s = min(max(station, 0.0), self.length)
        e = self._element(s)
        d = s - e.s0
        if isinstance(e, _Line):
            x, y = e.x0 + e.dx * d, e.y0 + e.dy * d
            hx, hy = e.dx, e.dy
        else:
            ang = e.a0 + e.sign * d / e.radius
            x, y = e.cx + e.radius * math.cos(ang), e.cy + e.radius * math.sin(ang)
            hx, hy = -e.sign * math.sin(ang), e.sign * math.cos(ang)
        return {"station": s, "x": x, "y": y, "dx": hx, "dy": hy,
                "bearing_deg": (math.degrees(math.atan2(hx, hy)) + 360.0) % 360.0}

    def offset_point(self, station: float, offset: float) -> tuple[float, float]:
        """Point `offset` metres from the centre line (positive = left of travel)."""
        p = self.point_at(station)
        return p["x"] - p["dy"] * offset, p["y"] + p["dx"] * offset

    def key_stations(self) -> list[float]:
        keys = [0.0, self.length]
        for c in self.curves:
            keys += [c["tc_station"], c["ct_station"]]
        return sorted(set(round(k, 6) for k in keys))


class VerticalAlignment:
    def __init__(self, pvis: list[tuple[float, float, float]]):
        if len(pvis) < 2:
            raise CivilInputError("A vertical alignment needs at least 2 PVIs")
        self.pvis = [(float(s), float(z), float(l)) for s, z, l in pvis]
        for (s0, _, _), (s1, _, _) in zip(self.pvis, self.pvis[1:]):
            if s1 <= s0:
                raise CivilInputError("PVI stations must increase")
        if any(not math.isfinite(v) for p in self.pvis for v in p):
            raise CivilInputError("PVI values must be finite numbers")
        if self.pvis[0][2] != 0 or self.pvis[-1][2] != 0:
            raise CivilInputError("The first and last PVI cannot have a curve (use length 0)")
        if any(p[2] < 0 for p in self.pvis):
            raise CivilInputError("Vertical curve lengths cannot be negative")
        self.start = self.pvis[0][0]
        self.end = self.pvis[-1][0]
        self.grades = [(z1 - z0) / (s1 - s0) for (s0, z0, _), (s1, z1, _) in zip(self.pvis, self.pvis[1:])]
        self.curves: list[dict] = []
        for i in range(1, len(self.pvis) - 1):
            s, z, length = self.pvis[i]
            if length <= 0:
                continue
            g1, g2 = self.grades[i - 1], self.grades[i]
            bvc, evc = s - length / 2.0, s + length / 2.0
            prev_end = self.pvis[i - 1][0] + self.pvis[i - 1][2] / 2.0
            next_start = self.pvis[i + 1][0] - self.pvis[i + 1][2] / 2.0
            if bvc < prev_end - 1e-6 or evc > next_start + 1e-6:
                raise CivilInputError(f"The vertical curve at PVI {i + 1} overlaps a neighbouring curve or runs past the end")
            a_pct = (g2 - g1) * 100.0
            hp = None
            if abs(g2 - g1) > 1e-12:
                x = -g1 * length / (g2 - g1)
                if 0.0 <= x <= length:
                    hp = bvc + x
            self.curves.append({
                "pvi": i + 1, "station": s, "elevation": z, "length": length,
                "bvc_station": bvc, "evc_station": evc,
                "grade_in_pct": g1 * 100.0, "grade_out_pct": g2 * 100.0,
                "algebraic_difference_pct": a_pct,
                "k_value": length / abs(a_pct) if abs(a_pct) > 1e-9 else None,
                "type": "crest" if a_pct < 0 else "sag",
                "high_low_point_station": hp,
            })

    def elevation(self, station: float) -> float:
        if station < self.start - 1e-6 or station > self.end + 1e-6:
            raise CivilInputError(f"Station {station:.2f} is outside the vertical alignment ({self.start:.2f} to {self.end:.2f})")
        st = min(max(station, self.start), self.end)
        for c in self.curves:
            if c["bvc_station"] <= st <= c["evc_station"]:
                i = c["pvi"] - 1
                g1, g2, length = self.grades[i - 1], self.grades[i], c["length"]
                z_bvc = c["elevation"] - g1 * length / 2.0
                x = st - c["bvc_station"]
                return z_bvc + g1 * x + (g2 - g1) * x * x / (2.0 * length)
        k = bisect.bisect_right([p[0] for p in self.pvis], st) - 1
        k = max(0, min(k, len(self.grades) - 1))
        return self.pvis[k][1] + self.grades[k] * (st - self.pvis[k][0])

    def grade_at(self, station: float) -> float:
        st = min(max(station, self.start), self.end)
        for c in self.curves:
            if c["bvc_station"] <= st <= c["evc_station"]:
                i = c["pvi"] - 1
                g1, g2 = self.grades[i - 1], self.grades[i]
                return g1 + (g2 - g1) * (st - c["bvc_station"]) / c["length"]
        k = bisect.bisect_right([p[0] for p in self.pvis], st) - 1
        return self.grades[max(0, min(k, len(self.grades) - 1))]

    def max_abs_grade_pct(self) -> float:
        return max(abs(g) for g in self.grades) * 100.0


def stations_for(length: float, interval: float, key_stations: list[float]) -> list[float]:
    """Every `interval` metres plus the key stations, sorted and de-duplicated."""
    if interval <= 0:
        raise CivilInputError("The station interval must be above zero")
    count = int(length // interval)
    if count > 5000:
        raise CivilInputError("That interval gives more than 5000 stations, use a larger interval")
    raw = [i * interval for i in range(count + 1)] + [length] + list(key_stations)
    out: list[float] = []
    for s in sorted(raw):
        if 0.0 <= s <= length + 1e-9 and (not out or s - out[-1] > 1e-6):
            out.append(min(s, length))
    return out
