"""
Rejects vehicle plans that are too coarse to explode, weigh or draw, and says exactly how to split them.

A plan like "one body solid and one exhaust tube" is valid against the part catalog, but it is not a vehicle: nothing
can be pulled apart, the body is a solid block with a credible volume of a house, and the drawing shows silhouettes.
The planner sends these messages back to the model, which then plans again with real sub-parts.

    issues = coarse_issues(vspec, expanded, request="a car with a 5 speed gearbox")

Pure Python, no CAD kernel. The rules look at part names, part types and parameter names only, so they are cheap
and can run on every retry. They are a floor, not a proof that the design is good.
"""
from __future__ import annotations

import re
from typing import Any

import vehicle_shell as VSH

MIN_PARTS = 12                    # fewest parts in a whole vehicle
MIN_GROUPS = 3                    # fewest assemblies (body, wheels, powertrain ...)
MIN_PANELS = 3                    # separate body pieces when there is a body part

_BODY_NAME = VSH.BODY_NAME
_PANEL_NAME = re.compile(r"door|hood|bonnet|boot|trunk|tailgate|roof|floor|panel|bumper|fender|wing|sill|pillar|"
                         r"windscreen|windshield|window|glass|seat|dash|firewall|bulkhead|skin|frame|rail|cross", re.I)
_GEAR_WORDS = re.compile(r"\b(gears?|gearbox|gear box|transmission|differential|speed|geared)\b", re.I)
_ENGINE_WORDS = re.compile(r"\b(engine|motor|piston|cylinder|crank\w*)\b", re.I)
_WHEEL_NAME = re.compile(r"(?<!fly)wheel|tyre|tire", re.I)
_MONOLITH_TYPES = {"feature_tree", "simple_box"}
_BIG_VEHICLE = re.compile(r"\b(car|sedan|saloon|coupe|hatchback|suv|van|truck|lorry|pickup|bus|tractor|tank|jeep|buggy|locomotive|train|tram|boat|ship|airplane|aeroplane|aircraft|plane|jet|helicopter)s?\b", re.I)


def _parts(expanded: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in (expanded.get("parts") or []) if isinstance(p, dict)]


def _ptype(p: dict[str, Any]) -> str:
    return str(p.get("part_type") or p.get("type") or "")


def _features(p: dict[str, Any]) -> list[dict[str, Any]]:
    params = p.get("parameters") or {}
    feats = params.get("features") if isinstance(params, dict) else None
    return [f for f in feats if isinstance(f, dict)] if isinstance(feats, list) else []


def _has_hollow(p: dict[str, Any]) -> bool:
    """A body counts as a shell when a cut feature removes its inside (expand_vehicle adds one automatically)."""
    return VSH.has_hollow(_features(p))


def coarse_issues(vspec: dict[str, Any], expanded: dict[str, Any], request: str = "") -> list[str]:
    """Plain-language problems, each ending in what to do about it. Empty when the plan is fine."""
    parts = _parts(expanded)
    if not parts:
        return []
    out: list[str] = []
    names = [str(p.get("name", "")) for p in parts]
    text = f"{request} {vspec.get('name', '')}".replace("_", " ")      # "hatchback_car" has no \\b before "car"
    wants_gears = bool(_GEAR_WORDS.search(request))
    wants_engine = bool(_ENGINE_WORDS.search(request))

    whole = bool(_BIG_VEHICLE.search(text))
    floor = MIN_PARTS + (6 if wants_gears else 0) + (6 if wants_engine else 0)
    if whole and len(parts) < floor:
        out.append(f"the plan has only {len(parts)} parts, and a vehicle needs at least {floor} to be pulled apart. "
                   "Build it from sub-parts: wheels as tyre + hub + axle + bearings, the body as separate panels "
                   "(floor, sides, roof, hood, doors), and the powertrain and exhaust as their own parts")
    groups = expanded.get("assemblies") or {}
    if whole and len(groups) < MIN_GROUPS:
        out.append(f"the parts sit in {len(groups)} group(s). Give each system its own 'assembly' name "
                   "(for example body, wheels, powertrain, exhaust) so the exploded view can move each group")

    for p in parts:
        n = str(p.get("name", ""))
        if whole and _ptype(p) in _MONOLITH_TYPES and _BODY_NAME.search(n) and not _PANEL_NAME.search(n) \
                and not VSH.is_sheet(_features(p), VSH.default_wall(VSH._extent(_features(p)))):
            panels = [m for m in names if m != n and _PANEL_NAME.search(m)]
            if len(panels) < MIN_PANELS:
                out.append(f"'{n}' is one {_ptype(p)} that holds the whole body. Split it into at least "
                           f"{MIN_PANELS} separate parts, such as floor, side panels, roof, hood and doors. For a car, "
                           "van or truck body, place the 'car_body' module instead; it makes all of those panels")
            elif _ptype(p) == "feature_tree" and not _has_hollow(p):
                why = next((m for m in (expanded.get("shell_problems") or []) if f"'{n}'" in m), "")
                out.append(f"'{n}' is a solid block, so its mass and volume are far too large"
                           + (f" ({why})" if why else "")
                           + ". Build the body from convex polygon or rectangle extrudes, or add a feature with mode "
                             "'cut' and an id like 'interior' that removes the inside, leaving a 2 to 3 mm wall")

    if wants_gears:
        gears = [p for p in parts if _ptype(p) == "gear"]
        shafts = [p for p in parts if _ptype(p) in ("shaft", "crankshaft")]
        if len(gears) < 2:
            out.append(f"gears were requested but the plan has {len(gears)} gear part(s). Use at least two meshing "
                       "parts of type 'gear' (or the gear_pair module) on parallel shafts")
        elif not shafts:
            out.append("the gears have no shaft to sit on. Add a shaft through each gear bore")
        for p in gears:
            prm = p.get("parameters") or {}
            if isinstance(prm, dict) and not prm.get("keyway_width_mm"):
                out.append(f"gear '{p.get('name')}' has no keyway. Add keyway_width_mm and keyway_depth_mm "
                           "(and backlash_mm, around 0.05 x module, so the pair can turn)")
                break
    if wants_engine and not any(_ptype(p) in ("crankshaft", "connecting_rod")
                                or re.search(r"crank|con_?rod|connecting", str(p.get("name", "")), re.I) for p in parts):
        out.append("an engine was requested but there is no crankshaft or connecting rod. Add the piston_engine "
                   "module or build crankshaft, rods, pistons and liners as separate parts")

    wheels = [m for m in names if _WHEEL_NAME.search(m)]
    if wheels and not any(_ptype(p) in ("shaft", "tube") and re.search(r"axle", str(p.get("name", "")), re.I)
                          for p in parts):
        out.append("the wheels have no axle. Add one shaft per axle, through the wheel centres")
    return out
