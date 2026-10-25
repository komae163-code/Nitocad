"""
Standard metric fasteners: hex bolt (ISO 4017), socket head cap screw (ISO 4762), hex nut (ISO 4032).

Dimensions come from standards.py, so "M8 x 30" always means the standard's head, nut and
diameters. The thread is NOT modelled: the shank is a plain cylinder at the nominal diameter,
which is how fasteners are normally shown in CAD assemblies (a real helical thread makes the
file huge and slows every boolean).  The bolt axis is Z, head below z = 0, the shank running
from z = 0 up to the length; a nut's bore equals the nominal diameter so it mates with the bolt
shank with no overlap.

Built only from calls already used elsewhere in this codebase: circle/polygon + extrude, union,
cut. UNVERIFIED in this environment (no CadQuery here); covered by tests/test_template_geometry.py.
"""
import math

import cadquery as cq

import standards


def _size(params: dict) -> str:
    key = standards.normalize_size(str(params.get("size", "M8")))
    if key is None:
        raise ValueError(f"unknown metric size {params.get('size')!r}; known: {', '.join(standards.thread_sizes())}")
    return key


def _hex(across_flats: float, height: float, z0: float = 0.0) -> cq.Workplane:
    # polygon() takes the diameter ACROSS CORNERS, a hex is quoted across flats
    return cq.Workplane("XY").workplane(offset=z0).polygon(6, across_flats / math.cos(math.radians(30))).extrude(height)


def generate_hex_bolt(params: dict) -> cq.Workplane:
    """params: size ('M8'), length_mm (shank length under the head)."""
    b = standards.hex_bolt(_size(params), params.get("length_mm", 30.0))
    if b is None:
        raise ValueError("hex_bolt needs a positive length_mm")
    head = _hex(b["head_across_flats_mm"], b["head_height_mm"], -b["head_height_mm"])
    shank = cq.Workplane("XY").circle(b["nominal_diameter_mm"] / 2).extrude(b["length_mm"])
    return head.union(shank)


def generate_socket_cap_screw(params: dict) -> cq.Workplane:
    """params: size ('M6'), length_mm."""
    b = standards.socket_cap_screw(_size(params), params.get("length_mm", 20.0))
    if b is None:
        raise ValueError("socket_cap_screw needs a positive length_mm")
    k = b["head_height_mm"]
    head = cq.Workplane("XY").workplane(offset=-k).circle(b["head_diameter_mm"] / 2).extrude(k)
    socket_depth = 0.6 * k
    socket = _hex(b["socket_across_flats_mm"], socket_depth, -socket_depth)
    shank = cq.Workplane("XY").circle(b["nominal_diameter_mm"] / 2).extrude(b["length_mm"])
    return head.cut(socket).union(shank)


def generate_hex_nut(params: dict) -> cq.Workplane:
    """params: size ('M8'). The bore is the nominal diameter so the nut sits on a bolt of the same size."""
    n = standards.hex_nut(_size(params))
    return _hex(n["across_flats_mm"], n["height_mm"]).faces(">Z").workplane().hole(n["nominal_diameter_mm"])
