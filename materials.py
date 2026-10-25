"""
Engineering material data for screening checks and mass.

TYPICAL values for the common condition of each material (minimum or typical handbook figures,
not a specific supplier's certificate). Anything built on them is a screening estimate: always
confirm against the datasheet of the actual stock. A name that is only a family ("aluminum",
"steel") resolves to a stated default grade and is flagged `generic` so a result can say
"assumed 6061-T6".
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Material:
    key: str
    category: str                    # metal | polymer | wood
    density_g_cm3: float
    e_gpa: float | None              # Young's modulus
    yield_mpa: float | None          # 0.2% proof / yield, typical for the stated condition
    uts_mpa: float | None
    condition: str = ""
    generic: bool = False            # True when matched on a family name, not a grade
    note: str = ""


_M = lambda *a, **k: Material(*a, **k)  # noqa: E731

MATERIALS: dict[str, Material] = {m.key: m for m in [
    _M("6061-T6", "metal", 2.70, 68.9, 276, 310, "T6 temper"),
    _M("7075-T6", "metal", 2.81, 71.7, 503, 572, "T6 temper"),
    _M("S235 steel", "metal", 7.85, 210, 235, 360, "hot rolled structural"),
    _M("C45 steel", "metal", 7.85, 210, 340, 630, "normalised"),
    _M("4140 steel", "metal", 7.85, 205, 415, 655, "annealed"),
    _M("304 stainless", "metal", 8.00, 193, 215, 505, "annealed"),
    _M("316 stainless", "metal", 8.00, 193, 205, 515, "annealed"),
    _M("cast iron", "metal", 7.20, 100, None, 200, "grey, class 200", note="brittle: no meaningful yield"),
    _M("Ti-6Al-4V", "metal", 4.43, 114, 880, 950, "annealed"),
    _M("brass", "metal", 8.50, 100, None, None, "", note="strength varies widely with temper"),
    _M("copper", "metal", 8.96, 117, None, None, "", note="strength varies widely with temper"),
    _M("PLA", "polymer", 1.24, 3.5, 50, 55, "FDM, along the layers", note="weaker across layers"),
    _M("ABS", "polymer", 1.04, 2.2, 40, 42, "FDM, along the layers", note="weaker across layers"),
    _M("PETG", "polymer", 1.27, 2.1, 50, 53, "FDM, along the layers", note="weaker across layers"),
    _M("nylon", "polymer", 1.15, 2.7, 70, 75, "PA6 dry, injection moulded", note="absorbs moisture"),
    _M("POM", "polymer", 1.41, 2.9, 65, 70, "acetal / Delrin"),
    _M("polycarbonate", "polymer", 1.20, 2.4, 62, 65, ""),
    _M("acrylic", "polymer", 1.18, 3.0, None, 70, "cast", note="brittle"),
    _M("oak", "wood", 0.75, 11.0, None, None, "", note="strength depends on grain direction"),
    _M("pine", "wood", 0.50, 9.0, None, None, "", note="strength depends on grain direction"),
]}

# alias (lower case) -> (material key, generic?). Longest alias wins.
_ALIASES: dict[str, tuple[str, bool]] = {
    "6061": ("6061-T6", False), "7075": ("7075-T6", False), "aluminum": ("6061-T6", True), "aluminium": ("6061-T6", True),
    "s235": ("S235 steel", False), "mild steel": ("S235 steel", False), "structural steel": ("S235 steel", False),
    "c45": ("C45 steel", False), "1045": ("C45 steel", False), "4140": ("4140 steel", False), "steel": ("S235 steel", True),
    "316": ("316 stainless", False), "304": ("304 stainless", False), "stainless": ("304 stainless", True),
    "cast iron": ("cast iron", False), "iron": ("cast iron", True), "ti-6al-4v": ("Ti-6Al-4V", False), "titanium": ("Ti-6Al-4V", True),
    "brass": ("brass", False), "copper": ("copper", False), "pla": ("PLA", False), "abs": ("ABS", False), "petg": ("PETG", False),
    "nylon": ("nylon", False), "pa6": ("nylon", False), "pom": ("POM", False), "delrin": ("POM", False), "acetal": ("POM", False),
    "polycarbonate": ("polycarbonate", False), "acrylic": ("acrylic", False), "oak": ("oak", False), "pine": ("pine", False),
}


def lookup(text: str | None) -> Material | None:
    """Best match for a material description, or None. Generic family names return the default
    grade with generic=True."""
    if not text:
        return None
    low = text.lower()
    hits = [(alias, *_ALIASES[alias]) for alias in _ALIASES if alias in low]
    if not hits:
        return None
    # a specific grade beats a family name ("6061 aluminium" is 6061, not generic aluminium);
    # among equals the longer alias wins ("304 stainless" is 304, not plain "steel")
    alias, key, generic = sorted(hits, key=lambda h: (h[2], -len(h[0])))[0]
    mat = MATERIALS[key]
    return Material(**{**mat.__dict__, "generic": True}) if generic else mat


def density_g_cm3(text: str | None) -> float | None:
    m = lookup(text)
    return m.density_g_cm3 if m else None
