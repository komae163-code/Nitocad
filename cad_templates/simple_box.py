"""
Simple box / enclosure template.
"""
import cadquery as cq

def generate_simple_box(params: dict) -> cq.Workplane:
    """
    Generate a simple box with walls.
    
    Expected params:
    - length_mm: Outer length
    - width_mm: Outer width
    - height_mm: Outer height
    - wall_thickness_mm: Wall thickness
    - hollow: True for an open-top box with walls, False (the default) for a
      solid block

    No lid is built. `has_lid` is the old name for `hollow` and still works,
    so a part saved under it rebuilds the same. If both are given, `hollow`
    wins. `lid_fit_tolerance_mm` belonged to the same idea, did nothing, and is
    ignored if an old part still carries it.
    """
    length = params.get("length_mm", 100.0)
    width = params.get("width_mm", 80.0)
    height = params.get("height_mm", 50.0)
    wall_thickness = params.get("wall_thickness_mm", 3.0)
    hollow = params.get("hollow", params.get("has_lid", False))
    
    # Create outer box
    outer = cq.Workplane("XY").box(length, width, height)
    
    # Shell it (remove top face and add walls)
    if hollow:
        # Create box with open top
        result = outer.faces(">Z").shell(-wall_thickness)
    else:
        # Solid block
        result = outer
    
    return result
