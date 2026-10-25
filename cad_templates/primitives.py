"""
Basic primitive shapes: shafts, cylinders, spheres, etc.
"""
import cadquery as cq
import math
from ._safe_ops import safe_chamfer

def generate_shaft(params: dict) -> cq.Workplane:
    """
    Generate a simple shaft/cylinder.
    
    Expected params:
    - diameter_mm: Shaft diameter
    - length_mm: Shaft length
    - chamfer_mm: Optional end chamfer
    - keyway_width_mm: Optional keyway width
    - keyway_depth_mm: Optional keyway depth
    """
    diameter = params.get('diameter_mm', 10.0)
    length = params.get('length_mm', 50.0)
    chamfer = params.get('chamfer_mm', 0.0)
    keyway_width = params.get('keyway_width_mm', 0.0)
    keyway_depth = params.get('keyway_depth_mm', 0.0)
    
    # Create cylinder
    result = cq.Workplane("XY").circle(diameter / 2).extrude(length)
    
    # Add chamfer to ends
    if chamfer > 0:
        # The two circular end edges. ("|Z" selected the seam line along the side of the cylinder,
        # so the ends were never chamfered.)
        result = safe_chamfer(result, chamfer, "%CIRCLE")
    
    # Add keyway if specified
    if keyway_width > 0 and keyway_depth > 0:
        # One pocket on the +Y side: keyway_width wide, keyway_depth deep
        # measured from the shaft surface (DIN 6885 t1), full shaft length.
        # (The previous cut extruded the slot both ways through the whole
        # diameter, which sliced the shaft into two separate pieces.)
        r = diameter / 2
        pocket_h = min(keyway_depth, r) + 1.0  # +1 mm so the cut clears the cylinder surface
        keyway = (
            cq.Workplane("XY")
            .box(keyway_width, pocket_h, length, centered=(True, False, False))
            .translate((0, r - min(keyway_depth, r), 0))
        )
        result = result.cut(keyway)
    
    return result

def generate_bearing(params: dict) -> cq.Workplane:
    """
    Generate a simple bearing representation (outer race, inner race, gap).
    
    Expected params:
    - inner_diameter_mm: Inner bore
    - outer_diameter_mm: Outer diameter
    - width_mm: Bearing width
    """
    inner = params.get('inner_diameter_mm', 10.0)
    outer = params.get('outer_diameter_mm', 20.0)
    width = params.get('width_mm', 5.0)
    
    # Outer cylinder
    outer_cyl = cq.Workplane("XY").circle(outer / 2).extrude(width)
    
    # Inner bore
    result = outer_cyl.faces(">Z").workplane().hole(inner)
    
    return result

def generate_spacer(params: dict) -> cq.Workplane:
    """
    Generate a spacer/standoff.
    
    Expected params:
    - outer_diameter_mm: Outer diameter
    - inner_diameter_mm: Inner bore
    - length_mm: Length
    """
    outer = params.get('outer_diameter_mm', 10.0)
    inner = params.get('inner_diameter_mm', 5.0)
    length = params.get('length_mm', 10.0)
    
    result = cq.Workplane("XY").circle(outer / 2).extrude(length)
    result = result.faces(">Z").workplane().hole(inner)
    
    return result

def generate_washer(params: dict) -> cq.Workplane:
    """
    Generate a washer.
    
    Expected params:
    - outer_diameter_mm: Outer diameter
    - inner_diameter_mm: Inner hole
    - thickness_mm: Thickness
    """
    outer = params.get('outer_diameter_mm', 10.0)
    inner = params.get('inner_diameter_mm', 5.0)
    thickness = params.get('thickness_mm', 1.5)
    
    result = cq.Workplane("XY").circle(outer / 2).extrude(thickness)
    result = result.faces(">Z").workplane().hole(inner)
    
    return result

def generate_sphere(params: dict) -> cq.Workplane:
    """
    Generate a sphere.
    
    Expected params:
    - diameter_mm: Sphere diameter
    """
    diameter = params.get('diameter_mm', 20.0)
    result = cq.Workplane("XY").sphere(diameter / 2)
    return result
