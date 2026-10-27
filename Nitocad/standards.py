"""
Standard-part data and lookups: metric threads and fasteners, deep-groove bearings, steel
pipe, and structural sections.

Pure data and arithmetic, no CAD. Every table is transcribed from the named standard's
nominal dimensions. Where a standard has options (clearance fit, schedule) the option is an
explicit argument and never guessed. Anything not in a table is "unknown": lookups return
None rather than interpolating a size.

  threads / fasteners   ISO 261 / 262 (metric coarse), ISO 273 (clearance holes),
                        ISO 4017 (hex bolt), ISO 4762 (socket head cap screw),
                        ISO 4032 (hex nut), ISO 7089 (plain washer)
  bearings              ISO 15 deep-groove ball bearing boundary dimensions
  pipe                  ASME B36.10M / B36.19M nominal pipe size, schedules 40 and 80
  sections              European IPE beams and UPN channels (EN 10365)
"""
from __future__ import annotations

import math
import re
from typing import Any

# size: (pitch coarse, ISO 4017 s, ISO 4017 k, ISO 4762 dk, ISO 4762 k, ISO 4762 s,
#        ISO 4032 m, ISO 7089 d1, ISO 7089 d2, ISO 7089 h, ISO 273 fine/medium/coarse)
_METRIC = {
    "M3":  (0.5,  5.5, 2.0,  5.5,  3.0,  2.5, 2.4,  3.2,  7.0, 0.5, (3.2, 3.4, 3.6)),
    "M4":  (0.7,  7.0, 2.8,  7.0,  4.0,  3.0, 3.2,  4.3,  9.0, 0.8, (4.3, 4.5, 4.8)),
    "M5":  (0.8,  8.0, 3.5,  8.5,  5.0,  4.0, 4.7,  5.3, 10.0, 1.0, (5.3, 5.5, 5.8)),
    "M6":  (1.0, 10.0, 4.0, 10.0,  6.0,  5.0, 5.2,  6.4, 12.0, 1.6, (6.4, 6.6, 7.0)),
    "M8":  (1.25, 13.0, 5.3, 13.0,  8.0,  6.0, 6.8,  8.4, 16.0, 1.6, (8.4, 9.0, 10.0)),
    "M10": (1.5, 16.0, 6.4, 16.0, 10.0,  8.0, 8.4, 10.5, 20.0, 2.0, (10.5, 11.0, 12.0)),
    "M12": (1.75, 18.0, 7.5, 18.0, 12.0, 10.0, 10.8, 13.0, 24.0, 2.5, (13.0, 13.5, 14.5)),
    "M16": (2.0, 24.0, 10.0, 24.0, 16.0, 14.0, 14.8, 17.0, 30.0, 3.0, (17.0, 17.5, 18.5)),
    "M20": (2.5, 30.0, 12.5, 30.0, 20.0, 17.0, 18.0, 21.0, 37.0, 3.0, (21.0, 22.0, 24.0)),
    "M24": (3.0, 36.0, 15.0, 36.0, 24.0, 19.0, 21.5, 25.0, 44.0, 4.0, (25.0, 26.0, 28.0)),
}

# ISO 898-1 / ISO 3506 strengths, MPa: (proof or 0.2% yield stress, minimum tensile strength)
PROPERTY_CLASSES = {
    "4.6": (240, 400), "5.8": (420, 520), "8.8": (580, 800), "10.9": (830, 1040), "12.9": (970, 1220),
    "A2-70": (450, 700), "A4-70": (450, 700), "A2-80": (600, 800), "A4-80": (600, 800),
}
FIT = {"fine": 0, "medium": 1, "coarse": 2}


def thread_sizes() -> list[str]:
    return list(_METRIC)


def normalize_size(size: str) -> str | None:
    """'m8', 'M 8', '8mm' no; 'M8' yes. Returns the table key or None."""
    if not isinstance(size, str):
        return None
    m = re.fullmatch(r"\s*[Mm]\s*(\d+(?:\.\d+)?)\s*", size)
    if not m:
        return None
    key = "M" + m.group(1).rstrip("0").rstrip(".") if "." in m.group(1) else "M" + m.group(1)
    return key if key in _METRIC else None


def metric_thread(size: str) -> dict[str, Any] | None:
    key = normalize_size(size)
    if key is None:
        return None
    d = float(key[1:])
    pitch, *_rest, fits = _METRIC[key]
    return {
        "size": key, "nominal_diameter_mm": d, "pitch_mm": pitch,
        "minor_diameter_mm": round(d - 1.22687 * pitch, 3),          # external thread d3
        "tap_drill_mm": round(d - pitch, 2),
        "stress_area_mm2": round(math.pi / 4 * (d - 0.9382 * pitch) ** 2, 1),
        "clearance_hole_mm": {"fine": fits[0], "medium": fits[1], "coarse": fits[2]},
        "standard": "ISO 261/262, ISO 273",
    }


def clearance_hole(size: str, fit: str = "medium") -> float | None:
    t = metric_thread(size)
    if t is None or fit not in FIT:
        return None
    return t["clearance_hole_mm"][fit]


def hex_bolt(size: str, length_mm: float) -> dict[str, Any] | None:
    key = normalize_size(size)
    if key is None or not _ok(length_mm):
        return None
    pitch, s, k, *_ = _METRIC[key]
    return {"size": key, "length_mm": float(length_mm), "head_across_flats_mm": s, "head_height_mm": k,
            "nominal_diameter_mm": float(key[1:]), "pitch_mm": pitch, "standard": "ISO 4017"}


def socket_cap_screw(size: str, length_mm: float) -> dict[str, Any] | None:
    key = normalize_size(size)
    if key is None or not _ok(length_mm):
        return None
    pitch, _s, _k, dk, k, s_key, *_ = _METRIC[key]
    return {"size": key, "length_mm": float(length_mm), "head_diameter_mm": dk, "head_height_mm": k,
            "socket_across_flats_mm": s_key, "nominal_diameter_mm": float(key[1:]), "pitch_mm": pitch,
            "standard": "ISO 4762"}


def hex_nut(size: str) -> dict[str, Any] | None:
    key = normalize_size(size)
    if key is None:
        return None
    pitch, s, _k, _dk, _k2, _sk, m, *_ = _METRIC[key]
    return {"size": key, "across_flats_mm": s, "height_mm": m, "nominal_diameter_mm": float(key[1:]),
            "pitch_mm": pitch, "standard": "ISO 4032"}


def plain_washer(size: str) -> dict[str, Any] | None:
    key = normalize_size(size)
    if key is None:
        return None
    *_a, d1, d2, h, _fits = _METRIC[key]
    return {"size": key, "inner_diameter_mm": d1, "outer_diameter_mm": d2, "thickness_mm": h, "standard": "ISO 7089"}


def _ok(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


# ----------------------------------------------------------------- bearings ---

# designation: (bore d, outside D, width B) in mm, ISO 15 deep-groove ball bearings
BEARINGS = {
    "623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "627": (7, 22, 7),
    "628": (8, 24, 8), "629": (9, 26, 8), "604": (4, 12, 4), "605": (5, 14, 5), "606": (6, 17, 6),
    "607": (7, 19, 6), "608": (8, 22, 7), "609": (9, 24, 7),
    "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9), "6003": (17, 35, 10),
    "6004": (20, 42, 12), "6005": (25, 47, 12), "6006": (30, 55, 13), "6007": (35, 62, 14),
    "6008": (40, 68, 15),
    "6200": (10, 30, 9), "6201": (12, 32, 10), "6202": (15, 35, 11), "6203": (17, 40, 12),
    "6204": (20, 47, 14), "6205": (25, 52, 15), "6206": (30, 62, 16), "6207": (35, 72, 17),
    "6208": (40, 80, 18),
    "6300": (10, 35, 11), "6301": (12, 37, 12), "6302": (15, 42, 13), "6303": (17, 47, 14),
    "6304": (20, 52, 15), "6305": (25, 62, 17), "6306": (30, 72, 19),
}


def bearing(designation: str) -> dict[str, Any] | None:
    """Deep-groove ball bearing by designation. Suffixes (2RS, ZZ, C3) are ignored: they change
    seals and clearance, not the boundary dimensions."""
    m = re.match(r"\s*(\d{3,4})(?:[\s\-]*[A-Za-z0-9]{1,4})?\s*$", str(designation))
    key = m.group(1) if m else None
    if key not in BEARINGS:
        return None
    d, D, B = BEARINGS[key]
    return {"designation": key, "inner_diameter_mm": float(d), "outer_diameter_mm": float(D), "width_mm": float(B),
            "standard": "ISO 15"}


# --------------------------------------------------------------------- pipe ---

# NPS: (outside diameter, wall sch 40, wall sch 80), mm
_PIPE = {
    "1/2": (21.34, 2.77, 3.73), "3/4": (26.67, 2.87, 3.91), "1": (33.40, 3.38, 4.55),
    "1-1/4": (42.16, 3.56, 4.85), "1-1/2": (48.26, 3.68, 5.08), "2": (60.33, 3.91, 5.54),
    "2-1/2": (73.03, 5.16, 7.01), "3": (88.90, 5.49, 7.62), "4": (114.30, 6.02, 8.56), "6": (168.28, 7.11, 10.97),
}


def pipe(nps: str, schedule: str | int = 40) -> dict[str, Any] | None:
    key = str(nps).strip().replace(" ", "-").replace('"', "")
    sched = str(schedule).lower().replace("sch", "").replace("schedule", "").strip(" .")
    if key not in _PIPE or sched not in ("40", "80"):
        return None
    od, w40, w80 = _PIPE[key]
    return {"nps": key, "schedule": sched, "outer_diameter_mm": od,
            "wall_thickness_mm": w40 if sched == "40" else w80, "standard": "ASME B36.10M"}


# ----------------------------------------------------------------- sections ---

# designation: (height h, width b, web tw, flange tf) in mm
_IPE = {"IPE100": (100, 55, 4.1, 5.7), "IPE120": (120, 64, 4.4, 6.3), "IPE140": (140, 73, 4.7, 6.9),
        "IPE160": (160, 82, 5.0, 7.4), "IPE180": (180, 91, 5.3, 8.0), "IPE200": (200, 100, 5.6, 8.5),
        "IPE220": (220, 110, 5.9, 9.2), "IPE240": (240, 120, 6.2, 9.8), "IPE270": (270, 135, 6.6, 10.2),
        "IPE300": (300, 150, 7.1, 10.7)}
_UPN = {"UPN80": (80, 45, 6.0, 8.0), "UPN100": (100, 50, 6.0, 8.5), "UPN120": (120, 55, 7.0, 9.0),
        "UPN140": (140, 60, 7.0, 10.0), "UPN160": (160, 65, 7.5, 10.5), "UPN200": (200, 75, 8.5, 11.5)}


def section(designation: str) -> dict[str, Any] | None:
    key = re.sub(r"[\s\-]", "", str(designation)).upper()
    table = _IPE if key in _IPE else _UPN if key in _UPN else None
    if table is None:
        return None
    h, b, tw, tf = table[key]
    return {"designation": key, "beam_type": "i_beam" if table is _IPE else "channel", "height_mm": float(h),
            "width_mm": float(b), "web_thickness_mm": tw, "flange_thickness_mm": tf,
            "standard": "EN 10365"}


# ------------------------------------------------------------ free-text find ---

def find_standard(text: str) -> dict[str, Any] | None:
    """Recognise a standard part named in plain text and return ready-to-use template parameters.

    Returns {"part_type", "parameters", "standard", "label", "notes"} or None. Never guesses:
    an unknown size or a missing length returns None."""
    t = str(text)
    low = t.lower()

    m = re.search(r"\b(?:ipe|upn)\s?-?\s?\d{2,3}\b", t, re.I)
    if m:
        sec = section(m.group(0))
        if sec:
            return {"part_type": "structural_beam", "label": sec["designation"], "standard": sec["standard"],
                    "parameters": {"beam_type": sec["beam_type"], "height_mm": sec["height_mm"], "width_mm": sec["width_mm"],
                                   "thickness_mm": sec["flange_thickness_mm"]},
                    "notes": ["The beam template has a single plate thickness, so the flange thickness is used. "
                              f"The real web is {sec['web_thickness_mm']} mm."]}
    m = re.search(r"\bnps\s*(\d+(?:[- ]\d/\d|/\d)?)\b.*?\b(?:sch(?:edule)?\.?\s*)(40|80)\b", low) or \
        re.search(r"\b(\d+(?:[- ]\d/\d|/\d)?)\s*(?:in(?:ch)?|\")?\s*(?:nps\s*)?(?:pipe\s*)?sch(?:edule)?\.?\s*(40|80)\b", low)
    if m:
        p = pipe(m.group(1), m.group(2))
        if p:
            return {"part_type": "tube", "label": f"NPS {p['nps']} sch {p['schedule']}", "standard": p["standard"],
                    "parameters": {"shape": "round", "outer_diameter_mm": p["outer_diameter_mm"],
                                   "wall_thickness_mm": p["wall_thickness_mm"]}, "notes": []}
    m = re.search(r"\b(6[0-3]\d{2}|60\d|62\d)\b(?=[^\d]*(?:bearing|2rs|zz|$)|\s)", t) if re.search(r"bearing", low) else None
    if m:
        b = bearing(m.group(1))
        if b:
            return {"part_type": "bearing", "label": b["designation"], "standard": b["standard"],
                    "parameters": {k: b[k] for k in ("inner_diameter_mm", "outer_diameter_mm", "width_mm")}, "notes": []}
    m = re.search(r"\b(m\s?\d{1,2}(?:\.\d)?)\b", low)
    if m:
        size = normalize_size(m.group(1))
        if size:
            length = re.search(r"(?:x|\u00d7|\s)\s*(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:long|length)?", low[m.end():])
            if re.search(r"socket|cap screw|shcs|allen|din 912|iso 4762", low):
                kind, fn, ptype = "socket head cap screw", socket_cap_screw, "socket_cap_screw"
            elif re.search(r"\bnut\b", low):
                return {"part_type": "hex_nut", "label": f"{size} nut", "standard": "ISO 4032",
                        "parameters": {"size": size}, "notes": []}
            elif re.search(r"washer", low):
                w = plain_washer(size)
                return {"part_type": "washer", "label": f"{size} washer", "standard": w["standard"],
                        "parameters": {k: w[k] for k in ("outer_diameter_mm", "inner_diameter_mm", "thickness_mm")}, "notes": []}
            elif re.search(r"bolt|screw|hex head|iso 4017|din 933", low):
                kind, fn, ptype = "hex bolt", hex_bolt, "hex_bolt"
            else:
                return None
            if length and fn(size, float(length.group(1))):
                return {"part_type": ptype, "label": f"{size} x {length.group(1)} {kind}", "standard": fn(size, 1)["standard"],
                        "parameters": {"size": size, "length_mm": float(length.group(1))}, "notes": []}
    return None
