"""
Civil elements: road segment, box culvert, cantilever retaining wall.

These are preliminary concept solids. They give the shape and the dimensions,
and nothing here checks a design code, loads, hydraulics or soil. All
parameters are millimetres (a 3.5 m lane is 3500), the same as every other
template, so the validator, exporters and viewer need no unit switch.
A request in metres is converted by the parser before it gets here.

Every solid sits with its underside at z = 0 and runs along +X from 0 to
`length_mm`, symmetric about y = 0 where the element is symmetric.
"""
import cadquery as cq

from ._civil_profiles import (
    culvert_cell_centres,
    culvert_dims,
    kerb_rects,
    retaining_wall_profile,
    road_heights,
    road_profile,
)


def _extrude_profile(points, length: float) -> cq.Workplane:
    """Extrude a (y, z) outline along +X. The YZ plane's local x is world Y,
    its local y is world Z and its normal is +X."""
    return cq.Workplane("YZ").polyline(points).close().extrude(length)


def generate_road_segment(params: dict) -> cq.Workplane:
    """
    Crowned road segment: carriageway plus shoulders, optional kerbs.

    Expected params:
    - length_mm: segment length along the road
    - lane_width_mm: width of one lane
    - lane_count: total lanes across the carriageway (2 = one each way)
    - shoulder_width_mm: shoulder on each side (0 for none)
    - pavement_thickness_mm: thickness at the crown (centre line)
    - crossfall_percent: carriageway fall from crown to edge
    - shoulder_crossfall_percent: shoulder fall
    - has_kerbs: bool, kerb along both outer edges
    - kerb_width_mm, kerb_height_mm
    """
    length = float(params.get("length_mm", 20000.0))
    lane_w = float(params.get("lane_width_mm", 3500.0))
    lanes = int(params.get("lane_count", 2))
    shoulder = float(params.get("shoulder_width_mm", 1500.0))
    thick = float(params.get("pavement_thickness_mm", 300.0))
    cross = float(params.get("crossfall_percent", 2.5))
    sh_cross = float(params.get("shoulder_crossfall_percent", 4.0))

    pts = road_profile(lane_w, lanes, shoulder, thick, cross, sh_cross)
    result = _extrude_profile(pts, length)

    if params.get("has_kerbs"):
        h = road_heights(lane_w, lanes, shoulder, thick, cross, sh_cross)
        kerb_w = float(params.get("kerb_width_mm", 150.0))
        kerb_h = float(params.get("kerb_height_mm", 150.0))
        overlap = min(20.0, h["z_shoulder_edge"] / 2.0)
        for rect in kerb_rects(h["half_width"], h["z_shoulder_edge"], kerb_w, kerb_h, overlap):
            result = result.union(_extrude_profile(rect, length))

    return result


def generate_box_culvert(params: dict) -> cq.Workplane:
    """
    Rectangular box culvert with one or more equal cells.

    Expected params:
    - clear_width_mm, clear_height_mm: opening of each cell
    - length_mm: culvert length along the flow
    - wall_thickness_mm: outer and internal walls
    - slab_thickness_mm: top and bottom slabs
    - barrel_count: number of cells side by side (1 to 4)
    """
    cw = float(params.get("clear_width_mm", 1500.0))
    ch = float(params.get("clear_height_mm", 1200.0))
    length = float(params.get("length_mm", 6000.0))
    wall = float(params.get("wall_thickness_mm", 200.0))
    slab = float(params.get("slab_thickness_mm", 250.0))
    n = int(params.get("barrel_count", 1))

    dims = culvert_dims(cw, ch, wall, slab, n)
    outer = (
        cq.Workplane("YZ")
        .center(0, dims["outer_height"] / 2.0)
        .rect(dims["outer_width"], dims["outer_height"])
        .extrude(length)
    )

    # Cutters run past both ends so no cut face is coplanar with an end face.
    for cy in culvert_cell_centres(cw, wall, n):
        cutter = (
            cq.Workplane("YZ")
            .workplane(offset=-1.0)
            .center(cy, slab + ch / 2.0)
            .rect(cw, ch)
            .extrude(length + 2.0)
        )
        outer = outer.cut(cutter)
    return outer


def generate_retaining_wall(params: dict) -> cq.Workplane:
    """
    Cantilever retaining wall (inverted T with a tapered stem).

    Expected params:
    - length_mm: wall length along its face
    - stem_height_mm: height of the stem above the base slab
    - stem_thickness_base_mm, stem_thickness_top_mm: stem taper
    - base_width_mm, base_thickness_mm: base slab
    - toe_length_mm: slab projecting in front of the stem
    The retained side is +Y.
    """
    length = float(params.get("length_mm", 10000.0))
    pts = retaining_wall_profile(
        stem_height_mm=float(params.get("stem_height_mm", 3000.0)),
        stem_thickness_base_mm=float(params.get("stem_thickness_base_mm", 400.0)),
        stem_thickness_top_mm=float(params.get("stem_thickness_top_mm", 250.0)),
        base_width_mm=float(params.get("base_width_mm", 2200.0)),
        base_thickness_mm=float(params.get("base_thickness_mm", 400.0)),
        toe_length_mm=float(params.get("toe_length_mm", 600.0)),
    )
    return _extrude_profile(pts, length)
