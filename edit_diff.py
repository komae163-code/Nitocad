"""
What an edit changed, measured on the finished build.

The spec diff says what was asked for. A change to one value can also change the size, the mass,
whether two parts overlap or whether a requirement still passes, and nobody asked for those. This
module compares the facts of the version before the edit with the facts of the version after it
and returns the difference as rows the Studio shows under the reply.

    diff = build(spec_changes, old_facts, new_facts, old_requirements, new_requirements, 3, 4)
    warning_sentence(diff)     " Check this: ..." when something got worse, else ""

`facts` is what requirements.facts_from_result stores with each part. `requirements` is what
requirements.evaluate returns. Pure Python, nothing here touches CAD.
"""
from __future__ import annotations

from typing import Any

MAX_ROWS = 12
_WORSE, _BETTER = "worse", "better"


def _g(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _size(size: Any) -> str | None:
    if isinstance(size, (list, tuple)) and len(size) == 3:
        return " x ".join(_g(round(float(x), 2)) for x in size) + " mm"
    return None


def _asm(facts: dict | None) -> dict:
    return (facts or {}).get("assembly") or {}


def _pairs(asm: dict) -> dict[tuple[str, str], dict]:
    out = {}
    for i in asm.get("interferences") or []:
        if isinstance(i, dict) and i.get("a") is not None and i.get("b") is not None:
            out[tuple(sorted((str(i["a"]), str(i["b"]))))] = i
    return out


def _count(v: Any) -> int | None:
    return len(v) if isinstance(v, (list, tuple)) else None


def _row(label: str, before: Any, after: Any, direction: str | None = None) -> dict:
    return {"label": label, "before": before, "after": after, "direction": direction}


def fact_changes(old: dict | None, new: dict | None) -> list[dict]:
    """Rows for what is different between two versions' measured facts."""
    if not old or not new:
        return []
    rows: list[dict] = []

    a, b = _size(old.get("size_mm")), _size(new.get("size_mm"))
    if a and b and a != b:
        rows.append(_row("Overall size", a, b))

    ma, mb = old.get("mass_g"), new.get("mass_g")
    if isinstance(ma, (int, float)) and isinstance(mb, (int, float)) and abs(ma - mb) > 0.005:
        rows.append(_row("Mass", f"{_g(round(ma, 2))} g", f"{_g(round(mb, 2))} g"))

    wa, wb = old.get("min_wall_mm"), new.get("min_wall_mm")
    if isinstance(wa, (int, float)) and isinstance(wb, (int, float)) and abs(wa - wb) > 0.005:
        rows.append(_row("Thinnest wall", f"{_g(round(wa, 2))} mm", f"{_g(round(wb, 2))} mm", _WORSE if wb < wa else None))

    if old.get("material") and new.get("material") and old["material"] != new["material"]:
        rows.append(_row("Material", old["material"], new["material"]))

    oa, na = _asm(old), _asm(new)
    if oa or na:
        pa, pb = ((oa.get("bom") or {}).get("part_count")), ((na.get("bom") or {}).get("part_count"))
        if isinstance(pa, int) and isinstance(pb, int) and pa != pb:
            rows.append(_row("Parts", pa, pb))
        io, in_ = _pairs(oa), _pairs(na)
        for key in sorted(set(in_) - set(io))[:5]:
            i = in_[key]
            rows.append(_row("New overlap", "none", f"{key[0]} and {key[1]}, {_g(i.get('volume_mm3'))} mm3 ({i.get('severity', 'minor')})", _WORSE))
        for key in sorted(set(io) - set(in_))[:5]:
            rows.append(_row("Overlap cleared", f"{key[0]} and {key[1]}", "none", _BETTER))
        for label, k in (("Separate groups", "disconnected_groups"), ("Pairs not checked", "unchecked_pairs")):
            ca, cb = _count(oa.get(k)), _count(na.get(k))
            if ca is not None and cb is not None and ca != cb:
                rows.append(_row(label, ca, cb, _WORSE if cb > ca else _BETTER))
    return rows[:MAX_ROWS]


def requirement_changes(old: list[dict] | None, new: list[dict] | None) -> list[dict]:
    """Rows for requirements whose status moved between pass, fail and unknown."""
    before = {r.get("id"): r for r in (old or []) if r.get("id") is not None}
    rows: list[dict] = []
    for r in new or []:
        o = before.get(r.get("id"))
        if not o or o.get("status") == r.get("status"):
            continue
        after = r.get("status")
        direction = _WORSE if after == "fail" else (_BETTER if o.get("status") == "fail" and after == "pass" else None)
        rows.append({"text": r.get("text", ""), "before": o.get("status"), "after": after, "direction": direction})
    return rows[:MAX_ROWS]


def build(spec_changes: list[str] | None, old_facts: dict | None, new_facts: dict | None,
          old_requirements: list[dict] | None = None, new_requirements: list[dict] | None = None,
          from_version: int | None = None, to_version: int | None = None) -> dict | None:
    """The whole diff for one edit, or None when nothing changed."""
    spec = [str(c) for c in (spec_changes or [])][:MAX_ROWS]
    facts = fact_changes(old_facts, new_facts)
    reqs = requirement_changes(old_requirements, new_requirements)
    if not (spec or facts or reqs):
        return None
    return {"from_version": from_version, "to_version": to_version, "spec": spec, "facts": facts, "requirements": reqs,
            "worse": any(r["direction"] == _WORSE for r in facts) or any(r["direction"] == _WORSE for r in reqs)}


def warning_sentence(diff: dict | None, limit: int = 3, requirements: bool = True) -> str:
    """One short sentence naming what got worse, for the reply text. Empty when nothing did.
    Pass requirements=False when the reply already states requirement results."""
    if not diff or not diff.get("worse"):
        return ""
    items: list[str] = []
    for r in diff.get("facts") or []:
        if r["direction"] != _WORSE:
            continue
        if r["label"] == "New overlap":
            items.append("new overlap between " + str(r["after"]).split(",")[0])
        elif r["label"] == "Separate groups":
            items.append(f"{r['after']} separate groups of parts")
        elif r["label"] == "Thinnest wall":
            items.append(f"thinnest wall now {r['after']}")
        else:
            items.append(f"{r['label'].lower()} {r['before']} to {r['after']}")
    for r in (diff.get("requirements") or []) if requirements else []:
        if r["direction"] == _WORSE:
            items.append(f"requirement \"{r['text']}\" now fails")
    if not items:
        return ""
    more = len(items) - limit
    text = "; ".join(items[:limit]) + (f"; and {more} more" if more > 0 else "")
    return f" Check this: {text}."
