"""
Standard parts driven by dimension tables (see standards_data.py): ISO fasteners
and ISO 15 deep-groove bearings. One template per family, not one per size.

Conventions (match the other templates: axis is +Z):
- screws/bolts: head underside at z=0, head occupies z in [-k, 0], shank z in [0, length]
- nuts, washers, bearings: base on z=0, extending +Z
- threads are cosmetic (plain cylinder at the nominal diameter); a nut bore equals the
  bolt's nominal diameter, so a bolt through a nut has zero interference.

Deliberately no fillets or chamfers: OCCT can silently return degenerate solids on small
edge treatments (see _safe_ops.py), and these parts are meant to be assembled and checked
for interference, where exact simple prisms are safer than cosmetic edge breaks.

UNVERIFIED in the environment this was written in (no CadQuery kernel); covered by
tests/test_standard_parts.py where cadquery is installed.
"""
import math

import cadquery as cq

from standards_data import lookup_bearing, lookup_fastener, lookup_flange, lookup_key

_HEX_CORNER = 1.0 / math.cos(math.pi / 6)  # across-corners = across-flats * this


def _hex_prism(across_flats: float, height: float, z0: float) -> cq.Workplane:
    return (cq.Workplane("XY").workplane(offset=z0)
            .polygon(6, across_flats * _HEX_CORNER).extrude(height))


def _cylinder(diameter: float, height: float, z0: float = 0.0) -> cq.Workplane:
    return cq.Workplane("XY").workplane(offset=z0).circle(diameter / 2).extrude(height)


def generate_fastener(params: dict) -> cq.Workplane:
    """
    ISO fastener from the standard's size table.

    Expected params:
    - standard: ISO_4762 (socket head cap screw), ISO_4017 (hex screw),
      ISO_4032 (hex nut), ISO_7089 (plain washer)
    - size: nominal thread size, e.g. "M8"
    - length_mm: shank length for screws (ignored for nuts and washers)
    """
    spec = lookup_fastener(params.get("standard", "ISO_4762"), params.get("size", "M8"))
    std, d = spec["standard"], spec["d"]
    length = float(params.get("length_mm", 20.0))

    if std in ("ISO_4762", "ISO_4017"):
        if length <= 0:
            raise ValueError("length_mm must be positive for a screw")
        shank = _cylinder(d, length)
        if std == "ISO_4762":
            head = _cylinder(spec["dk"], spec["k"], -spec["k"])
            head = head.cut(_hex_prism(spec["s"], spec["t"], -spec["k"]))
        else:
            head = _hex_prism(spec["s"], spec["k"], -spec["k"])
        return head.union(shank)

    if std == "ISO_4032":
        return _hex_prism(spec["s"], spec["m"], 0.0).cut(_cylinder(d, spec["m"]))

    return _cylinder(spec["d2"], spec["h"]).cut(_cylinder(spec["d1"], spec["h"]))


def generate_std_bearing(params: dict) -> cq.Workplane:
    """
    ISO 15 deep-groove ball bearing, envelope geometry (outer ring, inner ring and
    balls are not separated: one ring of the catalog bore, outer diameter and width).

    Expected params:
    - designation: bearing number, e.g. "6205" or "608" (suffixes like -2RS are ignored)
    """
    spec = lookup_bearing(params.get("designation", "6205"))
    return (cq.Workplane("XY").circle(spec["D"] / 2).circle(spec["d"] / 2).extrude(spec["B"]))


def generate_key(params: dict) -> cq.Workplane:
    """
    DIN 6885 parallel key. Cross-section (width b, height h) comes from the shaft diameter.

    Expected params:
    - shaft_diameter_mm: shaft the key fits (over 6 up to 110 mm)
    - length_mm: key length (a standard DIN 6885 length)
    - form: "A" round ends (default) or "B" square ends

    Orientation: length along +Z (z from 0 to length), width along X (centered),
    height along +Y with the key bottom at y=0. Seat it in a shaft keyway by moving it to
    y = shaft_radius - t1 (t1 = shaft keyway depth, from the same table).
    """
    spec = lookup_key(params.get("shaft_diameter_mm", 25.0))
    b, h = spec["b"], spec["h"]
    length = float(params.get("length_mm", 40.0))
    if length <= 0:
        raise ValueError("length_mm must be positive for a key")
    form = str(params.get("form", "A")).strip().upper()
    if form == "B":
        return cq.Workplane("XY").box(b, h, length, centered=(True, False, False))
    if form != "A":
        raise ValueError("key form must be A (round ends) or B (square ends)")
    if length <= b:
        raise ValueError("key length must exceed its width for round ends")
    return cq.Workplane("XZ").center(0, length / 2).slot2D(length, b, 90).extrude(-h)


def generate_asme_flange(params: dict) -> cq.Workplane:
    """
    ASME B16.5 Class 150 pipe flange (envelope: no hub or weld bevel).

    Expected params:
    - standard: "ASME_B16_5_150"
    - nps: nominal pipe size as a string, e.g. "4", "2-1/2"
    - flange_type: "slip_on" (bore = pipe OD) or "blind"

    Axis +Z: back face at z=0, thickness above it, 1.6 mm raised face on top. Bolt holes straddle
    the centerlines (no hole on an axis), as the standard specifies.
    """
    spec = lookup_flange(params.get("standard", "ASME_B16_5_150"), params.get("nps", "2"),
                         params.get("flange_type", "slip_on"))
    t, rf_h = spec["thickness"], spec["raised_face_height"]
    body = _cylinder(spec["od"], t).union(_cylinder(spec["raised_face_dia"], rf_h, t))
    n, pcd = spec["bolt_count"], spec["bolt_circle"]
    pts = [((pcd / 2) * math.cos(math.radians((i + 0.5) * 360.0 / n)),
            (pcd / 2) * math.sin(math.radians((i + 0.5) * 360.0 / n))) for i in range(n)]
    body = body.cut(cq.Workplane("XY").pushPoints(pts).circle(spec["bolt_hole"] / 2).extrude(t))
    if spec["bore"] > 0:
        body = body.cut(_cylinder(spec["bore"], t + rf_h))
    return body


def generate_o_ring(params: dict) -> cq.Workplane:
    """
    O-ring as a torus.

    Expected params:
    - inner_diameter_mm: free inside diameter
    - cross_section_mm: cord diameter (a standard AS568 / ISO 3601 cross-section)

    Lies in the XY plane, centered on the Z axis, from z=0 to z=cross_section.
    """
    inner = float(params.get("inner_diameter_mm", 20.0))
    cs = float(params.get("cross_section_mm", 3.53))
    if inner <= 0 or cs <= 0:
        raise ValueError("inner_diameter_mm and cross_section_mm must be positive")
    r = cs / 2
    big_r = inner / 2 + r  # centerline radius
    ring = cq.Workplane("XZ").pushPoints([(big_r, 0)]).circle(r).revolve(360, (0, 0, 0), (0, 1, 0))
    return ring.translate((0, 0, r))
