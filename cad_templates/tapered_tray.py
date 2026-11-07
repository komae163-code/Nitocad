"""
Tapered tray: an open-top tub that is smaller at the bottom than at the top, like a wheelbarrow tray, a hopper or a
planter. The sides slope outward, so it is not a plain box.
"""
import cadquery as cq


def generate_tapered_tray(params: dict) -> cq.Workplane:
    """
    Expected params (millimetres):
    - length_mm, width_mm: the opening at the top
    - bottom_length_mm, bottom_width_mm: the flat floor, never larger than the opening
    - height_mm: floor to rim
    - wall_thickness_mm: wall and floor thickness

    The origin is the middle of the floor's underside, so the tray stands on z = 0 and rises along +Z. (A box is
    centred on its origin. This one is not, so a tray placed at the height of a frame sits ON the frame.)
    """
    length = float(params.get("length_mm", 800.0))
    width = float(params.get("width_mm", 560.0))
    height = float(params.get("height_mm", 280.0))
    base_length = float(params.get("bottom_length_mm", 560.0))
    base_width = float(params.get("bottom_width_mm", 460.0))
    wall = float(params.get("wall_thickness_mm", 2.0))

    if min(length, width, height, base_length, base_width, wall) <= 0:
        raise ValueError("every size of a tapered tray must be positive")
    if base_length > length or base_width > width:
        raise ValueError("the floor of a tapered tray cannot be larger than its opening")
    if wall >= height or 2 * wall >= min(base_length, base_width):
        raise ValueError("the wall is too thick for this tray")

    outer = (cq.Workplane("XY").rect(base_length, base_width)
             .workplane(offset=height).rect(length, width).loft(combine=True))

    # The cavity follows the same slope, one wall thickness in, and runs past the rim so the top is open.
    slope_x = (length - base_length) / 2.0 / height          # how far each end leans out per mm of height
    slope_y = (width - base_width) / 2.0 / height
    over = 1.0
    rise = height - wall + over                              # cavity height, floor top to just past the rim
    # At the rim the cavity is the opening less one wall; the extra millimetre keeps leaning out at the same slope.
    top_cav_l = length - 2 * wall + 2 * slope_x * over
    top_cav_w = width - 2 * wall + 2 * slope_y * over
    cavity = (cq.Workplane("XY").workplane(offset=wall)
              .rect(base_length + 2 * slope_x * wall - 2 * wall, base_width + 2 * slope_y * wall - 2 * wall)
              .workplane(offset=rise)
              .rect(top_cav_l, top_cav_w)
              .loft(combine=True))
    return outer.cut(cavity)
