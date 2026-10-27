"""
Aerospace domain pack: shared constants and derived (non-structural) metrics.

NitoCAD's maturity statement for this pack: ARCHITECTED + PARTIALLY
IMPLEMENTED. Geometry templates exist; requirements traceability, structural
analysis, certification workflows and revision-bound evidence do NOT yet.
Nothing here should be presented as flight-ready or certified.
"""
AEROSPACE_PART_TYPES = ("airfoil_wing", "wing_rib", "fuselage_frame", "wing_spar", "attachment_lug",
                        "fuselage", "tail_surface", "turbofan_engine", "jet_aircraft")

GEOMETRY_ONLY_NOTICE = (
    "Aerospace geometry is preliminary. A valid CAD solid does not establish structural "
    "adequacy, fatigue life, environmental suitability or regulatory compliance. "
    "Loads, material and certification evidence must be supplied and verified separately."
)


def wing_metrics(params: dict) -> dict:
    """Planform numbers for a (semi-)wing. Geometry only, not aerodynamic claims."""
    cr = float(params.get("root_chord_mm", 200.0))
    ct = float(params.get("tip_chord_mm", 120.0))
    b = float(params.get("span_mm", 600.0))
    area = (cr + ct) / 2 * b
    taper = ct / cr if cr else 0.0
    mac = (2 / 3) * cr * (1 + taper + taper ** 2) / (1 + taper) if cr else 0.0
    return {
        "planform_area_mm2": round(area, 1),
        "taper_ratio": round(taper, 3),
        "mean_aero_chord_mm": round(mac, 1),
        "aspect_ratio_semi_span": round(b * b / area, 2) if area else 0.0,
    }
