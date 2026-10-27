"""
L-bracket template with mounting holes.
"""
import cadquery as cq
from ._safe_ops import safe_fillet


def generate_l_bracket(params: dict) -> cq.Workplane:
    """
    Generate an L-bracket.

    Expected params:
    - width_mm: Bracket width (along X)
    - height_mm: Total height of the vertical leg (along Z)
    - depth_mm: Total depth of the horizontal leg (along Y)
    - thickness_mm: Material thickness
    - hole_count: Number of holes per leg (default 2)
    - hole_diameter_mm: Hole diameter
    - fillet_radius_mm: Edge fillet radius

    The outside corner of the bend is on the Y/Z origin. The horizontal leg lies along +Y at the
    bottom, the vertical leg stands along +Z at the back, and both are as wide as `width_mm`
    (centred on X), so the part measures width x depth x height.

    The earlier version drew the horizontal leg centred on the vertical one, which made a T and not an L.
    """
    width = params.get("width_mm", 50.0)
    height = params.get("height_mm", 60.0)
    depth = params.get("depth_mm", 40.0)
    thickness = params.get("thickness_mm", 3.0)
    hole_count = int(params.get("hole_count", 2))
    hole_diameter = params.get("hole_diameter_mm", 3.2)
    fillet_radius = params.get("fillet_radius_mm", 2.0)

    # A leg thinner than the material would not be a leg. The validator keeps thickness well under this.
    thickness = min(thickness, depth * 0.5, height * 0.5)

    # The L as one profile in the YZ plane (local x = Y, local y = Z), extruded along X.
    profile = [(0, 0), (depth, 0), (depth, thickness), (thickness, thickness), (thickness, height), (0, height)]
    result = cq.Workplane("YZ").polyline(profile).close().extrude(width).translate((-width / 2, 0, 0))

    if hole_count > 0 and hole_diameter > 0:
        r = hole_diameter / 2
        # Holes in the vertical leg: drilled along Y, spaced up the leg.
        spacing = max((height - 20) / (hole_count - 1), hole_diameter * 1.5) if hole_count > 1 else 0
        for i in range(hole_count):
            z = height / 2 - ((hole_count - 1) * spacing) / 2 + i * spacing
            result = result.cut(cq.Solid.makeCylinder(r, thickness + 2, cq.Vector(0, -1, z), cq.Vector(0, 1, 0)))
        # Holes in the horizontal leg: drilled along Z, spaced along the part of the leg that sticks out.
        free = depth - thickness
        spacing = max((free - 20) / (hole_count - 1), hole_diameter * 1.5) if hole_count > 1 else 0
        for i in range(hole_count):
            y = thickness + free / 2 - ((hole_count - 1) * spacing) / 2 + i * spacing
            result = result.cut(cq.Solid.makeCylinder(r, thickness + 2, cq.Vector(0, y, -1), cq.Vector(0, 0, 1)))

    # Round the edges that run along the width: the bend and the two leg ends.
    if fillet_radius > 0 and fillet_radius < min(width, thickness) / 2:
        result = safe_fillet(result, fillet_radius, "|X")

    return result
