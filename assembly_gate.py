"""
The gate an assembly plan passes before anyone sees a preview.

A plan for an assembly is a part tree: named parts, each with the numbers that size it and a
position. Whether two of those parts occupy the same space is decided by arithmetic on those
numbers, so it can be decided before the CAD kernel runs, and a plan that fails can be sent back
to the planner with the exact reason instead of being built, shown, and only then reported as
"2 interferences".

    report = check(spec)
    report.errors      the plan is wrong: parts overlap, or a part sits beyond the end of its shaft
    report.warnings    the plan builds but is probably not what was meant: a gear loose on its shaft
    report.unchecked   parts this gate has no model for, so it said nothing about them

What it models: bodies of revolution about the Z axis, which is how every shaft, gear, pulley,
sprocket, bearing, spacer and washer template is built. Each is reduced to the material that is
certainly there (a ring between two radii, from z0 to z1). Teeth, grooves and keyways are cut
away from that ring's outside or from the shaft, never from inside it, so two of these rings that
overlap are overlapping in the real solids too. That keeps the gate from rejecting a plan that
would have built fine. It misses small overlaps that only teeth or keyways cause. The post-build
interference check in assembly_checks still has the last word on the real solids.

It also rejects a parameter name the template does not read, for the shaft family only. In every other
part type the chat prompt names parameters the catalog lacks (a bolt's "size", a building's rooms), so
a strict check there would reject plans that build fine. This check applies to every layout.

What the geometry checks skip, and say so: layouts other than "custom" (a row is placed apart by
construction and mated parts are placed by the mate engine), parts that are rotated or repeated, and
part types it has no model for.

Pure Python. No CAD kernel, so it runs in tests and on every plan.
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, field
from typing import Any

# Overlap deeper than this is an error. Shallower is tessellation and rounding.
OVERLAP_TOL_MM = 0.05
# A bore this much wider than the shaft is a loose part, not a fitted one.
LOOSE_FIT_MM = 0.5
_EPS = 1e-6
_MAX_LISTED = 6

# part_type -> its template's defaults for the values the model reads.
_FALLBACK_DEFAULTS: dict[str, dict[str, float]] = {
    "shaft": {"diameter_mm": 10.0, "length_mm": 50.0, "chamfer_mm": 0.0, "keyway_width_mm": 0.0, "keyway_depth_mm": 0.0},
    "gear": {"module": 2.0, "teeth": 20, "thickness_mm": 10.0, "bore_diameter_mm": 5.0},
    "pulley": {"outer_diameter_mm": 40.0, "thickness_mm": 15.0, "bore_diameter_mm": 5.0, "groove_depth_mm": 3.0},
    "sprocket": {"teeth": 18, "chain_pitch_mm": 12.7, "thickness_mm": 8.0, "bore_diameter_mm": 8.0},
    "bearing": {"inner_diameter_mm": 10.0, "outer_diameter_mm": 20.0, "width_mm": 5.0},
    "spacer": {"outer_diameter_mm": 10.0, "inner_diameter_mm": 5.0, "length_mm": 10.0},
    "washer": {"outer_diameter_mm": 10.0, "inner_diameter_mm": 5.0, "thickness_mm": 1.0},
}
# The parameter names each template of the shaft family reads. Anything else in "parameters" is
# silently ignored at build time, so a typo such as "diameter" for "diameter_mm" builds a part with the
# default size. tests/test_assembly_gate.py fails if this table and the templates ever disagree.
_KNOWN_PARAMS: dict[str, frozenset[str]] = {
    "shaft": frozenset({"diameter_mm", "length_mm", "chamfer_mm", "keyway_width_mm", "keyway_depth_mm"}),
    "gear": frozenset({"module", "teeth", "thickness_mm", "bore_diameter_mm", "pressure_angle"}),
    "pulley": frozenset({"outer_diameter_mm", "belt_width_mm", "bore_diameter_mm", "thickness_mm", "groove_depth_mm"}),
    "sprocket": frozenset({"teeth", "chain_pitch_mm", "thickness_mm", "bore_diameter_mm"}),
    "bearing": frozenset({"inner_diameter_mm", "outer_diameter_mm", "width_mm"}),
    "spacer": frozenset({"outer_diameter_mm", "inner_diameter_mm", "length_mm"}),
    "washer": frozenset({"outer_diameter_mm", "inner_diameter_mm", "thickness_mm"}),
}
# Things that belong on the part, next to part_type, and that a model sometimes puts inside "parameters".
_PART_LEVEL_KEYS = ("position", "rotation", "count", "name")
# part_type -> the bore key of a part that rides on a shaft
_BORE_KEY = {"gear": "bore_diameter_mm", "pulley": "bore_diameter_mm", "sprocket": "bore_diameter_mm",
             "bearing": "inner_diameter_mm", "spacer": "inner_diameter_mm", "washer": "inner_diameter_mm"}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Body:
    """Material that is certainly present: a ring between r_in and r_out, from z0 to z1, about the
    vertical axis through (cx, cy)."""
    name: str
    part_type: str
    cx: float
    cy: float
    z0: float
    z1: float
    r_in: float
    r_out: float
    r_nom: float = 0.0      # a shaft's nominal radius, before its chamfer or keyway are taken off r_out


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _defaults(part_type: str) -> dict[str, float]:
    return _FALLBACK_DEFAULTS.get(part_type, {})


def _value(part_type: str, params: dict, key: str) -> float | None:
    v = params.get(key)
    return _num(v) if v is not None else _num(_defaults(part_type).get(key))


def _ring(part_type: str, p: dict) -> tuple[float, float, float] | None:
    """(r_in, r_out, height) of the part's certain material, or None when there is no model."""
    g = lambda k: _value(part_type, p, k)  # noqa: E731
    if part_type == "shaft":
        d, length, ch, kw, kd = g("diameter_mm"), g("length_mm"), g("chamfer_mm"), g("keyway_width_mm"), g("keyway_depth_mm")
        if None in (d, length, ch, kw, kd):
            return None
        cut = max(ch, kd if kw > 0 and kd > 0 else 0.0)
        return 0.0, max(d / 2 - cut, 0.0), length
    if part_type == "gear":
        m, t, h, bore = g("module"), g("teeth"), g("thickness_mm"), g("bore_diameter_mm")
        if None in (m, t, h, bore) or t < 3:
            return None
        return bore / 2, m * t / 2 - 1.25 * m, h         # the teeth stand on the root circle
    if part_type == "pulley":
        od, h, bore, groove = g("outer_diameter_mm"), g("thickness_mm"), g("bore_diameter_mm"), g("groove_depth_mm")
        if None in (od, h, bore, groove):
            return None
        return bore / 2, od / 2 - groove, h
    if part_type == "sprocket":
        t, pitch, h, bore = g("teeth"), g("chain_pitch_mm"), g("thickness_mm"), g("bore_diameter_mm")
        if None in (t, pitch, h, bore) or t < 3:
            return None
        return bore / 2, pitch / (2 * math.sin(math.pi / t)) - pitch / 3, h   # tooth spaces are circles of pitch/3 on the pitch circle
    if part_type == "bearing":
        i, o, w = g("inner_diameter_mm"), g("outer_diameter_mm"), g("width_mm")
        return None if None in (i, o, w) else (i / 2, o / 2, w)
    if part_type == "spacer":
        o, i, length = g("outer_diameter_mm"), g("inner_diameter_mm"), g("length_mm")
        return None if None in (o, i, length) else (i / 2, o / 2, length)
    if part_type == "washer":
        o, i, h = g("outer_diameter_mm"), g("inner_diameter_mm"), g("thickness_mm")
        return None if None in (o, i, h) else (i / 2, o / 2, h)
    return None


def _labels(parts: list[dict]) -> list[str]:
    raw = [str(p.get("name") or p.get("part_type") or "part") for p in parts]
    seen: dict[str, int] = {}
    out = []
    for r in raw:
        seen[r] = seen.get(r, 0) + 1
        out.append(r if raw.count(r) == 1 else f"{r}_{seen[r]}")
    return out


def _vec(v: Any) -> list[float] | None:
    if isinstance(v, (list, tuple)) and len(v) == 3 and all(_num(c) is not None for c in v):
        return [float(c) for c in v]
    return None


def body_of(part: dict, label: str | None = None) -> Body | None:
    """The part as a Body, or None when it is rotated, repeated, or has no model."""
    ptype = part.get("part_type")
    if (part.get("count", 1) or 1) != 1:
        return None
    rot = _vec(part.get("rotation")) if part.get("rotation") is not None else [0.0, 0.0, 0.0]
    if rot is None or any(abs(c) > _EPS for c in rot):
        return None
    pos = _vec(part.get("position")) if part.get("position") is not None else [0.0, 0.0, 0.0]
    if pos is None or not isinstance(ptype, str):
        return None
    ring = _ring(ptype, part.get("parameters") or {})
    if ring is None:
        return None
    r_in, r_out, h = ring
    if h <= 0 or r_out <= r_in + _EPS:
        return None                                       # no material we can be sure of
    nominal = (_value("shaft", part.get("parameters") or {}, "diameter_mm") or 0.0) / 2 if ptype == "shaft" else r_out
    return Body(label or str(part.get("name") or ptype), ptype, pos[0], pos[1], pos[2], pos[2] + h, max(r_in, 0.0), r_out, nominal)


def clearance(a: Body, b: Body) -> float:
    """Signed distance between two bodies of revolution about parallel vertical axes. Negative is
    overlap, and its size is how far one would have to move to clear the other. Exact in sign."""
    d = math.hypot(a.cx - b.cx, a.cy - b.cy)
    radial = max(d - a.r_out - b.r_out, b.r_in - (d + a.r_out), a.r_in - (d + b.r_out))
    axial = max(a.z0 - b.z1, b.z0 - a.z1)
    return max(radial, axial)


def _fix_hint(a: Body, b: Body) -> str:
    """What to change, when the overlap is a part pushed onto a shaft it is too narrow for."""
    for ring, shaft in ((a, b), (b, a)):
        if shaft.part_type == "shaft" and ring.part_type in _BORE_KEY and ring.r_in < shaft.r_nom and abs(ring.cx - shaft.cx) < 1e-6 and abs(ring.cy - shaft.cy) < 1e-6:
            return f" Its bore is {ring.r_in * 2:g} mm and the shaft is {shaft.r_nom * 2:g} mm, so the bore needs to be about {round(shaft.r_nom * 2 + 0.2, 3):g} mm."
    if abs(a.cx - b.cx) < 1e-6 and abs(a.cy - b.cy) < 1e-6:
        lo, hi = (a, b) if a.z0 <= b.z0 else (b, a)
        return f" Both sit on the same axis, {lo.name} from z={lo.z0:g} to {lo.z1:g} and {hi.name} from z={hi.z0:g} to {hi.z1:g}. Move one so they do not share any of that length."
    return " Move one of them apart, or make one smaller."


def _check_names(parts: list[dict], labels: list[str], rep: Report) -> None:
    """A parameter the template does not read is ignored and the part is built with the default."""
    listed = 0
    for p, label in zip(parts, labels):
        known = _KNOWN_PARAMS.get(p.get("part_type"))
        given = p.get("parameters")
        if known is None or not isinstance(given, dict):
            continue
        for key in given:
            if key in known:
                continue
            listed += 1
            if listed > _MAX_LISTED:
                continue
            if key in _PART_LEVEL_KEYS:
                rep.errors.append(f'{label} has "{key}" inside "parameters". It belongs on the part itself, next to "part_type".')
                continue
            near = difflib.get_close_matches(str(key), sorted(known), n=1, cutoff=0.5)
            tail = f' Did you mean "{near[0]}"?' if near else " It takes: " + ", ".join(sorted(known)) + "."
            rep.errors.append(f'{label} has a parameter "{key}" that {p["part_type"]} does not have, so it would be ignored and the default used.{tail}')
    if listed > _MAX_LISTED:
        rep.errors.append(f"{listed - _MAX_LISTED} more unknown parameter names.")


def check(spec: dict | None) -> Report:
    """Check an assembly plan. Never raises."""
    rep = Report()
    try:
        _check(spec, rep)
    except Exception:  # noqa: BLE001 - the gate must never be the reason a chat turn fails
        return Report()
    return rep


def _check(spec: Any, rep: Report) -> None:
    if not (isinstance(spec, dict) and spec.get("part_type") == "assembly" and isinstance(spec.get("parameters"), dict)):
        return
    params = spec["parameters"]
    parts = [p for p in (params.get("parts") or []) if isinstance(p, dict)]
    labels = _labels(parts)
    _check_names(parts, labels, rep)
    if params.get("layout") != "custom" or params.get("vehicle") is not None:
        return        # a vehicle is checked by its own spec's design checks; parts that share a bore are meant to touch
    bodies: list[Body] = []
    for p, label in zip(parts, labels):
        if p.get("position") is not None and _vec(p.get("position")) is None:
            rep.errors.append(f"{label} has a position that is not three numbers.")
            continue
        b = body_of(p, label)
        if b is None:
            rep.unchecked.append(label)
        else:
            bodies.append(b)
            rep.checked.append(label)

    # 1. Parts must not share space.
    found = 0
    for i, a in enumerate(bodies):
        for b in bodies[i + 1:]:
            c = clearance(a, b)
            if c < -OVERLAP_TOL_MM:
                found += 1
                if found <= _MAX_LISTED:
                    rep.errors.append(f"{a.name} and {b.name} overlap by about {-c:.2g} mm.{_fix_hint(a, b)}")
    if found > _MAX_LISTED:
        rep.errors.append(f"{found - _MAX_LISTED} more overlapping pairs.")

    # 2. Parts that ride on a shaft must be on it and fitted to it.
    shafts = [b for b in bodies if b.part_type == "shaft"]
    if len(shafts) == 1:
        s = shafts[0]
        for b in bodies:
            if b.part_type not in _BORE_KEY or abs(b.cx - s.cx) > _EPS or abs(b.cy - s.cy) > _EPS:
                continue
            centre = (b.z0 + b.z1) / 2
            if centre < s.z0 - _EPS or centre > s.z1 + _EPS:
                rep.errors.append(f"{b.name} is centred at z={centre:g}, past the end of the {s.z1 - s.z0:g} mm shaft (z={s.z0:g} to {s.z1:g}). Put it on the shaft.")
            elif b.z0 < s.z0 - _EPS or b.z1 > s.z1 + _EPS:
                rep.warnings.append(f"{b.name} hangs over the end of the shaft by {max(s.z0 - b.z0, b.z1 - s.z1):.2g} mm.")
            if b.r_in - s.r_nom > LOOSE_FIT_MM / 2 + _EPS:
                rep.warnings.append(f"{b.name} is loose on the shaft: its bore is {b.r_in * 2:g} mm and the shaft is {s.r_nom * 2:g} mm.")
