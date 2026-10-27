"""
CAD template registry.
"""
from .motor_mount import generate_motor_mount
from .l_bracket import generate_l_bracket
from .flat_plate import generate_flat_plate
from .simple_box import generate_simple_box
from .primitives import generate_shaft, generate_bearing, generate_spacer, generate_washer, generate_sphere
from .transmission import generate_gear, generate_pulley, generate_sprocket
from .structural import generate_structural_beam, generate_angle, generate_tube
from .piping import generate_pipe_fitting, generate_flange
from .freeform import generate_freeform
from .misc import generate_hinge, generate_cam
from .hardware import generate_hex_standoff, generate_t_bracket, generate_channel_bracket
from .connecting_rod import generate_connecting_rod
from .crankshaft import generate_crankshaft
from .aerospace import generate_airfoil_wing, generate_wing_rib, generate_fuselage_frame, generate_wing_spar, generate_attachment_lug
from .aerospace_vehicle import (
    generate_fuselage, generate_tail_surface, generate_turbofan_engine, generate_jet_aircraft,
)
from .civil import generate_road_segment, generate_box_culvert, generate_retaining_wall
from .pcb import generate_pcb_board
from .fasteners import generate_hex_bolt, generate_socket_cap_screw, generate_hex_nut
from .building_part import generate_building
from .feature_tree_part import generate_feature_tree
from .standard_parts import generate_fastener, generate_asme_flange, generate_key, generate_o_ring, generate_std_bearing

TEMPLATES = {
    # Basic parts
    "motor_mount": generate_motor_mount,
    "l_bracket": generate_l_bracket,
    "flat_plate": generate_flat_plate,
    "simple_box": generate_simple_box,
    
    # Primitives
    "shaft": generate_shaft,
    "bearing": generate_bearing,
    "spacer": generate_spacer,
    "washer": generate_washer,
    "sphere": generate_sphere,
    
    # Transmission
    "gear": generate_gear,
    "pulley": generate_pulley,
    "sprocket": generate_sprocket,
    
    # Structural
    "structural_beam": generate_structural_beam,
    "angle": generate_angle,
    "tube": generate_tube,
    
    # Piping
    "pipe_fitting": generate_pipe_fitting,
    "flange": generate_flange,
    
    # Freeform
    "freeform": generate_freeform,
    
    # Misc
    "hinge": generate_hinge,
    "cam": generate_cam,

    # Hardware/structural (added after the initial 20)
    "hex_standoff": generate_hex_standoff,
    "t_bracket": generate_t_bracket,
    "channel_bracket": generate_channel_bracket,

    # Engine components (connecting_rod.py / crankshaft.py) - the most
    # geometrically complex templates in this project so far: multi-body
    # boolean unions rather than a single extrude+cut. See each module's
    # own docstring for the specific simplifications made (non-involute-
    # style approximations, same spirit as generate_gear()'s teeth).
    "connecting_rod": generate_connecting_rod,
    "crankshaft": generate_crankshaft,

    # Civil (preliminary concept solids, millimetres)
    "road_segment": generate_road_segment,
    "box_culvert": generate_box_culvert,
    "retaining_wall": generate_retaining_wall,

    # Aerospace domain pack (aerospace.py) - preliminary geometry only; see
    # aerospace_pack.GEOMETRY_ONLY_NOTICE.
    "airfoil_wing": generate_airfoil_wing,
    "wing_rib": generate_wing_rib,
    "fuselage_frame": generate_fuselage_frame,
    "wing_spar": generate_wing_spar,
    "attachment_lug": generate_attachment_lug,
    "fuselage": generate_fuselage,
    "tail_surface": generate_tail_surface,
    "turbofan_engine": generate_turbofan_engine,
    "jet_aircraft": generate_jet_aircraft,

    # Electronics: a circuit board as a 3D solid (see pcb/ and cad_templates/pcb.py)
    "pcb_board": generate_pcb_board,

    # Standard fasteners (dimensions from standards.py)
    "hex_bolt": generate_hex_bolt,
    "socket_cap_screw": generate_socket_cap_screw,
    "hex_nut": generate_hex_nut,
    # Standard parts from dimension tables (standards_data.py): one template per family
    "fastener": generate_fastener,
    "std_bearing": generate_std_bearing,
    "key": generate_key,
    "asme_flange": generate_asme_flange,
    "o_ring": generate_o_ring,
    # Open-ended sketch/feature DSL for parts no fixed template covers (feature_tree.py)
    "feature_tree": generate_feature_tree,
    # House / small building from flat Studio parameters (building/flat.py)
    "building": generate_building,
}

# Domain packs (see domain_packs/__init__.py): discipline-specific part types
# contributed as self-contained packs. Imported after the core templates, so a
# pack can never shadow a core part type (register_pack rejects duplicates).
from domain_packs import pack_templates  # noqa: E402

for _name, _fn in pack_templates().items():
    if _name in TEMPLATES:
        raise RuntimeError(f"domain pack part type {_name!r} collides with a core template")
    TEMPLATES[_name] = _fn
