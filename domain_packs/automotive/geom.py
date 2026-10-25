"""
Geometry helpers for the automotive pack.

Conventions (same philosophy as cad_templates/connecting_rod.py and
crankshaft.py, whose findings this follows):
  * Every feature is built from an explicit solid at explicit global
    coordinates and combined with .union()/.cut(). The faces(">Z").workplane()
    pattern centres its local origin on the face centroid, which is silently
    wrong for any off-origin feature, so it is not used here.
  * revolve() is avoided (see cad_templates/piping.py's findings). Rotational
    parts are stacks of extruded cylinders.
  * Cylinders whose axis is not Z are made with cq.Solid.makeCylinder(pnt,
    dir) instead of named workplanes, so there is no plane-orientation
    ambiguity.
  * Fillets are not applied by default (see _safe_ops.py); where offered they
    go through safe_fillet().
"""
from __future__ import annotations

import cadquery as cq

from .calc import chord_gap, link_polygon, polar_points  # noqa: F401  (re-exported)


def wp(shape) -> cq.Workplane:
    """Wrap a raw Shape (e.g. from Solid.makeCylinder) as a Workplane."""
    return cq.Workplane("XY").newObject([shape])


def cyl(r: float, h: float, z0: float = 0.0, cx: float = 0.0, cy: float = 0.0) -> cq.Workplane:
    """Z-axis cylinder of radius r and height h, base at z0, centred (cx, cy)."""
    return cq.Workplane("XY").workplane(offset=z0).moveTo(cx, cy).circle(r).extrude(h)


def annulus(r_out: float, r_in: float, h: float, z0: float = 0.0) -> cq.Workplane:
    ring = cyl(r_out, h, z0)
    if r_in > 0:
        ring = ring.cut(cyl(r_in, h + 2.0, z0 - 1.0))
    return ring


def axis_cyl(r: float, length: float, start: tuple, direction: tuple) -> cq.Workplane:
    """Cylinder along an arbitrary axis, starting at `start`."""
    return wp(cq.Solid.makeCylinder(r, length, cq.Vector(*start), cq.Vector(*direction)))


def prism(points: list[tuple[float, float]], h: float, z0: float = 0.0) -> cq.Workplane:
    """Closed XY polygon extruded along +Z from z0."""
    return cq.Workplane("XY").workplane(offset=z0).polyline(points).close().extrude(h)



def cut_z_holes(solid: cq.Workplane, pts, dia: float, z0: float, h: float) -> cq.Workplane:
    for (x, y) in pts:
        solid = solid.cut(cyl(dia / 2.0, h, z0, x, y))
    return solid


def fuse(solids: list[cq.Workplane]) -> cq.Workplane:
    result = solids[0]
    for s in solids[1:]:
        result = result.union(s)
    return result




def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> cq.Workplane:
    """Axis-aligned box from coordinate ranges (explicit global placement)."""
    return prism([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], z1 - z0, z0)
