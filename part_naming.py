"""
Part names people can read. The reference model's tree says `side_glazing_1_left` and `dark_glass_greenhouse`; ours says
`wheel_fl_tyre` and `panels_door_rr`. The ids themselves stay as they are (explode directions, checks and exports key on
them); this module does two jobs around them:

  readable(name)  the label to show people: "Front left door", "Left front wheel bearing", "Engine piston 3"
  audit(names)    what is hard to read in a set of ids, so new vehicles get fixed at the source

Naming rule, also given to the planner: full words, side as `_left` / `_right` (never `_l` / `_r`), corners spelled
`front_left` (never `fl`), a number before the side (`side_glazing_1_left`), no module prefix repeated on every part.
"""
from __future__ import annotations

import re
from collections import Counter

_CORNER = {"fl": ("front", "left"), "fr": ("front", "right"), "rl": ("rear", "left"), "rr": ("rear", "right")}
_SIDE = {"l": "left", "r": "right", "left": "left", "right": "right"}
_SMALL = {"fl", "fr", "rl", "rr", "l", "r"}
# Prefixes that say nothing about the part ("panels_roof"). Meaningful ones (engine_, wheel_, gear_) are kept.
GENERIC_PREFIXES = {"panels", "parts", "group", "module", "assembly", "misc", "items", "component", "comp"}
# Dropped from labels only (exterior_roof reads "Roof"). They are fine as ids, so the audit does not flag them.
LABEL_PREFIXES = GENERIC_PREFIXES | {"exterior"}
RULE = ("Name parts in full words: side as _left or _right (never _l or _r), corners as front_left (never fl, fr, rl, rr), "
        "a number before the side (side_glazing_1_left), and no prefix repeated on every part of a group.")


def _tokens(name: str) -> list[str]:
    return [t for t in str(name).lower().split("_") if t]


def readable(name: str) -> str:
    """'exterior_door_front_left' -> 'Front left door'; 'half_shaft_left' -> 'Left half shaft'; 'wheel_fl_tyre' ->
    'Front left wheel tyre'; 'engine_piston_3' -> 'Engine piston 3'. The corner or side moves to the front, a generic
    prefix is dropped, and the rest of the words keep their order."""
    toks = _tokens(name)
    if toks[:1] and toks[0] in LABEL_PREFIXES and len(toks) > 1:
        toks = toks[1:]
    lead: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if not lead and t in _CORNER:
            lead = list(_CORNER[t])
        elif not lead and t in ("front", "rear") and i + 1 < len(toks) and toks[i + 1] in _SIDE:
            lead = [t, _SIDE[toks[i + 1]]]
            i += 1
        elif not lead and t in _SIDE:
            lead = [_SIDE[t]]
        else:
            rest.append(t)
        i += 1
    words = lead + rest
    return " ".join(words).capitalize() if words else str(name)


def audit(names: list[str]) -> list[dict[str, str]]:
    """Issues worth fixing in a set of part ids. Each is {name, issue, suggestion}; [] means they read well."""
    out: list[dict[str, str]] = []
    for n in names:
        toks = _tokens(n)
        if any(t in _CORNER for t in toks):
            out.append({"name": n, "issue": "corner abbreviation (fl, fr, rl, rr)", "suggestion": _suggest(toks)})
        elif any(t in ("l", "r") for t in toks):
            out.append({"name": n, "issue": "side abbreviation (_l, _r)", "suggestion": _suggest(toks)})
        elif len(toks) > 1 and toks[0] in GENERIC_PREFIXES:
            out.append({"name": n, "issue": f"empty prefix '{toks[0]}_'", "suggestion": "_".join(toks[1:])})
    return out


def _suggest(toks: list[str]) -> str:
    """The id spelled out: wheel_fl_tyre -> wheel_front_left_tyre, half_shaft_l -> half_shaft_left."""
    out: list[str] = []
    for t in toks:
        if t in _CORNER:
            out += list(_CORNER[t])
        elif t in ("l", "r"):
            out.append(_SIDE[t])
        else:
            out.append(t)
    return "_".join(out)


def audit_report(vehicle: str, names: list[str]) -> str:
    issues = audit(names)
    if not issues:
        return f"{vehicle}: {len(names)} parts, names read well."
    kinds = Counter(i["issue"].split(" (")[0].split(" '")[0] for i in issues)
    lines = [f"{vehicle}: {len(names)} parts, {len(issues)} names to improve ({', '.join(f'{c} {k}' for k, c in kinds.most_common())})"]
    lines += [f"  {i['name']}  ->  {i['suggestion']}   [{i['issue']}]" for i in issues[:12]]
    if len(issues) > 12:
        lines.append(f"  ... and {len(issues) - 12} more")
    return "\n".join(lines)


if __name__ == "__main__":      # python part_naming.py  -> audit every example vehicle
    import json
    import sys
    from pathlib import Path

    import vehicle_spec as VS
    for f in sorted((Path(__file__).parent / "vehicles" / "examples").glob("*.json")):
        ex = VS.expand_vehicle(json.loads(f.read_text()), None, None, None)
        if ex["ok"]:
            print(audit_report(f.stem, [p["name"] for p in ex["parts"]]) + "\n")
    sys.exit(0)
