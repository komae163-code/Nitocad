"""
Transmission components: gears, pulleys, sprockets.
"""
import cadquery as cq
import math

from ._gear_profile import involute_gear_outline

def generate_gear(params: dict) -> cq.Workplane:
    """
    Generate a spur gear with involute teeth.
    
    Expected params:
    - module: Gear module (mm)
    - teeth: Number of teeth
    - thickness_mm: Gear thickness
    - bore_diameter_mm: Center bore
    - pressure_angle: Pressure angle (default 20°)
    """
    module = params.get('module', 2.0)
    teeth = params.get('teeth', 20)
    thickness = params.get('thickness_mm', 10.0)
    bore = params.get('bore_diameter_mm', 5.0)
    pressure_angle = params.get('pressure_angle', 20.0)
    
    # A real spur gear outline: involute flanks, a tip arc and a root arc, `teeth` times around.
    # (See cad_templates/_gear_profile.py. The earlier version cut one notch per tooth and then rotated
    # it about the origin, which moved the notches to every second tooth angle and never opened them to
    # the tip circle.)
    outline = involute_gear_outline(module, int(teeth), pressure_angle)
    result = cq.Workplane("XY").polyline(outline).close().extrude(thickness)

    # Add center bore
    if bore > 0:
        result = result.faces(">Z").workplane().hole(bore)
    
    return result

def generate_pulley(params: dict) -> cq.Workplane:
    """
    Generate a simple V-belt or timing pulley.
    
    Expected params:
    - outer_diameter_mm: Outer diameter
    - belt_width_mm: Belt width
    - bore_diameter_mm: Center bore
    - thickness_mm: Total thickness
    - groove_depth_mm: Groove depth (default 3mm)
    """
    outer_dia = params.get('outer_diameter_mm', 40.0)
    belt_width = params.get('belt_width_mm', 10.0)
    bore = params.get('bore_diameter_mm', 5.0)
    thickness = params.get('thickness_mm', 15.0)
    groove_depth = params.get('groove_depth_mm', 3.0)
    
    # Create main body
    result = cq.Workplane("XY").circle(outer_dia / 2).extrude(thickness)
    
    # Add flanges
    flange_height = 2.0
    flange = (
        cq.Workplane("XY")
        .circle(outer_dia / 2 + 2)
        .extrude(flange_height)
    )
    result = result.union(flange)
    
    flange2 = (
        cq.Workplane("XY")
        .workplane(offset=thickness - flange_height)
        .circle(outer_dia / 2 + 2)
        .extrude(flange_height)
    )
    result = result.union(flange2)
    
    # Cut the belt groove: the ring from (outer/2 - groove_depth) out past the rim, between the two flanges.
    # (The earlier version cut a solid cylinder of radius outer/2 - groove_depth, which removed the hub and
    # left a hollow drum.)
    groove_start = max((thickness - belt_width) / 2, flange_height)
    groove_end = min((thickness + belt_width) / 2, thickness - flange_height)
    groove_floor = max(outer_dia / 2 - groove_depth, bore / 2 + 0.5)
    if groove_end > groove_start and groove_depth > 0 and groove_floor < outer_dia / 2:
        groove = (
            cq.Workplane("XY")
            .workplane(offset=groove_start)
            .circle(outer_dia / 2 + flange_height + 0.5)
            .circle(groove_floor)
            .extrude(groove_end - groove_start)
        )
        result = result.cut(groove)

    # Add center bore
    if bore > 0:
        result = result.faces(">Z").workplane().hole(bore)
    
    return result

def generate_sprocket(params: dict) -> cq.Workplane:
    """
    Generate a simplified chain sprocket.
    
    Expected params:
    - teeth: Number of teeth
    - chain_pitch_mm: Chain pitch (e.g., 12.7mm for #40)
    - thickness_mm: Sprocket thickness
    - bore_diameter_mm: Center bore
    """
    teeth = params.get('teeth', 18)
    pitch = params.get('chain_pitch_mm', 12.7)
    thickness = params.get('thickness_mm', 8.0)
    bore = params.get('bore_diameter_mm', 8.0)
    
    # Calculate pitch radius
    pitch_radius = pitch / (2 * math.sin(math.pi / teeth))
    outer_radius = pitch_radius + pitch / 4
    
    # Create base
    result = cq.Workplane("XY").circle(outer_radius).extrude(thickness)
    
    # Simplified tooth cutting
    tooth_angle = 360.0 / teeth
    for i in range(teeth):
        angle = i * tooth_angle
        x = pitch_radius * math.cos(math.radians(angle))
        y = pitch_radius * math.sin(math.radians(angle))
        
        # Cut tooth space. It is a circle, so it needs no turning; rotating it about the origin
        # (as an earlier version did) moved it to every second tooth angle.
        space = (
            cq.Workplane("XY")
            .center(x, y)
            .circle(pitch / 3)
            .extrude(thickness)
        )
        result = result.cut(space)
    
    # Add center bore
    if bore > 0:
        result = result.faces(">Z").workplane().hole(bore)
    
    return result
