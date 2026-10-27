"""
Standard-part dimension tables (ISO fasteners, ISO 15 deep-groove bearings).

Pure data and lookups: no CadQuery import, so the validator, the assembly
planner and the tests can use it without the CAD kernel.

SOURCE AND VERIFICATION: values were entered from the published ISO tables
(ISO 4762, ISO 4017, ISO 4032, ISO 7089, ISO 15) from memory of the
standards, in an environment with no access to the standard documents.
Before this ships as an "industrial standard" feature, check every row
against your licensed copy of each standard and set VERIFIED_AGAINST_STANDARD
to the date and person who did. tests/test_standards_data.py pins structural
sanity (e.g. nut bore < across-flats), not the individual values.

All dimensions in millimetres. Threads are NOT modelled geometrically; the
shank is a plain cylinder at the nominal diameter, which is standard CAD
practice for assemblies (the designation carries the thread).
"""
from __future__ import annotations

import re

VERIFIED_AGAINST_STANDARD: str | None = None  # e.g. "2026-11-02 by <name>"

# Coarse pitch by nominal size (ISO 261 / ISO 262).
PITCH = {
    "M2": 0.4, "M2.5": 0.45, "M3": 0.5, "M4": 0.7, "M5": 0.8, "M6": 1.0,
    "M8": 1.25, "M10": 1.5, "M12": 1.75, "M16": 2.0, "M20": 2.5, "M24": 3.0,
}

# ISO 4762 socket head cap screw: size -> (dk head dia, k head height, s socket AF, t socket depth)
ISO_4762 = {
    "M2": (3.8, 2.0, 1.5, 1.0), "M2.5": (4.5, 2.5, 2.0, 1.1), "M3": (5.5, 3.0, 2.5, 1.3),
    "M4": (7.0, 4.0, 3.0, 2.0), "M5": (8.5, 5.0, 4.0, 2.5), "M6": (10.0, 6.0, 5.0, 3.0),
    "M8": (13.0, 8.0, 6.0, 4.0), "M10": (16.0, 10.0, 8.0, 5.0), "M12": (18.0, 12.0, 10.0, 6.0),
    "M16": (24.0, 16.0, 14.0, 8.0), "M20": (30.0, 20.0, 17.0, 10.0), "M24": (36.0, 24.0, 19.0, 12.0),
}

# ISO 4017 hexagon head screw: size -> (s across flats, k head height)
ISO_4017 = {
    "M2": (4.0, 1.4), "M2.5": (5.0, 1.7), "M3": (5.5, 2.0), "M4": (7.0, 2.8),
    "M5": (8.0, 3.5), "M6": (10.0, 4.0), "M8": (13.0, 5.3), "M10": (16.0, 6.4),
    "M12": (18.0, 7.5), "M16": (24.0, 10.0), "M20": (30.0, 12.5), "M24": (36.0, 15.0),
}

# ISO 4032 hexagon nut, style 1: size -> (s across flats, m thickness)
ISO_4032 = {
    "M2": (4.0, 1.6), "M2.5": (5.0, 2.0), "M3": (5.5, 2.4), "M4": (7.0, 3.2),
    "M5": (8.0, 4.7), "M6": (10.0, 5.2), "M8": (13.0, 6.8), "M10": (16.0, 8.4),
    "M12": (18.0, 10.8), "M16": (24.0, 14.8), "M20": (30.0, 18.0), "M24": (36.0, 21.5),
}

# ISO 7089 plain washer, normal series: size -> (d1 inner, d2 outer, h thickness)
ISO_7089 = {
    "M2": (2.2, 5.0, 0.3), "M2.5": (2.7, 6.0, 0.5), "M3": (3.2, 7.0, 0.5), "M4": (4.3, 9.0, 0.8),
    "M5": (5.3, 10.0, 1.0), "M6": (6.4, 12.0, 1.6), "M8": (8.4, 16.0, 1.6), "M10": (10.5, 20.0, 2.0),
    "M12": (13.0, 24.0, 2.5), "M16": (17.0, 30.0, 3.0), "M20": (21.0, 37.0, 3.0), "M24": (25.0, 44.0, 4.0),
}

FASTENER_STANDARDS = {
    "ISO_4762": "hexagon socket head cap screw",
    "ISO_4017": "hexagon head screw, full thread",
    "ISO_4032": "hexagon nut, style 1",
    "ISO_7089": "plain washer, normal series",
}
_TABLES = {"ISO_4762": ISO_4762, "ISO_4017": ISO_4017, "ISO_4032": ISO_4032, "ISO_7089": ISO_7089}
# Standards where length_mm is a design input (screws); nuts and washers ignore it.
LENGTH_STANDARDS = frozenset({"ISO_4762", "ISO_4017"})

# ISO 4762/4017 preferred nominal length series (mm).
STANDARD_LENGTHS = [
    2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70,
    80, 90, 100, 110, 120, 130, 140, 150, 160, 180, 200,
]

# ISO 15 deep-groove ball bearings: designation -> (d bore, D outer, B width)
BEARINGS = {
    # miniature 60x / 62x
    "623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6),
    "627": (7, 22, 7), "628": (8, 24, 8), "629": (9, 26, 8),
    "604": (4, 12, 4), "605": (5, 14, 5), "606": (6, 17, 6), "607": (7, 19, 6),
    "608": (8, 22, 7), "609": (9, 24, 7),
    # 6000 series (light)
    "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9), "6003": (17, 35, 10),
    "6004": (20, 42, 12), "6005": (25, 47, 12), "6006": (30, 55, 13), "6007": (35, 62, 14),
    "6008": (40, 68, 15), "6009": (45, 75, 16), "6010": (50, 80, 16),
    # 6200 series (medium)
    "6200": (10, 30, 9), "6201": (12, 32, 10), "6202": (15, 35, 11), "6203": (17, 40, 12),
    "6204": (20, 47, 14), "6205": (25, 52, 15), "6206": (30, 62, 16), "6207": (35, 72, 17),
    "6208": (40, 80, 18), "6209": (45, 85, 19), "6210": (50, 90, 20),
    # 6300 series (heavy)
    "6300": (10, 35, 11), "6301": (12, 37, 12), "6302": (15, 42, 13), "6303": (17, 47, 14),
    "6304": (20, 52, 15), "6305": (25, 62, 17), "6306": (30, 72, 19), "6307": (35, 80, 21),
    "6308": (40, 90, 23), "6309": (45, 100, 25), "6310": (50, 110, 27),
}

# DIN 6885 / ISO 773 parallel keys: (shaft d over, up to and incl.) -> (b width, h height, t1 shaft keyway depth, t2 hub keyway depth)
KEY_TABLE = [
    (6, 8, 2, 2, 1.2, 1.0), (8, 10, 3, 3, 1.8, 1.4), (10, 12, 4, 4, 2.5, 1.8),
    (12, 17, 5, 5, 3.0, 2.3), (17, 22, 6, 6, 3.5, 2.8), (22, 30, 8, 7, 4.0, 3.3),
    (30, 38, 10, 8, 5.0, 3.3), (38, 44, 12, 8, 5.0, 3.3), (44, 50, 14, 9, 5.5, 3.8),
    (50, 58, 16, 10, 6.0, 4.3), (58, 65, 18, 11, 7.0, 4.4), (65, 75, 20, 12, 7.5, 4.9),
    (75, 85, 22, 14, 9.0, 5.4), (85, 95, 25, 14, 9.0, 5.4), (95, 110, 28, 16, 10.0, 6.4),
]
KEY_LENGTHS = [
    6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 32, 36, 40, 45, 50, 56, 63, 70, 80,
    90, 100, 110, 125, 140, 160, 180, 200, 220, 250, 280, 320,
]
# permitted key length range per width b
KEY_LENGTH_RANGE = {
    2: (6, 20), 3: (6, 36), 4: (8, 45), 5: (10, 56), 6: (14, 70), 8: (18, 90), 10: (22, 110),
    12: (28, 140), 14: (36, 160), 16: (45, 180), 18: (50, 200), 20: (56, 220), 22: (63, 250),
    25: (70, 280), 28: (80, 320),
}

# ASME B16.5 Class 150 flanges (inch values as published): NPS -> (OD, thickness, bolt circle, bolt count,
# bolt hole dia, raised-face dia). Thickness is the minimum flange thickness excluding the raised face.
FLANGE_ASME_150_IN = {
    "1/2": (3.50, 0.44, 2.38, 4, 0.62, 1.38), "3/4": (3.88, 0.50, 2.75, 4, 0.62, 1.69),
    "1": (4.25, 0.56, 3.12, 4, 0.62, 2.00), "1-1/4": (4.62, 0.62, 3.50, 4, 0.62, 2.50),
    "1-1/2": (5.00, 0.69, 3.88, 4, 0.62, 2.88), "2": (6.00, 0.75, 4.75, 4, 0.75, 3.62),
    "2-1/2": (7.00, 0.88, 5.50, 4, 0.75, 4.12), "3": (7.50, 0.94, 6.00, 4, 0.75, 5.00),
    "3-1/2": (8.50, 0.94, 7.00, 8, 0.75, 5.50), "4": (9.00, 0.94, 7.50, 8, 0.75, 6.19),
    "5": (10.00, 0.94, 8.50, 8, 0.88, 7.31), "6": (11.00, 1.00, 9.50, 8, 0.88, 8.50),
    "8": (13.50, 1.12, 11.75, 8, 0.88, 10.62), "10": (16.00, 1.19, 14.25, 12, 1.00, 12.75),
    "12": (19.00, 1.25, 17.00, 12, 1.00, 15.00),
}
# Pipe outside diameter by NPS in mm (ASME B36.10M); a slip-on flange bore is modelled at this diameter.
PIPE_OD_MM = {
    "1/2": 21.3, "3/4": 26.7, "1": 33.4, "1-1/4": 42.2, "1-1/2": 48.3, "2": 60.3, "2-1/2": 73.0,
    "3": 88.9, "3-1/2": 101.6, "4": 114.3, "5": 141.3, "6": 168.3, "8": 219.1, "10": 273.1, "12": 323.9,
}
FLANGE_STANDARDS = {"ASME_B16_5_150": "ASME B16.5 Class 150 flange"}
FLANGE_TYPES = ("slip_on", "blind")
FLANGE_RAISED_FACE_MM = 1.6  # 1/16 in raised face (Class 150)

# O-ring cross-sections (mm): AS568 inch-series metric equivalents and ISO 3601-1 nominal series.
O_RING_CROSS_SECTIONS = (1.78, 2.62, 3.53, 5.33, 6.99, 1.8, 2.65, 3.55, 5.3, 7.0)

HINTS = {
    "fastener": (
        "standard is one of " + ", ".join(f"{k} ({v})" for k, v in FASTENER_STANDARDS.items())
        + "; size like M8 (" + ", ".join(PITCH) + "); length_mm is the shank length for screws and must be a "
        "standard length (ignored for nuts and washers). Axis +Z: the head underside / nut / washer base sits "
        "at the part origin and a screw shank extends along +Z."
    ),
    "key": (
        "DIN 6885 parallel key (form A round ends or B square ends), sized from shaft_diameter_mm (6 to 110); "
        "length_mm must be a standard length (snapped if not). Key length runs along +Z, width along X, height "
        "along +Y with the key bottom at the origin. To seat it: set the shaft's keyway_width_mm to the key width b "
        "and keyway_depth_mm to the shaft keyway depth t1 (b, h, t1 are derived from the shaft diameter: e.g. 25 mm "
        "-> b 8, h 7, t1 4.0), then place the key at y = shaft_radius - t1."
    ),
    "asme_flange": (
        "ASME B16.5 Class 150 pipe flange: nps is the nominal pipe size as a string (" + ", ".join(FLANGE_ASME_150_IN)
        + "); flange_type is \"slip_on\" (bore = pipe OD) or \"blind\" (no bore). OD, thickness, bolt circle, bolt "
        "count and hole size come from the standard; do not set them. Axis +Z: back face at the origin, raised face on top."
    ),
    "o_ring": (
        "O-ring as a torus: inner_diameter_mm and cross_section_mm; the cross-section must be a standard size ("
        + ", ".join(f"{c:g}" for c in O_RING_CROSS_SECTIONS) + " mm). Lies in the XY plane from z=0 up by the cross-section."
    ),
    "std_bearing": (
        "designation is an ISO 15 deep-groove ball bearing number such as 6205 or 608 (bore, outer diameter and "
        "width come from the standard; do not set them). Axis +Z, from the origin up by the bearing width."
    ),
}


def normalize_size(size: str) -> str:
    return str(size).strip().upper().replace(" ", "")


def lookup_fastener(standard: str, size: str) -> dict:
    """Dimensions for one fastener, or ValueError listing the valid choices."""
    std = str(standard).strip().upper().replace(" ", "_").replace("-", "_")
    if std not in _TABLES:
        raise ValueError(f"Unknown fastener standard {standard!r}. Valid: {', '.join(_TABLES)}.")
    sz = normalize_size(size)
    row = _TABLES[std].get(sz)
    if row is None:
        raise ValueError(f"{std} has no size {size!r}. Valid: {', '.join(_TABLES[std])}.")
    out = {"standard": std, "size": sz, "d": float(sz[1:]), "pitch": PITCH[sz]}
    if std == "ISO_4762":
        out.update(dk=row[0], k=row[1], s=row[2], t=row[3])
    elif std == "ISO_4017":
        out.update(s=row[0], k=row[1])
    elif std == "ISO_4032":
        out.update(s=row[0], m=row[1])
    else:
        out.update(d1=row[0], d2=row[1], h=row[2])
    return out


def nearest_standard_length(length_mm: float) -> int:
    return min(STANDARD_LENGTHS, key=lambda s: (abs(s - length_mm), s))


_DESIG_RE = re.compile(r"^(\d{3,4})(?:[\s\-]?(?:2RS1?|2RSR?|RS1?|ZZ|Z|2Z|C3|C\d|N|NR|M))*$")


def lookup_bearing(designation: str) -> dict:
    """ISO 15 deep-groove bearing by number. Seal/clearance suffixes (-2RS, ZZ, C3) are accepted
    and ignored: they do not change the envelope dimensions."""
    raw = str(designation).strip().upper()
    m = _DESIG_RE.match(raw)
    base = m.group(1) if m else raw
    row = BEARINGS.get(base)
    if row is None:
        raise ValueError(f"Unknown bearing designation {designation!r}. Valid: {', '.join(BEARINGS)}.")
    return {"designation": base, "d": float(row[0]), "D": float(row[1]), "B": float(row[2])}


def lookup_key(shaft_diameter_mm: float) -> dict:
    """Key cross-section for a shaft diameter (DIN 6885 range: over 6 up to and including 110 mm)."""
    d = float(shaft_diameter_mm)
    for lo, hi, b, h, t1, t2 in KEY_TABLE:
        if lo < d <= hi:
            return {"b": float(b), "h": float(h), "t1": t1, "t2": t2,
                    "length_min": KEY_LENGTH_RANGE[b][0], "length_max": KEY_LENGTH_RANGE[b][1]}
    raise ValueError(f"No DIN 6885 key for shaft diameter {shaft_diameter_mm} mm; valid range is over 6 up to 110 mm.")


def standard_key_length(length_mm: float, length_min: float, length_max: float) -> int:
    """Nearest standard key length inside the permitted range for this key width."""
    allowed = [x for x in KEY_LENGTHS if length_min <= x <= length_max]
    return min(allowed, key=lambda x: (abs(x - length_mm), x))


def lookup_flange(standard: str, nps: str, flange_type: str = "slip_on") -> dict:
    """ASME B16.5 Class 150 flange dimensions in mm, or ValueError listing valid choices."""
    std = str(standard).strip().upper().replace(" ", "_").replace("-", "_").replace(".", "_")
    if std not in FLANGE_STANDARDS:
        raise ValueError(f"Unknown flange standard {standard!r}. Valid: {', '.join(FLANGE_STANDARDS)}.")
    size = str(nps).strip().replace(" ", "-").replace('"', "")
    row = FLANGE_ASME_150_IN.get(size)
    if row is None:
        raise ValueError(f"No Class 150 flange for NPS {nps!r}. Valid: {', '.join(FLANGE_ASME_150_IN)}.")
    ftype = str(flange_type).strip().lower().replace("-", "_").replace(" ", "_")
    if ftype not in FLANGE_TYPES:
        raise ValueError(f"Unknown flange type {flange_type!r}. Valid: {', '.join(FLANGE_TYPES)}.")
    od, t, pcd, n, hole, rf = row
    mm = lambda inches: round(inches * 25.4, 2)  # noqa: E731
    return {"standard": std, "nps": size, "flange_type": ftype, "od": mm(od), "thickness": mm(t), "bolt_circle": mm(pcd),
            "bolt_count": n, "bolt_hole": mm(hole), "raised_face_dia": mm(rf), "raised_face_height": FLANGE_RAISED_FACE_MM,
            "bore": PIPE_OD_MM[size] if ftype == "slip_on" else 0.0}


def is_standard_o_ring_cross_section(cs_mm: float) -> bool:
    return any(abs(cs_mm - c) < 1e-9 for c in O_RING_CROSS_SECTIONS)
