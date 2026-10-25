"""
Relations inside an assembly: when the shaft changes, the parts on it follow.

An assembly spec is plain numbers. A gear bore of 8.2 does not know it is 0.2 mm over an 8 mm
shaft, so an edit that only changes the shaft leaves the gear loose or the bearing floating.
Neither the AI planner nor the rules planner carries the change through, so this module does it
after every edit, the same way for both.

    result = reconcile(old_spec, new_spec)
    result.spec      new_spec with the follow-on changes applied (a copy)
    result.changes   what moved, one line each, in the same shape as the spec diff
                     ("gear bore_diameter_mm: 8.2 to 10.2 (follows shaft diameter)")
    result.blocked   what could not follow and why, so nothing fails quietly

Two relations, both about one shaft:
  * bore follows shaft diameter: a gear, pulley, sprocket, bearing or spacer whose bore was a
    running fit on the old shaft (0 to 0.5 mm over) keeps the same clearance on the new one.
  * position follows shaft length: a part on the shaft keeps its distance from the end it is
    nearest to. A part in the middle third keeps its fractional place.

What it never does:
  * override a value the edit itself changed. If the plan moved the gear, the gear stays where
    the plan put it.
  * change an assembly with more or fewer parts than before, with more than one shaft, or with
    mated layout (the mate engine places those).
  * make a part that cannot hold the new bore, or push parts off the shaft or into each other.
    It leaves them as they were and says so in `blocked`.

Pure Python, no CAD kernel.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

# Bores are this much wider than the shaft (diameter), so the two solids never share a surface.
FIT_CLEARANCE_MM = 0.2
# A bore this close to the old shaft counts as fitted to it.
MAX_FIT_GAP_MM = 0.5
# Least wall left around a bore for the new bore to be accepted.
MIN_WALL_MM = 1.5
_EPS = 1e-6

# part_type -> (bore key, size along the shaft key)
_FOLLOWERS: dict[str, tuple[str, str]] = {
    "gear": ("bore_diameter_mm", "thickness_mm"),
    "pulley": ("bore_diameter_mm", "thickness_mm"),
    "sprocket": ("bore_diameter_mm", "thickness_mm"),
    "bearing": ("inner_diameter_mm", "width_mm"),
    "spacer": ("inner_diameter_mm", "length_mm"),
}
# What the CAD engine uses when the spec leaves the value out.
_DEFAULT_EXTENT = {"gear": 10.0, "pulley": 15.0, "sprocket": 8.0, "bearing": 5.0, "spacer": 10.0}
_DEFAULT_OUTER = {"pulley": 40.0, "bearing": 20.0, "spacer": 10.0}
_SHAFT_DEFAULTS = {"diameter_mm": 10.0, "length_mm": 50.0}


@dataclass
class Result:
    spec: dict
    changes: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


def fit_bore(shaft_diameter_mm: float, clearance_mm: float = FIT_CLEARANCE_MM) -> float:
    """The bore a part gets on a shaft of this diameter."""
    return _clean(shaft_diameter_mm + clearance_mm)


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _clean(x: float) -> float | int:
    x = round(float(x), 3)
    return int(x) if x.is_integer() else x


def _is_assembly(spec: Any) -> bool:
    return isinstance(spec, dict) and spec.get("part_type") == "assembly" and isinstance(spec.get("parameters"), dict)


def _parts(spec: dict) -> list[dict]:
    parts = (spec.get("parameters") or {}).get("parts")
    return [p for p in parts if isinstance(p, dict)] if isinstance(parts, list) else []


def _pair(old_parts: list[dict], new_parts: list[dict]) -> list[tuple[dict, dict]] | None:
    """Match each new part to the old one it came from: by name when every part has a unique
    name, otherwise by place. None when parts were added or removed."""
    on, nn = [p.get("name") for p in old_parts], [p.get("name") for p in new_parts]
    if all(on) and all(nn) and len(set(on)) == len(on) and len(set(nn)) == len(nn) and set(on) == set(nn):
        by = {p["name"]: p for p in old_parts}
        return [(by[p["name"]], p) for p in new_parts]
    if len(old_parts) == len(new_parts):
        return list(zip(old_parts, new_parts))
    return None


def _label(p: dict) -> str:
    return str(p.get("name") or p.get("id") or p.get("part_type") or "part")


def _extent(part_type: str, params: dict) -> float:
    key = _FOLLOWERS[part_type][1]
    v = _num(params.get(key))
    return v if v is not None else _DEFAULT_EXTENT[part_type]


def _bore_limit(part_type: str, params: dict) -> float | None:
    """The widest bore the part's own body allows before the wall is gone (diameter, mm)."""
    if part_type == "gear":
        m, t = _num(params.get("module")) or 2.0, _num(params.get("teeth")) or 20.0
        return m * t - 2.5 * m  # root circle diameter: the teeth stand on it
    if part_type == "sprocket":
        pitch, t = _num(params.get("chain_pitch_mm")) or 12.7, _num(params.get("teeth")) or 18.0
        return pitch / math.sin(math.pi / max(t, 3.0)) - 2 * pitch / 3  # tooth spaces are circles of pitch/3 on the pitch circle
    outer = _num(params.get("outer_diameter_mm"))
    return outer if outer is not None else _DEFAULT_OUTER.get(part_type)


def _position(p: dict) -> list[float] | None:
    pos = p.get("position")
    if isinstance(pos, (list, tuple)) and len(pos) == 3 and all(_num(c) is not None for c in pos):
        return [float(c) for c in pos]
    return None


def pos_text(pos: Any) -> str:
    """A position as it appears in the diff, for example (0, 0, 20)."""
    if isinstance(pos, (list, tuple)) and len(pos) == 3 and all(_num(c) is not None for c in pos):
        return "(" + ", ".join(f"{float(c):g}" for c in pos) + ")"
    return str(pos)


def _same(a: list[float] | None, b: list[float] | None) -> bool:
    return a is not None and b is not None and all(abs(x - y) <= _EPS for x, y in zip(a, b))


def reconcile(old_spec: dict | None, new_spec: dict) -> Result:
    """Carry a change to the shaft through to the parts on it. See the module docstring."""
    new = copy.deepcopy(new_spec)
    res = Result(new)
    if not (_is_assembly(old_spec) and _is_assembly(new)):
        return res
    if (old_spec["parameters"].get("layout") == "mated") or (new["parameters"].get("layout") == "mated"):
        return res
    pairs = _pair(_parts(old_spec), _parts(new))
    if not pairs:
        return res
    shafts = [(o, n) for o, n in pairs if o.get("part_type") == "shaft" and n.get("part_type") == "shaft"]
    if len(shafts) != 1 or sum(1 for _, n in pairs if n.get("part_type") == "shaft") != 1:
        return res
    so, sn = shafts[0]
    sop, snp = so.get("parameters") or {}, sn.get("parameters") or {}
    d_old = _num(sop.get("diameter_mm", _SHAFT_DEFAULTS["diameter_mm"]))
    d_new = _num(snp.get("diameter_mm", _SHAFT_DEFAULTS["diameter_mm"]))
    l_old = _num(sop.get("length_mm", _SHAFT_DEFAULTS["length_mm"]))
    l_new = _num(snp.get("length_mm", _SHAFT_DEFAULTS["length_mm"]))
    if None in (d_old, d_new, l_old, l_new):
        return res
    d_changed, l_changed = abs(d_new - d_old) > _EPS, abs(l_new - l_old) > _EPS
    if not (d_changed or l_changed):
        return res
    custom = new["parameters"].get("layout") == "custom"

    on_shaft: list[dict] = []
    for o, n in pairs:
        t = n.get("part_type")
        if t != o.get("part_type") or t not in _FOLLOWERS:
            continue
        if (o.get("count", 1) or 1) != 1 or (n.get("count", 1) or 1) != 1:
            continue
        label = _label(n)
        bore_key = _FOLLOWERS[t][0]
        op = o.get("parameters") or {}
        npar = n.setdefault("parameters", {})

        if d_changed:
            ob, nb = _num(op.get(bore_key)), _num(npar.get(bore_key))
            fitted = ob is not None and nb is not None and abs(nb - ob) <= _EPS and -_EPS <= ob - d_old <= MAX_FIT_GAP_MM
            if fitted:
                target = _clean(d_new + (ob - d_old))
                limit = _bore_limit(t, npar)
                if limit is not None and target + 2 * MIN_WALL_MM > limit:
                    res.blocked.append(f"{label} keeps its {ob:g} mm bore: a {d_new:g} mm shaft would leave it under {MIN_WALL_MM:g} mm of wall")
                else:
                    npar[bore_key] = target
                    res.changes.append(f"{label} {bore_key}: {ob:g} to {target:g} (follows shaft diameter)")

        if l_changed and custom:
            po, pn = _position(o), _position(n)
            if po is None or pn is None or abs(po[0]) > _EPS or abs(po[1]) > _EPS:
                continue
            e_o, e_n = _extent(t, op), _extent(t, npar)
            if po[2] < -_EPS or po[2] + e_o > l_old + _EPS:
                continue  # not on the old shaft, so not ours to move
            on_shaft.append({"n": n, "label": label, "pn": pn, "z_old": po[2], "e": e_n,
                             "explicit": not _same(po, pn), "z": pn[2]})

    if on_shaft:
        planned = []
        for item in on_shaft:
            if item["explicit"]:
                continue  # the edit placed it, so it stays where the edit put it
            e, z_old = item["e"], item["z_old"]
            frac = (z_old + e / 2) / l_old if l_old > 0 else 0.5
            if frac < 1 / 3:
                z = z_old
            elif frac > 2 / 3:
                z = z_old + (l_new - l_old)
            else:
                z = frac * l_new - e / 2
            item["z"] = _clean(z)
            planned.append(item)
        spans = sorted((it["z"], it["z"] + it["e"], it["label"]) for it in on_shaft)
        off = [it["label"] for it in planned if it["z"] < -_EPS or it["z"] + it["e"] > l_new + _EPS]
        hit = [(a[2], b[2]) for a, b in zip(spans, spans[1:]) if a[1] > b[0] + _EPS]
        if off or hit:
            why = "would not fit on" if off else "would overlap on"
            res.blocked.append(f"the parts stay where they were: they {why} a {l_new:g} mm shaft")
        else:
            for it in planned:
                if abs(it["z"] - it["z_old"]) > _EPS:
                    before = [it["pn"][0], it["pn"][1], it["z_old"]]
                    it["n"]["position"] = [it["pn"][0], it["pn"][1], it["z"]]
                    res.changes.append(f"{it['label']} position: {pos_text(before)} to {pos_text(it['n']['position'])} (follows shaft length)")
    return res
