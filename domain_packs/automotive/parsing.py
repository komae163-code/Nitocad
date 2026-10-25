"""
Deterministic (no-LLM) parsing for the automotive pack: part-type inference
and parameter extraction. This is the FALLBACK path - the LLM parser is
primary and its prompt is generated from the same specs. Extraction here only
sets a parameter when the description states it; everything unstated is left
out so the template default applies.

Inference matches specific multi-word phrases only, never bare generic words
('hub', 'rotor', 'arm', 'mount' alone are far too ambiguous), so it cannot
shadow the core mechanical keywords.
"""
from __future__ import annotations

import re

from .standards import parse_bolt_pattern

_UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4}


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.lower()).strip()


_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    # part_type, any-of phrases (substring), none-of patterns (regex)
    ("brake_rotor", ("brake rotor", "brake disc", "brake disk", "disc rotor", "disk rotor",
                     "vented rotor", "vented disc", "vented disk", "ventilated rotor",
                     "ventilated disc", "solid rotor", "solid brake disc"), ()),
    ("ball_joint", ("ball joint", "balljoint"), ()),
    ("subframe", ("subframe", "sub frame", "suspension cradle", "engine cradle", "front cradle"), ()),
    ("steering_knuckle", ("steering knuckle", "wheel knuckle", "hub carrier", "wheel carrier",
                          "spindle knuckle", "wheel upright"), ()),
    ("wheel_hub", ("wheel hub", "hub flange", "wheel bearing hub", "hub unit",
                   "front hub", "rear hub", "lobed hub"), ()),
    ("piston", ("piston",), (r"piston\s+rods?\b", r"piston\s+pins?\b(?!\s+bore)", r"piston\s+rings?\b(?!\s+grooves?)",
                              r"connecting\s+rod", r"con\s+rod", r"conrod", r"piston\s+pump",
                              r"hydraulic\s+piston", r"pneumatic\s+piston", r"piston\s+cylinder")),
    ("flywheel", ("flywheel", "fly wheel"), ()),
    ("control_arm", ("control arm", "wishbone", "a arm", "suspension arm", "lower arm",
                     "upper arm", "trailing arm", "lower control", "upper control"), ()),
    ("battery_bracket", ("battery bracket", "battery mount", "battery mounting bracket",
                         "battery enclosure bracket", "battery enclosure mount",
                         "battery pack bracket", "battery pack mount", "battery tray bracket",
                         "ev battery bracket", "battery support bracket"), ()),
]


def infer_part_type(text: str) -> str | None:
    t = _norm(text)
    for part, any_of, none_of in _RULES:
        if any(p in t for p in any_of) and not any(re.search(n, t) for n in none_of):
            return part
    return None


def _to_mm(value: float, unit: str | None) -> float:
    return value * _UNIT_MM.get((unit or "mm").lower(), 1.0)


_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = r"(mm|cm|inch|inches|in)?"


def _before(text: str, words: str) -> float | None:
    """Number (with optional unit) that directly precedes <up to 3 words> then one of `words`:
    '300mm vented brake rotor', '86 mm dished piston'."""
    m = re.search(_NUM + r"\s*" + _UNIT + r"\s*(?:[a-z]+\s+){0,3}?(?:" + words + r")\b", text, re.I)
    return _to_mm(float(m.group(1)), m.group(2)) if m else None


def _after(text: str, words: str) -> float | None:
    """Number that follows a label: 'center bore 66.1', 'thickness of 28mm'."""
    m = re.search(r"(?:" + words + r")\s*(?:diameter|dia)?\s*(?:of|is|=|:)?\s*" + _NUM + r"\s*" + _UNIT, text, re.I)
    return _to_mm(float(m.group(1)), m.group(2)) if m else None


def _num_then(text: str, words: str) -> float | None:
    """'66.1mm center bore', '12mm thick'."""
    m = re.search(_NUM + r"\s*" + _UNIT + r"\s*(?:" + words + r")\b", text, re.I)
    return _to_mm(float(m.group(1)), m.group(2)) if m else None


def _first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _put(out: dict, key: str, val) -> None:
    if val is not None:
        out[key] = val


def _common_pattern(text: str, out: dict) -> None:
    bp = parse_bolt_pattern(text)
    if bp:
        out["bolt_count"], out["bolt_pcd_mm"] = bp


def extract_parameters(part_type: str, text: str, dim: dict) -> dict:
    t = text.lower()
    out: dict = {}

    if part_type == "brake_rotor":
        _put(out, "outer_diameter_mm", _first(_before(t, r"rotor|disc|disk"), dim.get("diameter")))
        _put(out, "thickness_mm", _first(_after(t, r"thickness"), _num_then(t, r"thick|thickness")))
        _put(out, "center_bore_mm", _first(_after(t, r"cent(?:er|re)\s*bore"), _num_then(t, r"cent(?:er|re)\s*bore")))
        _common_pattern(t, out)
        if re.search(r"\b(vented|ventilated)\b", t):
            out["vented"] = True
        elif re.search(r"\bsolid\b", t):
            out["vented"] = False
        m = re.search(r"(\d+)\s*vanes?", t)
        if m:
            out["vane_count"] = int(m.group(1))

    elif part_type == "wheel_hub":
        _put(out, "flange_diameter_mm", _first(_after(t, r"flange"), _before(t, r"flange|hub"), dim.get("diameter")))
        _put(out, "bore_diameter_mm", _first(_after(t, r"bore"), _num_then(t, r"bore")))
        _put(out, "pilot_diameter_mm", _first(_after(t, r"pilot"), _num_then(t, r"pilot")))
        _common_pattern(t, out)
        if "lobed" in t:
            out["lobed"] = True

    elif part_type == "piston":
        _put(out, "diameter_mm", _first(_before(t, r"piston"), _num_then(t, r"bore"), dim.get("diameter")))
        _put(out, "height_mm", _first(_after(t, r"(?:overall\s*)?height"), _num_then(t, r"tall|high|height")))
        _put(out, "compression_height_mm", _after(t, r"compression\s*height"))
        _put(out, "pin_bore_diameter_mm", _first(_num_then(t, r"(?:wrist\s*|gudgeon\s*|piston\s*)?pin(?:\s*bore)?"),
                                                  _after(t, r"pin\s*bore")))
        m = re.search(r"(\d)\s*(?:ring|rings|ring grooves?)\b", t)
        if m and int(m.group(1)) <= 3:
            out["ring_groove_count"] = int(m.group(1))
        if re.search(r"\b(dish|dished|bowl)\b", t):
            out["crown_style"] = "dish"
        elif re.search(r"\bflat[\s-]*(?:top|crown)\b", t):
            out["crown_style"] = "flat"

    elif part_type == "flywheel":
        _put(out, "outer_diameter_mm", _first(_before(t, r"flywheel|fly wheel"), dim.get("diameter")))
        _put(out, "thickness_mm", _first(_after(t, r"thickness"), _num_then(t, r"thick|thickness")))
        m = re.search(r"(\d+)\s*(?:tooth|teeth)", t)
        if m:
            out["ring_gear_teeth"] = int(m.group(1))
        m = re.search(r"(\d+)\s*(?:crank\s*)?(?:bolts?|bolt holes)", t)
        if m and 3 <= int(m.group(1)) <= 12:
            out["crank_bolt_count"] = int(m.group(1))

    elif part_type == "control_arm":
        _put(out, "arm_length_mm", _first(_after(t, r"(?:arm\s*)?length"), _num_then(t, r"long|length")))
        _put(out, "thickness_mm", _first(_after(t, r"thickness"), _num_then(t, r"thick|thickness")))
        _put(out, "arm_width_mm", _first(_after(t, r"(?:arm\s*)?width"), _num_then(t, r"wide|width")))
        _put(out, "bushing_bore_diameter_mm", _first(_after(t, r"bush(?:ing)?\s*bore"), _num_then(t, r"bush(?:ing)?\s*bore")))
        _put(out, "ball_joint_hole_diameter_mm", _first(_after(t, r"ball\s*joint\s*(?:hole|bore)"),
                                                         _num_then(t, r"ball\s*joint\s*(?:hole|bore)")))
        if re.search(r"cross[\s-]*brace|closed\s*(?:a|arm)|braced", t):
            out["cross_brace"] = True

    elif part_type == "steering_knuckle":
        _put(out, "bearing_bore_diameter_mm", _first(_after(t, r"bearing\s*bore"), _num_then(t, r"bearing\s*bore")))
        _put(out, "seat_diameter_mm", _first(_after(t, r"seat"), _num_then(t, r"seat")))

    elif part_type == "ball_joint":
        _put(out, "stud_base_diameter_mm", _first(_after(t, r"stud"), _num_then(t, r"stud")))
        _put(out, "housing_diameter_mm", _first(_after(t, r"housing"), _num_then(t, r"housing")))

    elif part_type == "battery_bracket":
        _put(out, "base_length_mm", _first(_after(t, r"base\s*length"), _num_then(t, r"base\s*length"), _num_then(t, r"long|length")))
        _put(out, "width_mm", _first(_after(t, r"width"), _num_then(t, r"wide|width")))
        _put(out, "upright_height_mm", _first(_after(t, r"(?:upright\s*)?height"), _num_then(t, r"tall|high|height")))
        _put(out, "thickness_mm", _first(_after(t, r"thickness"), _num_then(t, r"thick|thickness")))
        d = _first(_after(t, r"hole\s*(?:diameter)?"), _num_then(t, r"holes?"))
        if d:
            out["base_hole_diameter_mm"] = d
            out["upright_hole_diameter_mm"] = d
        m = re.search(r"(\d)\s*gussets?", t)
        if m:
            out["gusset_count"] = min(4, int(m.group(1)))
        elif re.search(r"\bno\s+gussets?\b", t):
            out["gusset_count"] = 0
        if re.search(r"\bslot(?:ted|s)?\b", t):
            out["slotted"] = True

    return out
