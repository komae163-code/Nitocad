"""Plain-language reasons for a build that the geometry check refused.

When the post-build check says "STL mesh is not watertight", the person needs to know
what in their request caused it, not a mesh term. This reads the numbers the person
typed (base, boss, bore, holes, envelope, weight limit) and reports only conflicts that
follow directly from those numbers. If nothing can be read with confidence it gives a
short general explanation instead of guessing.

Pure Python, no third-party imports, so it is unit-testable anywhere.
"""
from __future__ import annotations

import math
import re
from typing import Any

# What the geometry check says when the solid it built is not usable.
_GEOMETRY_FAILURES = ("geometry verification failed", "not watertight", "no mesh geometry", "degenerate")

_N = r"(\d+(?:\.\d+)?)"
_X = r"\s*(?:x|×|\*|by)\s*"
_DIMS3 = _N + _X + _N + _X + _N
_COUNTS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10}

# grams per cubic centimetre
_DENSITY = (
    (r"6061|7075|alumin", 2.70), (r"stainless", 8.0), (r"steel", 7.85), (r"brass", 8.5),
    (r"copper", 8.96), (r"titanium", 4.43), (r"\babs\b", 1.04), (r"\bpla\b", 1.24), (r"nylon", 1.15),
)

# Room to leave around a hole or bore so the solid stays sound.
_MARGIN_MM = 1.5          # material each side of a hole in a wall
_MIN_MARGIN_MM = 1.0      # below this the wall is called out as too thin
_BASE_RIM_MM = 4.0        # base left around a bore


def is_geometry_failure(reason: str) -> bool:
    low = (reason or "").lower()
    return any(k in low for k in _GEOMETRY_FAILURES)


def _f(x: str) -> float:
    return float(x)


def _fmt(v: float) -> str:
    return f"{v:.1f}".rstrip("0").rstrip(".") if abs(v - round(v)) > 1e-9 else str(int(round(v)))


def parse_request(text: str) -> dict[str, Any]:
    """Read the dimensions the person stated. Anything not stated clearly is left out."""
    t = (text or "").replace("Ø", " diameter ").replace("ø", " diameter ")
    out: dict[str, Any] = {}

    m = re.search(r"\bbase\b\D{0,25}" + _DIMS3, t, re.I)
    if m:
        out["base"] = (_f(m.group(1)), _f(m.group(2)), _f(m.group(3)))   # length, width, thickness

    m = re.search(r"(?:fit(?:s)?\s+(?:in|within|inside)|within|envelope(?:\s+of)?|no\s+larger\s+than)\D{0,12}" + _DIMS3, t, re.I)
    if m:
        out["envelope"] = (_f(m.group(1)), _f(m.group(2)), _f(m.group(3)))

    m = re.search(_N + r"\s*mm\s*(?:diameter|dia\.?)?\s*(?:round\s+)?boss", t, re.I) \
        or re.search(r"boss\D{0,30}?" + _N + r"\s*mm\s*(?:diameter|dia)", t, re.I)
    if m:
        out["boss_d"] = _f(m.group(1))
    m = re.search(r"boss[^.;]{0,40}?" + _N + r"\s*mm\s*(?:tall|high|height)", t, re.I)
    if m:
        out["boss_h"] = _f(m.group(1))

    m = re.search(_N + r"\s*mm\s*(?:diameter\s*)?bore", t, re.I) \
        or re.search(r"bore\s*(?:of|diameter|dia\.?)?\s*" + _N + r"\s*mm", t, re.I)
    if m:
        out["bore_d"] = _f(m.group(1))

    # Holes: "four 10 mm holes on a 95 x 30 mm rectangle", "two 5 mm holes in the top of the boss".
    base_holes = []
    boss_holes = []
    for m in re.finditer(r"\b(\d+|" + "|".join(_COUNTS) + r")\s+" + _N + r"\s*mm\s+(?:diameter\s+)?holes?\b([^.;]{0,60})", t, re.I):
        n = m.group(1).lower()
        count = int(n) if n.isdigit() else _COUNTS[n]
        d, tail = _f(m.group(2)), m.group(3)
        if re.search(r"\bboss\b", tail, re.I):
            circle = re.search(_N + r"\s*mm\s*(?:diameter\s*)?(?:pitch\s*)?(?:circle|pcd|bolt\s*circle)", tail, re.I)
            boss_holes.append({"n": count, "d": d, "pcd": _f(circle.group(1)) if circle else None})
        else:
            rect = re.search(_N + _X + _N, tail)
            base_holes.append({"n": count, "d": d, "rect": (_f(rect.group(1)), _f(rect.group(2))) if rect else None})
    if base_holes:
        out["base_holes"] = base_holes
    if boss_holes:
        out["boss_holes"] = boss_holes

    m = re.search(r"(?:weigh(?:s|ing)?|weight|mass)\s*(?:of\s*)?(?:under|below|less\s+than|<|at\s+most|no\s+more\s+than|max(?:imum)?(?:\s+of)?)?\s*" + _N + r"\s*(kg|g)\b", t, re.I)
    if m:
        out["max_g"] = _f(m.group(1)) * (1000.0 if m.group(2).lower() == "kg" else 1.0)

    for pat, rho in _DENSITY:
        if re.search(pat, t, re.I):
            out["density"] = rho
            break
    return out


def _estimate_mass_g(p: dict[str, Any]) -> float | None:
    """Base plate + boss standing on it - bore through both - the base holes, in grams.
    Small holes in the boss are left out: their depth is not usually stated."""
    need = ("base", "boss_d", "boss_h", "bore_d", "density")
    if any(k not in p for k in need):
        return None
    length, width, thick = p["base"]
    vol = length * width * thick
    for h in p.get("base_holes", []):
        vol -= h["n"] * math.pi * (h["d"] / 2) ** 2 * thick
    vol += math.pi * (p["boss_d"] / 2) ** 2 * p["boss_h"]
    vol -= math.pi * (p["bore_d"] / 2) ** 2 * (thick + p["boss_h"])
    return max(vol, 0.0) / 1000.0 * p["density"]


def find_conflicts(text: str) -> list[str]:
    """Each item is one short paragraph: a title, what is wrong with the numbers, how to fix it."""
    p = parse_request(text)
    items: list[str] = []
    base = p.get("base")
    env = p.get("envelope")
    boss_d, bore_d = p.get("boss_d"), p.get("bore_d")

    # 1. A bore as wide as, or wider than, the base cuts straight through its edges.
    if base and bore_d:
        length, width, _ = base
        if bore_d >= width:
            items.append(
                f"Bore wider than the base. The bore is {_fmt(bore_d)} mm wide, but the base is only {_fmt(width)} mm wide, "
                f"so the bore cuts through both long edges of the base and leaves paper-thin slivers there. "
                f"Make the base at least {_fmt(bore_d + 2 * _BASE_RIM_MM)} mm wide, or use a smaller bore.")
        elif bore_d >= length:
            items.append(
                f"Bore longer than the base. The bore is {_fmt(bore_d)} mm wide, but the base is only {_fmt(length)} mm long, "
                f"so the bore cuts through both ends of the base. "
                f"Make the base at least {_fmt(bore_d + 2 * _BASE_RIM_MM)} mm long, or use a smaller bore.")

    # 2. A boss wider than the base or the envelope.
    if boss_d:
        if env and boss_d > env[1]:
            items.append(
                f"Boss wider than the box. A {_fmt(boss_d)} mm boss does not fit in a {_fmt(env[1])} mm wide envelope. "
                f"Widen the envelope and the base to at least {_fmt(boss_d + 2)} mm, or use a smaller boss.")
        if base and boss_d > base[1]:
            items.append(
                f"Boss wider than the base. The {_fmt(boss_d)} mm boss is wider than the {_fmt(base[1])} mm base, "
                f"so it hangs over the base by {_fmt((boss_d - base[1]) / 2)} mm each side. "
                f"Make the base at least {_fmt(boss_d + 2)} mm wide.")

    # 3. Holes in the boss wall.
    if boss_d and bore_d and boss_d > bore_d:
        wall = (boss_d - bore_d) / 2
        for h in p.get("boss_holes", []):
            r_pitch = h["pcd"] / 2 if h["pcd"] else (boss_d + bore_d) / 4        # middle of the wall when no circle is given
            inner = r_pitch - h["d"] / 2 - bore_d / 2
            outer = boss_d / 2 - r_pitch - h["d"] / 2
            margin = min(inner, outer)
            if margin < _MIN_MARGIN_MM:
                if margin <= 0:
                    what = (f"the {h['n']} holes of {_fmt(h['d'])} mm are as wide as the wall or wider, "
                            f"so their edges touch the surfaces on both sides")
                else:
                    what = f"the {_fmt(h['d'])} mm holes leave only {_fmt(margin)} mm of material on one side"
                fixes = [f"a bore of {_fmt(boss_d - 2 * (h['d'] + 2 * _MARGIN_MM))} mm or smaller"]
                if wall - 2 * _MARGIN_MM >= 1:
                    fixes.append(f"holes of {_fmt(wall - 2 * _MARGIN_MM)} mm or smaller")
                items.append(
                    f"Holes as wide as the wall. The {_fmt(boss_d)} mm boss with a {_fmt(bore_d)} mm bore leaves a wall only "
                    f"{_fmt(wall)} mm thick, and {what}. Use {' or '.join(fixes)}.")

    # 4. Holes in the base that run off the edge or into the boss.
    if base:
        length, width, _ = base
        for h in p.get("base_holes", []):
            if not h["rect"]:
                continue
            a, b = h["rect"]
            r = h["d"] / 2
            if a / 2 + r > length / 2 or b / 2 + r > width / 2:
                items.append(
                    f"Holes past the edge. The {h['n']} holes of {_fmt(h['d'])} mm on the {_fmt(a)} x {_fmt(b)} mm rectangle "
                    f"reach outside the {_fmt(length)} x {_fmt(width)} mm base. Move them inward or make the base larger.")
            elif boss_d and math.hypot(a / 2, b / 2) - r < boss_d / 2:
                items.append(
                    f"Holes inside the boss. The {_fmt(h['d'])} mm holes on the {_fmt(a)} x {_fmt(b)} mm rectangle "
                    f"overlap the {_fmt(boss_d)} mm boss. Spread them wider or use a smaller boss.")

    # 5. Size and weight against the stated limits.
    if env and base:
        if base[0] > env[0] or base[1] > env[1]:
            items.append(
                f"Base bigger than the box. The base is {_fmt(base[0])} x {_fmt(base[1])} mm but the envelope allows "
                f"{_fmt(env[0])} x {_fmt(env[1])} mm.")
        if p.get("boss_h") and base[2] + p["boss_h"] > env[2]:
            items.append(
                f"Too tall for the box. The {_fmt(base[2])} mm base plus the {_fmt(p['boss_h'])} mm boss is "
                f"{_fmt(base[2] + p['boss_h'])} mm, but the envelope allows {_fmt(env[2])} mm.")
    est = _estimate_mass_g(p)
    if est is not None and p.get("max_g") and est > p["max_g"]:
        items.append(
            f"Weight limit. By my estimate this comes to about {_fmt(round(est))} g (taking the boss to stand {_fmt(p['boss_h'])} mm "
            f"above the base), against your {_fmt(p['max_g'])} g limit. "
            f"Raise the limit to about {_fmt(math.ceil(est * 1.05 / 10) * 10)} g or make the part smaller.")
    return items


_LEAD = "I could not build a clean solid from that, so nothing was built and you were not charged."
_GENERAL = ("This usually means a hole or bore is as wide as the material around it, or a feature sticks out past the part. "
            "Check that holes and bores fit inside the part, then try again.")


def explain_failure(request_text: str, reason: str) -> str | None:
    """A friendly reply for a refused geometry build, or None when the failure is something else."""
    if not is_geometry_failure(reason):
        return None
    try:
        items = find_conflicts(request_text)
    except Exception:  # noqa: BLE001 - an explainer must never turn a refusal into a crash
        items = []
    if items:
        return _LEAD + " Here is what in the request conflicts:\n\n" + "\n\n".join(items)
    return _LEAD + " " + _GENERAL
