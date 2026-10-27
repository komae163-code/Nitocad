"""
The outline of a spur gear, as a list of (x, y) points. No CadQuery, so it is tested without a kernel.

The gear template used to cut one small rectangle per tooth at the root circle and then rotate it about
the origin. The rotation moved each notch as well as turning it, so the notches landed on every second
tooth angle (a 20-tooth gear got 10), and a notch centred on the root circle never reached the tip
circle anyway, so each one was a closed pocket and not a gap between teeth. This builds the real thing:
involute flanges, a tip arc, and a root arc, tooth after tooth around the circle.

Standard full-depth spur gear, module m, z teeth, pressure angle a:
    pitch radius   r  = m z / 2
    base radius    rb = r cos(a)
    tip radius     ra = r + m
    root radius    rf = r - 1.25 m
    tooth thickness at the pitch circle = pi m / 2   (half the circular pitch)

Tooth 0 is centred on the +X axis. Teeth are spaced 2 pi / z apart, counter-clockwise.
"""
from __future__ import annotations

import math

TOOTH_FLANK_POINTS = 8
ROOT_ARC_POINTS = 3


def _inv(phi: float) -> float:
    """The involute function: tan(phi) - phi."""
    return math.tan(phi) - phi


def _flank_angle(r: float, half_pitch_angle: float, inv_alpha: float, rb: float) -> float:
    """Angular position of a tooth's flank at radius r, measured from the tooth's centre line.

    At the pitch circle this is half the tooth's angular thickness. It shrinks as r grows."""
    phi = math.acos(min(1.0, rb / r))
    return half_pitch_angle + inv_alpha - _inv(phi)


def gear_radii(module: float, teeth: int, pressure_angle_deg: float = 20.0) -> dict[str, float]:
    """Pitch, base, tip and root radii. The tip is cut back if the tooth would come to a point first."""
    rp = module * teeth / 2.0
    alpha = math.radians(pressure_angle_deg)
    rb = rp * math.cos(alpha)
    ra = rp + module
    rf = rp - 1.25 * module
    half = math.pi / (2.0 * teeth)
    inv_a = _inv(alpha)
    if _flank_angle(ra, half, inv_a, rb) <= 0.0:       # a pointed tooth: stop where the flanks meet
        lo, hi = rp, ra
        for _ in range(60):
            mid = (lo + hi) / 2
            if _flank_angle(mid, half, inv_a, rb) > 0:
                lo = mid
            else:
                hi = mid
        ra = lo * 0.999
    return {"pitch": rp, "base": rb, "tip": ra, "root": rf}


def involute_gear_outline(module: float, teeth: int, pressure_angle_deg: float = 20.0,
                          flank_points: int = TOOTH_FLANK_POINTS) -> list[tuple[float, float]]:
    """Closed counter-clockwise outline of the gear. The first point is not repeated at the end."""
    if teeth < 3 or module <= 0:
        raise ValueError("a gear needs at least 3 teeth and a positive module")
    radii = gear_radii(module, teeth, pressure_angle_deg)
    rp, rb, ra, rf = radii["pitch"], radii["base"], radii["tip"], radii["root"]
    alpha = math.radians(pressure_angle_deg)
    half, inv_a = math.pi / (2.0 * teeth), _inv(alpha)
    pitch_angle = 2.0 * math.pi / teeth

    r_start = max(rb, rf)                                  # the involute begins at the base circle, or the root if that is higher
    steps = max(2, flank_points)
    flank_r = [r_start + (ra - r_start) * i / (steps - 1) for i in range(steps)]
    theta = [_flank_angle(r, half, inv_a, rb) for r in flank_r]
    if pitch_angle - 2.0 * theta[0] <= 0.0:
        raise ValueError("neighbouring teeth overlap at their base: use a smaller pressure angle or more teeth")

    pts: list[tuple[float, float]] = []

    def polar(r: float, a: float) -> tuple[float, float]:
        return (r * math.cos(a), r * math.sin(a))

    for k in range(teeth):
        c = k * pitch_angle
        # Root arc from the previous tooth's right flank to this tooth's left flank.
        prev_right = c - pitch_angle + theta[0]
        this_left = c - theta[0]
        for j in range(ROOT_ARC_POINTS + 1):
            a = prev_right + (this_left - prev_right) * j / ROOT_ARC_POINTS
            pts.append(polar(rf, a))
        if rf < r_start:                                     # below the base circle the flank runs radially down to the root
            pts.append(polar(r_start, c - theta[0]))
        for r, t in zip(flank_r, theta):                     # left flank, going out
            pts.append(polar(r, c - t))
        pts.append(polar(ra, c))                             # tip, mid-arc
        for r, t in zip(reversed(flank_r), reversed(theta)):  # right flank, coming back in
            pts.append(polar(r, c + t))
        if rf < r_start:
            pts.append(polar(rf, c + theta[0]))

    # Drop consecutive duplicates (a flank that starts on the root circle repeats a point).
    clean: list[tuple[float, float]] = []
    for p in pts:
        if not clean or math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 1e-9:
            clean.append(p)
    if math.hypot(clean[0][0] - clean[-1][0], clean[0][1] - clean[-1][1]) <= 1e-9:
        clean.pop()
    return clean


def outline_area(points: list[tuple[float, float]]) -> float:
    """Signed area by the shoelace formula. Positive when the outline runs counter-clockwise."""
    s = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        s += x0 * y1 - x1 * y0
    return s / 2.0
