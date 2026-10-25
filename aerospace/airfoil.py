"""Pure-Python airfoil + section geometry (stdlib only, no CAD kernel)."""
import math


def parse_naca4(code) -> tuple[float, float, float]:
    """'2412' -> (camber m, camber position p, thickness t), all as chord fractions."""
    s = str(code).strip()
    if len(s) != 4 or not s.isdigit():
        raise ValueError(f"NACA 4-digit code expected (e.g. '2412'), got {code!r}")
    return int(s[0]) / 100.0, int(s[1]) / 10.0, int(s[2:]) / 100.0


def naca4_thickness(x: float, t: float) -> float:
    """Half-thickness at chord fraction x (open trailing edge)."""
    return 5 * t * (0.2969 * math.sqrt(x) - 0.1260 * x - 0.3516 * x ** 2
                    + 0.2843 * x ** 3 - 0.1015 * x ** 4)


def naca4_camber(x: float, m: float, p: float) -> tuple[float, float]:
    """Camber line height and slope at chord fraction x."""
    if m == 0 or p == 0:
        return 0.0, 0.0
    if x < p:
        return m / p ** 2 * (2 * p * x - x ** 2), 2 * m / p ** 2 * (p - x)
    return (m / (1 - p) ** 2 * ((1 - 2 * p) + 2 * p * x - x ** 2),
            2 * m / (1 - p) ** 2 * (p - x))


def naca4_points(code, chord: float, n: int = 30) -> list[tuple[float, float]]:
    """Closed airfoil outline (TE upper -> LE -> TE lower), no repeated end point."""
    m, p, t = parse_naca4(code)
    xs = [(1 - math.cos(math.pi * i / n)) / 2 for i in range(n + 1)]  # cosine spacing
    upper, lower = [], []
    for x in xs:
        yc, dyc = naca4_camber(x, m, p)
        yt = naca4_thickness(x, t)
        th = math.atan(dyc)
        upper.append((x - yt * math.sin(th), yc + yt * math.cos(th)))
        lower.append((x + yt * math.sin(th), yc - yt * math.cos(th)))
    pts = upper[::-1] + lower[1:]  # LE shared point appears once
    return [(x * chord, y * chord) for x, y in pts]


def polygon_section_properties(pts):
    """Exact area, centroid and second moments of a simple closed polygon.

    Returns dict(area, cx, cy, ixx, iyy, c_max_y) about the centroid, any
    winding. ixx = integral of (y-cy)^2 dA (bending about the x axis).
    """
    n = len(pts)
    a2 = cx6 = cy6 = ixx_o = iyy_o = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cr = x0 * y1 - x1 * y0
        a2 += cr
        cx6 += (x0 + x1) * cr
        cy6 += (y0 + y1) * cr
        ixx_o += (y0 * y0 + y0 * y1 + y1 * y1) * cr
        iyy_o += (x0 * x0 + x0 * x1 + x1 * x1) * cr
    if a2 == 0:
        raise ValueError("degenerate polygon (zero area)")
    sgn = 1.0 if a2 > 0 else -1.0
    area = abs(a2) / 2.0
    cx = cx6 / (3.0 * a2)
    cy = cy6 / (3.0 * a2)
    ixx = sgn * ixx_o / 12.0 - area * cy * cy
    iyy = sgn * iyy_o / 12.0 - area * cx * cx
    return {
        "area": area, "cx": cx, "cy": cy, "ixx": ixx, "iyy": iyy,
        "c_max_y": max(abs(y - cy) for _, y in pts),
    }
