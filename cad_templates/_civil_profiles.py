"""
Pure-Python cross-section maths for the civil templates (civil.py).

No CadQuery import on purpose: everything here is plain numbers, so the
geometry rules can be unit-tested without the CAD kernel installed. civil.py
turns these point lists into solids.

All values are millimetres, like the rest of NitoCAD. A cross-section is a list
of (y, z) points in the plane across the element: y is sideways (centre line
at y = 0 for symmetric elements), z is up from the underside. The solid is
the cross-section extruded along +X for `length_mm`.

Civil parts here are preliminary concept geometry. Nothing in this module
checks a design code, so none of it should be described as code compliant.
"""
from __future__ import annotations

Point = tuple[float, float]

MIN_EDGE_THICKNESS_MM = 50.0   # thinnest pavement edge the road profile may fall to


def _dedupe(points: list[Point], tol: float = 1e-6) -> list[Point]:
    """Drop consecutive duplicate points (zero-width shoulders create them)."""
    out: list[Point] = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= tol and abs(out[0][1] - out[-1][1]) <= tol:
        out.pop()
    return out


def road_heights(
    lane_width_mm: float,
    lane_count: int,
    shoulder_width_mm: float,
    pavement_thickness_mm: float,
    crossfall_percent: float,
    shoulder_crossfall_percent: float,
) -> dict[str, float]:
    """Key levels of the road cross-section.

    The surface is highest at the centre line (the crown) and falls away to
    each side at the carriageway crossfall, then at the shoulder crossfall.
    """
    half_carriageway = lane_count * lane_width_mm / 2.0
    half_width = half_carriageway + shoulder_width_mm
    z_crown = pavement_thickness_mm
    z_carriageway_edge = z_crown - half_carriageway * crossfall_percent / 100.0
    z_shoulder_edge = z_carriageway_edge - shoulder_width_mm * shoulder_crossfall_percent / 100.0
    return {
        "half_carriageway": half_carriageway,
        "half_width": half_width,
        "z_crown": z_crown,
        "z_carriageway_edge": z_carriageway_edge,
        "z_shoulder_edge": z_shoulder_edge,
    }


def road_profile(
    lane_width_mm: float,
    lane_count: int,
    shoulder_width_mm: float,
    pavement_thickness_mm: float,
    crossfall_percent: float,
    shoulder_crossfall_percent: float,
) -> list[Point]:
    """Closed outline of a two-way crowned road, counter-clockwise."""
    h = road_heights(
        lane_width_mm, lane_count, shoulder_width_mm,
        pavement_thickness_mm, crossfall_percent, shoulder_crossfall_percent,
    )
    hw, hc = h["half_width"], h["half_carriageway"]
    pts: list[Point] = [
        (-hw, 0.0),
        (hw, 0.0),
        (hw, h["z_shoulder_edge"]),
        (hc, h["z_carriageway_edge"]),
        (0.0, h["z_crown"]),
        (-hc, h["z_carriageway_edge"]),
        (-hw, h["z_shoulder_edge"]),
    ]
    return _dedupe(pts)


def kerb_rects(
    half_width_mm: float,
    z_edge_mm: float,
    kerb_width_mm: float,
    kerb_height_mm: float,
    overlap_mm: float,
) -> list[list[Point]]:
    """One rectangle per side. Each starts `overlap_mm` below the surface edge
    so the union with the road body overlaps instead of just touching, which
    is the case OpenCASCADE booleans handle least reliably."""
    z0 = z_edge_mm - overlap_mm
    z1 = z_edge_mm + kerb_height_mm
    right = [(half_width_mm - kerb_width_mm, z0), (half_width_mm, z0),
             (half_width_mm, z1), (half_width_mm - kerb_width_mm, z1)]
    left = [(-half_width_mm, z0), (-half_width_mm + kerb_width_mm, z0),
            (-half_width_mm + kerb_width_mm, z1), (-half_width_mm, z1)]
    return [left, right]


def culvert_dims(
    clear_width_mm: float,
    clear_height_mm: float,
    wall_thickness_mm: float,
    slab_thickness_mm: float,
    barrel_count: int,
) -> dict[str, float]:
    """Outer size of a box culvert with `barrel_count` equal cells."""
    outer_w = barrel_count * clear_width_mm + (barrel_count + 1) * wall_thickness_mm
    outer_h = clear_height_mm + 2.0 * slab_thickness_mm
    return {"outer_width": outer_w, "outer_height": outer_h}


def culvert_cell_centres(
    clear_width_mm: float,
    wall_thickness_mm: float,
    barrel_count: int,
) -> list[float]:
    """Sideways centre (y) of each cell, symmetric about y = 0."""
    pitch = clear_width_mm + wall_thickness_mm
    first = -pitch * (barrel_count - 1) / 2.0
    return [first + i * pitch for i in range(barrel_count)]


def retaining_wall_profile(
    stem_height_mm: float,
    stem_thickness_base_mm: float,
    stem_thickness_top_mm: float,
    base_width_mm: float,
    base_thickness_mm: float,
    toe_length_mm: float,
) -> list[Point]:
    """Cantilever retaining wall as one closed outline.

    The retained earth is on the +y side. The front face of the stem is
    vertical and the back face leans (the taper), so the stem is thicker at
    the base than at the top. Total height is base thickness plus stem height.
    """
    tb = base_thickness_mm
    total = tb + stem_height_mm
    front = toe_length_mm
    back_base = toe_length_mm + stem_thickness_base_mm
    back_top = toe_length_mm + stem_thickness_top_mm
    return _dedupe([
        (0.0, 0.0),
        (base_width_mm, 0.0),
        (base_width_mm, tb),
        (back_base, tb),
        (back_top, total),
        (front, total),
        (front, tb),
        (0.0, tb),
    ])


def polygon_area(points: list[Point]) -> float:
    """Signed shoelace area; positive for counter-clockwise outlines."""
    a = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0
