"""
Candidate designs: for a bracket the person sees a thin, a standard and a heavy version and picks one.

This module is pure. It turns one built spec into up to three specs that differ in plate thickness and
nothing else the person asked for (sizes, holes, material stay as they are). Building them and showing
them is chat_service's job.

Supported parts: motor_mount and l_bracket (cad_templates/motor_mount.py, l_bracket.py).

Every candidate is held to the limits validator.py enforces, so what is offered is what gets built:
  * l_bracket: a hole must be narrower than the plate (validator drops it to thickness - 0.5 otherwise),
    and the plate may not be thicker than a third of the smallest side.
  * both: the edge fillet must stay under half the thickness, so it is scaled down with a thin plate.
If a limit leaves no room for a distinct thin or heavy version, that one is left out and the person is
told why, instead of offering two versions that are really the same.
"""
from __future__ import annotations

import copy
import re
from typing import Any

SUPPORTED = ("motor_mount", "l_bracket")

THIN_FACTOR = 0.6
HEAVY_FACTOR = 1.6
MIN_STEP_MM = 0.5          # a candidate must differ from the standard by at least this much
MIN_THICKNESS_MM = 1.5     # below this a plate is too flimsy to offer
MAX_MOTOR_THICKNESS_MM = 12.0

_DEFAULT_THICKNESS = 3.0
_L_BRACKET_DEFAULTS = {"width_mm": 50.0, "height_mm": 60.0, "depth_mm": 40.0, "hole_diameter_mm": 3.2}

_ORDER = ("thin", "standard", "heavy")
_LABEL = {"thin": "Thin", "standard": "Standard", "heavy": "Heavy"}

# If the person already said how thick or how heavy, there is nothing to offer.
_STATED_THICKNESS = re.compile(
    r"\b(thick(?:ness|er|est)?|thin(?:ner|nest)?|heavy|heavier|sturdy|stiff|lightweight|light[- ]weight|"
    r"gauge|\d+(?:\.\d+)?\s*mm\s+(?:plate|sheet|stock))\b",
    re.IGNORECASE,
)


class CandidateError(ValueError):
    """These parameters cannot be turned into candidates. The message is safe to show."""


def applies(spec: dict | None) -> bool:
    return bool(spec) and spec.get("part_type") in SUPPORTED


def user_stated_thickness(text: str) -> bool:
    """True when the message already says how thick or how heavy the part should be."""
    return bool(_STATED_THICKNESS.search(text or ""))


def _num(params: dict, key: str, default: float) -> float:
    try:
        v = float(params.get(key, default))
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _round(v: float) -> float:
    """Half-millimetre steps: a thickness someone would actually order."""
    return round(v * 2) / 2


def _limits(part_type: str, params: dict) -> tuple[float, float]:
    """(lowest, highest) plate thickness the validator and the template accept, in mm."""
    if part_type == "l_bracket":
        hole = _num(params, "hole_diameter_mm", _L_BRACKET_DEFAULTS["hole_diameter_mm"])
        sides = [_num(params, k, _L_BRACKET_DEFAULTS[k]) for k in ("width_mm", "height_mm", "depth_mm")]
        low = max(MIN_THICKNESS_MM, hole + 0.5)           # hole must be narrower than the plate
        high = min(sides) / 3.0                           # validator: thickness <= smallest side / 3
        return low, high
    return MIN_THICKNESS_MM, MAX_MOTOR_THICKNESS_MM


def _with_thickness(spec: dict, thickness: float) -> dict:
    out = copy.deepcopy(spec)
    params = out.setdefault("parameters", {})
    params["thickness_mm"] = thickness
    fillet = params.get("fillet_radius_mm")
    if isinstance(fillet, (int, float)) and fillet > 0:
        # the fillet must stay under half the thickness or the validator rewrites it
        params["fillet_radius_mm"] = round(min(float(fillet), max(0.0, thickness / 2 - 0.2)), 2)
    return out


def variants(spec: dict) -> dict[str, Any]:
    """
    Thin, standard and heavy versions of a bracket spec.

    Returns {"candidates": [{"id", "label", "thickness_mm", "spec"} ...], "skipped": {id: reason}}.
    The standard version is the spec as given (its thickness is written in explicitly). Candidates come
    back in thin, standard, heavy order. Raises CandidateError for a part type with no candidates.
    """
    if not applies(spec):
        raise CandidateError("Candidate designs are available for motor mounts and L brackets.")
    ptype = spec["part_type"]
    params = spec.get("parameters") or {}
    base = _num(params, "thickness_mm", _DEFAULT_THICKNESS)
    low, high = _limits(ptype, params)

    standard = _with_thickness(spec, base)
    found: dict[str, dict] = {"standard": {"id": "standard", "label": _LABEL["standard"], "thickness_mm": base, "spec": standard}}
    skipped: dict[str, str] = {}

    thin_t = _round(max(low, base * THIN_FACTOR))
    if base - thin_t >= MIN_STEP_MM:
        found["thin"] = {"id": "thin", "label": _LABEL["thin"], "thickness_mm": thin_t, "spec": _with_thickness(spec, thin_t)}
    else:
        skipped["thin"] = (f"A thinner plate than {base:g} mm would not fit these holes" if ptype == "l_bracket"
                           else f"A plate thinner than {base:g} mm is too flimsy to offer")

    heavy_t = _round(min(high, base * HEAVY_FACTOR))
    if heavy_t - base >= MIN_STEP_MM:
        found["heavy"] = {"id": "heavy", "label": _LABEL["heavy"], "thickness_mm": heavy_t, "spec": _with_thickness(spec, heavy_t)}
    else:
        skipped["heavy"] = f"A plate thicker than {base:g} mm would be out of proportion for this size"

    return {"candidates": [found[i] for i in _ORDER if i in found], "skipped": skipped}


def summarize(candidate: dict, result: dict | None, facts: dict | None) -> dict:
    """
    What the person compares: thickness, volume, and mass when a material is known.
    `facts` is requirements.facts_from_result for this candidate; `result` is the generator's result,
    used only when the facts carry no volume. Missing numbers come back as None, never as guesses.
    """
    facts = facts or {}
    volume = facts.get("volume_mm3")
    if volume is None:
        volume = ((((result or {}).get("verification") or {}).get("measured")) or {}).get("volume")
    return {
        "thickness_mm": candidate["thickness_mm"],
        "volume_cm3": round(volume / 1000.0, 2) if isinstance(volume, (int, float)) and volume > 0 else None,
        "mass_g": facts.get("mass_g"),
        "size_mm": facts.get("size_mm"),
    }


def common_metric(entries: list[dict]) -> str | None:
    """The one number every candidate has, so the cards compare like with like: mass, else volume."""
    for key in ("mass_g", "volume_cm3"):
        if entries and all(e["summary"].get(key) is not None for e in entries):
            return key
    return None


def describe(candidate: dict, summary: dict, metric: str | None) -> str:
    """One plain line for the card, for example 'Thin: 2 mm plate, 41 g'."""
    bits = [f"{candidate['thickness_mm']:g} mm plate"]
    if metric == "mass_g":
        bits.append(f"{summary['mass_g']:g} g")
    elif metric == "volume_cm3":
        bits.append(f"{summary['volume_cm3']:g} cm3")
    return f"{candidate['label']}: " + ", ".join(bits)


def ordering_is_sane(entries: list[dict]) -> bool:
    """Thin lighter than standard, standard lighter than heavy. True when there is nothing to compare."""
    key = common_metric(entries)
    if key is None:
        return True
    vals = [e["summary"][key] for e in entries]
    return all(a < b for a, b in zip(vals, vals[1:]))
