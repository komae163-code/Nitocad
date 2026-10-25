"""Material records with provenance. Units: MPa, g/cc.

The built-in library holds TYPICAL room-temperature handbook values. They are
NOT design allowables (no A/B-basis statistical reduction). Any margin of
safety computed from a 'typical' basis is flagged and blocks release for
engineering review. Production use requires allowables from MMPDS, CMH-17 or
a controlled company/supplier specification, supplied via custom_material()
with a source citation.
"""
from dataclasses import dataclass
from typing import Optional

DESIGN_BASES = ("A", "B", "S", "user_certified")


@dataclass(frozen=True)
class Material:
    id: str
    name: str
    density_g_cc: float
    e_mpa: float
    nu: float
    ftu_mpa: float
    fty_mpa: float
    fsu_mpa: Optional[float]
    fbru_mpa: Optional[float]
    basis: str          # 'typical' | 'A' | 'B' | 'S' | 'user_certified'
    source: str
    galvanic_group: Optional[str] = None   # 'aluminium' | 'titanium' | 'carbon_steel' | 'stainless_steel' | 'cfrp' | ...

    @property
    def is_design_allowable(self) -> bool:
        return self.basis in DESIGN_BASES


_TYPICAL_SRC = "Typical room-temperature handbook values (not design allowables)"

LIBRARY = {
    m.id: m for m in (
        Material("6061-T6", "Aluminium 6061-T6", 2.70, 68_900, 0.33, 310, 276, 207, None, "typical", _TYPICAL_SRC, "aluminium"),
        Material("2024-T3", "Aluminium 2024-T3", 2.78, 73_100, 0.33, 483, 345, 283, None, "typical", _TYPICAL_SRC, "aluminium"),
        Material("7075-T6", "Aluminium 7075-T6", 2.81, 71_700, 0.33, 572, 503, 331, None, "typical", _TYPICAL_SRC, "aluminium"),
        Material("Ti-6Al-4V", "Titanium Ti-6Al-4V (annealed)", 4.43, 113_800, 0.342, 950, 880, 550, None, "typical", _TYPICAL_SRC, "titanium"),
    )
}


class MaterialError(ValueError):
    pass


def custom_material(d: dict) -> Material:
    """Build a material from user-supplied data. Source and basis are mandatory."""
    need = ("id", "density_g_cc", "e_mpa", "ftu_mpa", "fty_mpa", "basis", "source")
    missing = [k for k in need if d.get(k) in (None, "")]
    if missing:
        raise MaterialError(f"custom material missing: {', '.join(missing)}")
    if d["basis"] not in ("typical",) + DESIGN_BASES:
        raise MaterialError(f"basis must be one of typical, {', '.join(DESIGN_BASES)}")
    for k in ("density_g_cc", "e_mpa", "ftu_mpa", "fty_mpa"):
        if float(d[k]) <= 0:
            raise MaterialError(f"{k} must be positive")
    if float(d["fty_mpa"]) > float(d["ftu_mpa"]):
        raise MaterialError("fty_mpa cannot exceed ftu_mpa")
    opt = lambda k: float(d[k]) if d.get(k) is not None else None
    return Material(str(d["id"]), str(d.get("name", d["id"])), float(d["density_g_cc"]),
                    float(d["e_mpa"]), float(d.get("nu", 0.33)), float(d["ftu_mpa"]),
                    float(d["fty_mpa"]), opt("fsu_mpa"), opt("fbru_mpa"),
                    d["basis"], str(d["source"]), d.get("galvanic_group"))


def resolve_material(ref: dict) -> Material:
    """ref is {'id': '7075-T6'} for the library, or a full custom record."""
    if not isinstance(ref, dict) or not ref.get("id"):
        raise MaterialError("material must be an object with an id")
    if len(ref) == 1:
        try:
            return LIBRARY[ref["id"]]
        except KeyError:
            raise MaterialError(f"unknown library material {ref['id']!r}; supply a full custom record")
    return custom_material(ref)
