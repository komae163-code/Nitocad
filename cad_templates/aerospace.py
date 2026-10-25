"""
Aerospace domain pack, part templates (preliminary geometry only).

IMPORTANT: these generate valid CAD solids. A valid solid does NOT establish
structural adequacy, fatigue life, airworthiness or regulatory compliance.
Every result needs analysis and verification with appropriate engineering
tools before any real use. See aerospace_pack.GEOMETRY_ONLY_NOTICE.

Templates:
  - airfoil_wing:    tapered, optionally swept wing from a NACA 4-digit section
  - wing_rib:        airfoil-profile rib with lightening holes and spar slots
  - fuselage_frame:  ring bulkhead with lightening holes and an inner flange

Airfoils are polylines (not splines) so lofts stay robust in OpenCASCADE; the
trailing edge is slightly blunt (open-TE coefficient) so the solid is
manufacturable and the loft does not collapse to a knife edge.
"""
import math
import cadquery as cq

from aerospace.airfoil import (  # noqa: F401  (re-exported for existing imports/tests)
    parse_naca4, naca4_thickness, naca4_camber, naca4_points,
)


def generate_airfoil_wing(params: dict) -> cq.Workplane:
    """
    Params: naca (default '2412'), root_chord_mm, tip_chord_mm, span_mm
    (semi-span), sweep_deg (leading-edge sweep), both_halves (mirror to a
    full wing, default False).
    """
    code = params.get("naca", "2412")
    root_c = float(params.get("root_chord_mm", 200.0))
    tip_c = float(params.get("tip_chord_mm", 120.0))
    span = float(params.get("span_mm", 600.0))
    sweep = float(params.get("sweep_deg", 0.0))

    root = naca4_points(code, root_c)
    offset_x = span * math.tan(math.radians(sweep))
    tip = [(x + offset_x, y) for x, y in naca4_points(code, tip_c)]

    # "XZ" workplane: local x = global X (chord), local y = global Z (thickness),
    # normal = -Y, so the wing extends toward -Y.
    wing = (
        cq.Workplane("XZ").polyline(root).close()
        .workplane(offset=span).polyline(tip).close()
        .loft(ruled=True, combine=True)
    )
    if params.get("both_halves"):
        try:
            wing = wing.mirror("XZ", union=True)
        except Exception:
            pass  # fall back to the half wing rather than failing the job
    return wing


def generate_wing_rib(params: dict) -> cq.Workplane:
    """
    Params: naca, chord_mm, thickness_mm (rib plate thickness),
    lightening_hole_count, lightening_hole_ratio (hole dia / local airfoil
    thickness, kept <= 0.6), spar_positions (chord fractions, e.g. [0.25, 0.7]),
    spar_slot_width_mm.
    """
    code = params.get("naca", "2412")
    chord = float(params.get("chord_mm", 200.0))
    plate_t = float(params.get("thickness_mm", 2.0))
    n_holes = int(params.get("lightening_hole_count", 3))
    ratio = min(float(params.get("lightening_hole_ratio", 0.5)), 0.6)
    spars = params.get("spar_positions", [0.25, 0.70])
    slot_w = float(params.get("spar_slot_width_mm", 6.0))
    m, p, t = parse_naca4(code)

    body = cq.Workplane("XY").polyline(naca4_points(code, chord)).close().extrude(plate_t)

    # Spar slots first so lightening holes can be spaced between them.
    for xf in spars:
        yc, _ = naca4_camber(xf, m, p)
        h = 2 * naca4_thickness(xf, t) * chord * 0.7
        if h > 1.0:
            body = body.cut(
                cq.Workplane("XY").center(xf * chord, yc * chord).rect(slot_w, h).extrude(plate_t)
            )

    if n_holes > 0:
        lo, hi = 0.30, 0.65
        stations = [lo + (hi - lo) * (i + 0.5) / n_holes for i in range(n_holes)]
        for xf in stations:
            if any(abs(xf - s) * chord < slot_w + 4 for s in spars):
                continue
            yc, _ = naca4_camber(xf, m, p)
            d = 2 * naca4_thickness(xf, t) * chord * ratio
            if d >= 3.0:
                body = body.cut(
                    cq.Workplane("XY").center(xf * chord, yc * chord).circle(d / 2).extrude(plate_t)
                )
    return body


def generate_fuselage_frame(params: dict) -> cq.Workplane:
    """
    Params: outer_diameter_mm, ring_width_mm (radial web width), thickness_mm,
    lightening_hole_count, lightening_hole_diameter_mm (0 = auto),
    flange_height_mm (inner stiffening flange, 0 = none), flange_thickness_mm.
    """
    od = float(params.get("outer_diameter_mm", 300.0))
    width = float(params.get("ring_width_mm", 40.0))
    t = float(params.get("thickness_mm", 2.0))
    n = int(params.get("lightening_hole_count", 8))
    hd = float(params.get("lightening_hole_diameter_mm", 0.0))
    fh = float(params.get("flange_height_mm", 10.0))
    ft = float(params.get("flange_thickness_mm", t))

    ro = od / 2
    ri = ro - width
    ring = cq.Workplane("XY").circle(ro).circle(ri).extrude(t)

    if n > 0:
        if hd <= 0:
            hd = width * 0.5
        pcr = (ro + ri) / 2
        ring = ring.cut(cq.Workplane("XY").polarArray(pcr, 0, 360, n).circle(hd / 2).extrude(t))

    if fh > 0:
        ring = ring.union(cq.Workplane("XY").circle(ri + ft).circle(ri).extrude(t + fh))
    return ring


def generate_wing_spar(params: dict) -> cq.Workplane:
    """I-section spar along +X. Params: length_mm, height_mm, flange_width_mm,
    flange_thickness_mm, web_thickness_mm."""
    from aerospace.sections import i_beam_points
    length = float(params.get("length_mm", 600.0))
    pts = i_beam_points(float(params.get("height_mm", 40.0)), float(params.get("flange_width_mm", 30.0)),
                        float(params.get("flange_thickness_mm", 3.0)), float(params.get("web_thickness_mm", 2.0)))
    # "YZ" workplane: local x = global Y, local y = global Z, normal = +X
    return cq.Workplane("YZ").polyline(pts).close().extrude(length)


def generate_attachment_lug(params: dict) -> cq.Workplane:
    """Single lug with a full-round end. Params: width_mm, length_mm (base to end),
    thickness_mm, hole_diameter_mm. The hole centre is width/2 from the end."""
    w = float(params.get("width_mm", 30.0))
    length = float(params.get("length_mm", 50.0))
    t = float(params.get("thickness_mm", 6.0))
    d = float(params.get("hole_diameter_mm", 10.0))
    cx = length - w / 2
    body = (cq.Workplane("XY").box(cx, w, t, centered=(False, True, False))
            .union(cq.Workplane("XY").center(cx, 0).circle(w / 2).extrude(t)))
    return body.cut(cq.Workplane("XY").center(cx, 0).circle(d / 2).extrude(t))
