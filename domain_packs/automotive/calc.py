"""Pure-Python geometry maths (no CadQuery) - importable everywhere."""
from __future__ import annotations

import math


def polar_points(n: int, pcd: float, phase_deg: float = 0.0) -> list[tuple[float, float]]:
    r = pcd / 2.0
    return [
        (r * math.cos(math.radians(phase_deg + 360.0 * i / n)),
         r * math.sin(math.radians(phase_deg + 360.0 * i / n)))
        for i in range(n)
    ]


def link_polygon(p1: tuple, p2: tuple, w1: float, w2: float | None = None) -> list[tuple[float, float]]:
    """Quadrilateral from p1 to p2, width w1 at p1 and w2 at p2 (tapered
    link). Explicit perpendicular-offset corners, not workplane.rotate()."""
    w2 = w1 if w2 is None else w2
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        raise ValueError("link endpoints coincide")
    nx, ny = -dy / length, dx / length
    return [
        (p1[0] + nx * w1 / 2, p1[1] + ny * w1 / 2),
        (p2[0] + nx * w2 / 2, p2[1] + ny * w2 / 2),
        (p2[0] - nx * w2 / 2, p2[1] - ny * w2 / 2),
        (p1[0] - nx * w1 / 2, p1[1] - ny * w1 / 2),
    ]


def chord_gap(n: int, pcd: float) -> float:
    """Centre-to-centre distance between adjacent holes on a bolt circle."""
    return pcd * math.sin(math.pi / n)
