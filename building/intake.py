"""
Intake: turn a description (and optionally images) into a building SPEC proposal.

Two routes, same output: a deterministic rule parser that works offline, and an LLM route (any provider,
including vision) injected as a callable. Either way the result is a PROPOSAL: every inferred item is listed
in `assumptions`, open points in `questions`, and `needs_confirmation` stays true. Extraction is not
confirmation; nothing here becomes a requirement until the user confirms it.

The footprint is chosen by search over the real layout engine, so a suggested size is one that actually fits.
Dimensions read from an image are never trusted: they are returned as a question, not as a fact.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from .layout import design_building
from .spec import ROOMS, normalize_building_spec

WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"


def _n(tok: str) -> int:
    return int(tok) if tok.isdigit() else WORDS[tok]


def _count(text: str, noun: str) -> int | None:
    m = re.search(rf"\b{_NUM}[\s-]*(?:{noun})\b", text)
    return _n(m.group(1)) if m else None


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text) is not None


def spec_from_counts(c: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Distribute a room program over floors by a standard pattern (living areas below, bedrooms above).

    `c` keys: levels, bedrooms, master (bool), bathrooms, toilets (None = not stated), dining, study, store,
    kitchen, living (bools). Returns (levels list, assumptions). Shared by the text parser and the Studio's
    flat building parameters, so both describe the same building the same way."""
    assumptions: list[str] = []
    levels, beds, master = c["levels"], c["bedrooms"], c["master"]
    baths, toilets = c["bathrooms"], c.get("toilets")

    def bed_rooms(n: int, first_master: bool) -> list[dict[str, Any]]:
        return [{"type": "master_bedroom"} if (i == 0 and first_master) else {"type": "bedroom"} for i in range(n)]

    ground: list[dict[str, Any]] = [{"type": "living"}]
    if c.get("dining"):
        ground.append({"type": "dining"})
    ground.append({"type": "kitchen"})
    if c.get("study"):
        ground.append({"type": "study"})
    if c.get("store"):
        ground.append({"type": "store"})
    if levels == 1:
        ground += bed_rooms(beds, master)
        ground += [{"type": "bathroom"} for _ in range(baths)]
        ground += [{"type": "toilet"} for _ in range(toilets or 0)]
        return [{"rooms": ground}], assumptions
    ground.append({"type": "toilet"})
    if toilets and toilets > 1:
        ground += [{"type": "toilet"} for _ in range(toilets - 1)]
    elif not toilets:
        assumptions.append("A guest toilet was added on the ground floor")
    upper_n = levels - 1
    rooms_up: list[list[dict[str, Any]]] = [[] for _ in range(upper_n)]
    beds_list = bed_rooms(beds, master or beds >= 2)
    if not master and beds >= 2:
        assumptions.append("The first upstairs bedroom is a master bedroom")
    for i, b in enumerate(beds_list):
        rooms_up[i % upper_n].append(b)
    for i in range(baths):
        rooms_up[i % upper_n].append({"type": "bathroom"})
    for lvl in rooms_up:
        if not any(r["type"] == "bathroom" for r in lvl) and lvl:
            lvl.append({"type": "bathroom"})
            assumptions.append("A bathroom was added to an upper floor that had none")
    assumptions.append("Rooms were distributed over the floors by a standard pattern (living areas below, bedrooms above)")
    return [{"rooms": ground}] + [{"rooms": r} for r in rooms_up if r], assumptions


def parse_description(text: str) -> dict[str, Any]:
    """Deterministic parser. Returns {"spec", "counts", "assumptions", "questions"}; the spec has no dimensions unless stated."""
    t = text.lower().replace("en-suite", "ensuite")
    assumptions: list[str] = []
    questions: list[str] = []

    levels = _count(t, r"stor(?:e)?y|storeys|stories|floors?|levels?")
    if levels is None:
        if _has(t, r"\bduplex\b"):
            levels = 2
        elif _has(t, r"bungalow|single[- ]stor|single floor|one[- ]stor"):
            levels = 1
    if levels is None:
        levels = 1
        assumptions.append("One storey (the number of floors was not stated)")
        questions.append("How many floors should the building have?")
    levels = max(1, min(levels, 4))

    beds = _count(t, r"bed(?:room)?s?")
    master = _has(t, r"master|ensuite")
    if beds is None and _has(t, r"\bbedroom\b"):
        beds = 1
    if beds is None:
        beds = 3 if master else 2
        assumptions.append(f"{beds} bedrooms (the number was not stated)")
        questions.append("How many bedrooms do you need?")
    baths = _count(t, r"bath(?:room)?s?|shower rooms?")
    if baths is None:
        baths = max(1, (beds + 1) // 2)
        assumptions.append(f"{baths} bathroom(s) (the number was not stated)")
    toilets = _count(t, r"toilets?|wc|restrooms?|powder rooms?")
    counts = {"levels": levels, "bedrooms": beds, "master": master, "bathrooms": baths, "toilets": toilets,
              "dining": _has(t, r"dining"), "study": _has(t, r"\bstudy\b|home office|\boffice\b"),
              "store": _has(t, r"\bstore(?:room)?\b|storage"), "kitchen": True, "living": True}
    if not _has(t, r"kitchen"):
        assumptions.append("A kitchen was added (not mentioned)")
    if not _has(t, r"living|sitting room|parlou?r|lounge"):
        assumptions.append("A living room was added (not mentioned)")

    spec_levels, more = spec_from_counts(counts)
    assumptions += more
    if levels > 1:
        questions.append("Do you want a bedroom on the ground floor? (none is assumed)")

    spec: dict[str, Any] = {"name": "Building", "levels": spec_levels}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(m|meters|metres|ft|feet|')?\s*(?:x|by|×|\*)\s*(\d+(?:\.\d+)?)\s*(m|meters|metres|ft|feet|')?", t)
    if m:
        a, b = float(m.group(1)), float(m.group(3))
        unit = (m.group(2) or m.group(4) or "m")
        k = 0.3048 if unit in ("ft", "feet", "'") else 1.0
        a, b = a * k, b * k
        if 5 <= min(a, b) and max(a, b) <= 40:
            spec["width_mm"], spec["depth_mm"] = max(a, b) * 1000, min(a, b) * 1000
        else:
            questions.append(f"Is {m.group(0)} the plot or the building footprint? (it does not look like a footprint)")
    return {"spec": spec, "counts": counts, "assumptions": assumptions, "questions": questions}


MAX_FOOTPRINT_WIDTH_MM = 26000
MAX_FOOTPRINT_RATIO = 2.2


def search_footprint(spec: dict[str, Any]) -> dict[str, Any]:
    """Pick the smallest footprint (width >= depth) whose layout builds, treating room areas as minimums.

    Rooms that come out larger than requested are fine: several floors share one footprint and the
    floor with the most rooms sets its size, so the others get generous rooms. That is only a note.
    Preference, in order: every room at or above its requested area, a compact shape (width at most
    2.2 times depth), then the smallest area. A plan with some rooms under their requested area is used
    only when nothing better builds. Returns {"footprint": (w, d) | None, "under": [warnings],
    "blockers": [errors]}, where "blockers" is only filled when nothing builds at all and names what is
    stopping it at the largest size tried.
    """
    multi = len(spec["levels"]) > 1
    depths = (9000, 9500, 10000, 10500, 11000, 11500, 12000) if multi else (8000, 8500, 9000, 9500, 10000, 11000)
    found: list[tuple[int, int, float, list[str], float]] = []   # (under count, not compact, area, under warnings, w, d)
    for d in depths:
        first_any = None
        for w in range(max(d, 9000), MAX_FOOTPRINT_WIDTH_MM + 1, 500):
            r = design_building({**spec, "width_mm": float(w), "depth_mm": float(d)})
            if not r["ok"]:
                continue
            under = [x for x in r["warnings"] if "under the requested" in x]
            cand = (1 if under else 0, 0 if w / d <= MAX_FOOTPRINT_RATIO else 1, float(w * d), under, float(w), float(d))
            if first_any is None:
                first_any = cand
            if not under:
                first_any = cand        # widening only makes rooms bigger, so this is the smallest that meets every area
                break
        if first_any is not None:
            found.append(first_any)
    if found:
        best = min(found, key=lambda c: (c[0], c[1], c[2]))
        return {"footprint": (best[4], best[5]), "under": best[3], "blockers": [], "limits": (MAX_FOOTPRINT_WIDTH_MM, depths[-1])}
    biggest = design_building({**spec, "width_mm": float(MAX_FOOTPRINT_WIDTH_MM), "depth_mm": float(depths[-1])})
    return {"footprint": None, "under": [], "blockers": list(biggest["errors"])[:3], "limits": (MAX_FOOTPRINT_WIDTH_MM, depths[-1])}


def suggest_footprint(spec: dict[str, Any]) -> tuple[float, float] | None:
    """Smallest footprint the layout engine accepts for this room program (see search_footprint)."""
    return search_footprint(spec)["footprint"]


def no_footprint_message(found: dict[str, Any], advice: str) -> str:
    """Say what actually blocks the layout instead of just that nothing fits."""
    width, depth = found.get("limits", (MAX_FOOTPRINT_WIDTH_MM, 12000))
    notes: list[str] = []
    for b in found.get("blockers") or []:
        b = b.replace(" Remove a room or enlarge the footprint.", "").rstrip(" .")
        if b and b not in notes:
            notes.append(b + ".")
    why = " ".join(notes) if notes else "The layout engine rejected every size."
    return f"No footprint up to {width / 1000:g} x {depth / 1000:g} m works for this room program. {why} {advice}"


INTAKE_SYSTEM = """You turn a building brief (text and/or drawings) into a JSON building spec. Return ONLY JSON:
{"name": str, "width_mm": number?, "depth_mm": number?, "levels": [{"rooms": [{"type": str, "name": str?, "count": int?, "area_m2": number?}]}],
 "roof": {"type": "gable"|"flat", "pitch_deg": number?}?}
Rules:
- levels are listed ground floor first. Room types: living, dining, kitchen, study, bedroom, master_bedroom, bathroom, toilet, store.
- Use ONLY information in the brief or drawing. Do NOT invent dimensions: omit width_mm/depth_mm unless a measurement is written
  or stated. Never estimate scale from a picture. Omit area_m2 unless stated.
- width_mm is the longer side of the building footprint, depth_mm the shorter (millimetres).
- If a drawing shows rooms, list them with their labels as names. Put unclear items in "notes" (a list of strings) instead of guessing."""


def extract_with_llm(text: str, call_llm: Callable[..., dict], images: list[Any] | None = None,
                     max_attempts: int = 2) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Returns (raw_spec, errors, notes). The callable takes (system, user, images) and returns a dict."""
    user, errors = text, []
    for _ in range(max_attempts):
        try:
            data = call_llm(INTAKE_SYSTEM, user, images)
        except Exception as exc:  # noqa: BLE001 - network, provider, bad JSON
            errors = [f"The language model call failed: {exc}"]
            continue
        if not isinstance(data, dict):
            errors = ["The model did not return a JSON object."]
            continue
        notes = [str(x) for x in data.pop("notes", [])] if isinstance(data.get("notes"), list) else []
        probe = {**data}
        probe.setdefault("width_mm", 13000.0)
        probe.setdefault("depth_mm", 10000.0)
        _, errors, _ = normalize_building_spec(probe)
        errors = [e for e in errors if "swap width" not in e or ("width_mm" in data and "depth_mm" in data)]
        if not errors:
            return data, [], notes
        user = text + "\n\nThe previous JSON was rejected. Fix exactly these problems and return the full JSON again:\n- " + "\n- ".join(errors)
    return None, errors, []


def intake(text: str, call_llm: Callable[..., dict] | None = None, images: list[Any] | None = None) -> dict[str, Any]:
    """Description (+ optional images) -> a confirmed-able building proposal with the layout already checked."""
    source = "rules"
    assumptions: list[str] = []
    questions: list[str] = []
    notes: list[str] = []
    raw: dict[str, Any] | None = None
    if call_llm is not None:
        raw, errs, notes = extract_with_llm(text, call_llm, images)
        if raw is not None:
            source = "image" if images else "llm"
        else:
            assumptions.append("The language model route failed (" + "; ".join(errs[:2]) + "); the built-in rule parser was used")
    elif images:
        questions.append("Images were provided but no vision model is configured, so they were ignored.")
    if raw is None:
        parsed = parse_description(text)
        raw, assumptions, questions = parsed["spec"], assumptions + parsed["assumptions"], questions + parsed["questions"]
    if source == "image":
        questions.append("Dimensions read from a drawing are unreliable. Please confirm the footprint size.")
        for k in ("width_mm", "depth_mm"):
            if k in raw:
                questions.append(f"Confirm {k.replace('_mm', '')} = {raw[k]:g} mm (taken from the drawing).")
    if "width_mm" not in raw or "depth_mm" not in raw:
        found = search_footprint(raw)
        fp = found["footprint"]
        if fp is None:
            questions.append(no_footprint_message(found, "Reduce the rooms or give a footprint."))
        else:
            raw["width_mm"], raw["depth_mm"] = fp
            assumptions.append(f"Footprint {fp[0] / 1000:g} m x {fp[1] / 1000:g} m chosen as the smallest that fits this program")
            assumptions.extend(found["under"])
            questions.append("Confirm or give your own footprint (width x depth).")
    design = design_building(raw) if "width_mm" in raw and "depth_mm" in raw else {"ok": False, "errors": ["No footprint."], "warnings": [], "assumptions": []}
    suggested = None
    if not design["ok"] and "width_mm" in raw and "depth_mm" in raw and not any("chosen as the smallest" in a for a in assumptions):
        fp = suggest_footprint({k: v for k, v in raw.items() if k not in ("width_mm", "depth_mm")})
        if fp:
            suggested = {"width_mm": fp[0], "depth_mm": fp[1]}
            questions.append(f"The footprint {raw['width_mm'] / 1000:g} x {raw['depth_mm'] / 1000:g} m does not fit this program. "
                             f"The smallest that does is {fp[0] / 1000:g} x {fp[1] / 1000:g} m. Use that, or reduce the rooms?")
    return {"spec": raw, "source": source, "needs_confirmation": True, "confirmed": False, "suggested_footprint": suggested,
            "assumptions": assumptions + design.get("assumptions", []), "questions": questions, "notes": notes,
            "design_ok": design["ok"], "errors": design["errors"], "warnings": design["warnings"]}


def confirm(proposal: dict[str, Any], overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply the user's answers and mark the spec confirmed ONLY if the layout still works."""
    spec = {**proposal["spec"], **(overrides or {})}
    design = design_building(spec)
    return {**proposal, "spec": spec, "design_ok": design["ok"], "errors": design["errors"], "warnings": design["warnings"],
            "confirmed": bool(design["ok"]), "needs_confirmation": not design["ok"]}
