"""
Simple box / enclosure template.
"""
import cadquery as cq

def generate_simple_box(params: dict) -> cq.Workplane:
    """
    Generate a box: a solid block, or a hollow box that is open at the top.

    Expected params:
    - length_mm: Outer length
    - width_mm: Outer width
    - height_mm: Outer height
    - hollow: True for an open-top box with walls (an enclosure, a tray, a box with walls),
      False for a solid block (default)
    - wall_thickness_mm: Wall thickness, used when hollow

    `has_lid` is still accepted and means the same as `hollow`. It was this switch's first name, though no lid
    was ever built. `lid_fit_tolerance_mm` is accepted and ignored for the same reason.
    """
    length = params.get("length_mm", 100.0)
    width = params.get("width_mm", 80.0)
    height = params.get("height_mm", 50.0)
    wall_thickness = params.get("wall_thickness_mm", 3.0)
    hollow = params.get("hollow", False)
    has_lid = params.get("has_lid", False)

    # Create outer box
    outer = cq.Workplane("XY").box(length, width, height)

    if hollow or has_lid:
        # Remove the top face and hollow out the inside
        result = outer.faces(">Z").shell(-wall_thickness)
    else:
        result = outer

    return result
