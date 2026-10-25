"""
Design criteria checks.

A Criteria object is a set of limits that YOU choose. NitoCAD checks results
against them and reports pass or fail for each one. It never says a design
"complies with" a standard.

generic_starter() is a set of round numbers that appear in many textbooks, so a
first run is not empty. They are starting points, not any country's standard.
Replace them with the values from the standard your project must follow, using
the `criteria` field of each request, or edit the dict and save it with the
project.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, fields

from .errors import CivilInputError

STARTER_NAME = "Generic starter criteria (editable, not a design standard)"


@dataclass
class Criteria:
    name: str = STARTER_NAME
    # Road geometry
    lane_width_min_m: float | None = 2.7
    lane_width_max_m: float | None = 4.0
    crossfall_min_pct: float | None = 1.5
    crossfall_max_pct: float | None = 4.0
    max_grade_pct: float | None = 8.0
    design_speed_kmh: float | None = 60.0
    e_max: float | None = 0.06                    # maximum superelevation (ratio)
    f_max: float | None = 0.15                    # maximum side friction factor
    min_curve_radius_m: float | None = None       # overrides the speed based value when set
    min_k_crest: float | None = None
    min_k_sag: float | None = None
    # Drainage
    pipe_min_velocity_ms: float | None = 0.6
    pipe_max_velocity_ms: float | None = 4.0
    pipe_max_fill_ratio: float | None = 0.8
    channel_max_velocity_ms: float | None = 2.0
    channel_max_froude: float | None = 0.8
    culvert_max_hw_to_d: float | None = 1.2
    culvert_min_velocity_ms: float | None = 0.6
    culvert_max_velocity_ms: float | None = 4.0
    # Retaining wall
    wall_min_fs_overturning: float | None = 2.0
    wall_min_fs_sliding: float | None = 1.5
    wall_require_middle_third: bool = True


def generic_starter() -> Criteria:
    return Criteria()


def criteria_from_dict(overrides: dict | None) -> Criteria:
    c = Criteria()
    if not overrides:
        return c
    allowed = {f.name: f for f in fields(Criteria)}
    for k, v in overrides.items():
        if k not in allowed:
            raise CivilInputError(f"Unknown criteria field '{k}'")
        if k == "name":
            if not isinstance(v, str) or not v.strip() or len(v) > 120:
                raise CivilInputError("criteria name must be a short text")
        elif k == "wall_require_middle_third":
            if not isinstance(v, bool):
                raise CivilInputError("wall_require_middle_third must be true or false")
        elif v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)):
            raise CivilInputError(f"criteria '{k}' must be a number or null")
        setattr(c, k, v)
    return c


def min_radius_from_speed(c: Criteria) -> float | None:
    """R = V^2 / (127 (e + f)), V in km/h, the standard point-mass curve formula."""
    if c.design_speed_kmh and c.e_max is not None and c.f_max is not None and (c.e_max + c.f_max) > 0:
        return c.design_speed_kmh ** 2 / (127.0 * (c.e_max + c.f_max))
    return None


def _f(check: str, ok: bool | None, value, limit, text: str) -> dict:
    return {"check": check, "status": "info" if ok is None else ("pass" if ok else "fail"),
            "value": value, "limit": limit, "message": text}


def check_road(c: Criteria, *, lane_width_m: float, crossfall_pct: float,
               horizontal=None, vertical=None) -> list[dict]:
    out = []
    if c.lane_width_min_m is not None:
        out.append(_f("lane width minimum", lane_width_m >= c.lane_width_min_m, lane_width_m, c.lane_width_min_m,
                      f"Lane width {lane_width_m:g} m, minimum {c.lane_width_min_m:g} m"))
    if c.lane_width_max_m is not None:
        out.append(_f("lane width maximum", lane_width_m <= c.lane_width_max_m, lane_width_m, c.lane_width_max_m,
                      f"Lane width {lane_width_m:g} m, maximum {c.lane_width_max_m:g} m"))
    if c.crossfall_min_pct is not None:
        out.append(_f("crossfall minimum", crossfall_pct >= c.crossfall_min_pct, crossfall_pct, c.crossfall_min_pct,
                      f"Crossfall {crossfall_pct:g}%, minimum {c.crossfall_min_pct:g}%"))
    if c.crossfall_max_pct is not None:
        out.append(_f("crossfall maximum", crossfall_pct <= c.crossfall_max_pct, crossfall_pct, c.crossfall_max_pct,
                      f"Crossfall {crossfall_pct:g}%, maximum {c.crossfall_max_pct:g}%"))
    if vertical is not None:
        g = vertical.max_abs_grade_pct()
        if c.max_grade_pct is not None:
            out.append(_f("maximum grade", g <= c.max_grade_pct, round(g, 3), c.max_grade_pct,
                          f"Steepest grade {g:.2f}%, limit {c.max_grade_pct:g}%"))
        for cv in vertical.curves:
            lim = c.min_k_crest if cv["type"] == "crest" else c.min_k_sag
            if lim is not None and cv["k_value"] is not None:
                out.append(_f(f"{cv['type']} curve K at PVI {cv['pvi']}", cv["k_value"] >= lim,
                              round(cv["k_value"], 2), lim, f"K = {cv['k_value']:.1f}, minimum {lim:g}"))
    if horizontal is not None:
        rmin = c.min_curve_radius_m if c.min_curve_radius_m is not None else min_radius_from_speed(c)
        if rmin is not None:
            if not horizontal.curves:
                out.append(_f("curve radius", None, None, round(rmin, 1), "No horizontal curves to check"))
            for cv in horizontal.curves:
                out.append(_f(f"curve radius at PI {cv['pi']}", cv["radius"] >= rmin, cv["radius"], round(rmin, 1),
                              f"Radius {cv['radius']:g} m, minimum {rmin:.1f} m"))
    if not out:
        out.append(_f("road", None, None, None, "No road criteria are set"))
    return out


def check_pipe(c: Criteria, result: dict) -> list[dict]:
    if result.get("status") != "ok":
        return [_f("pipe capacity", False, result.get("flow_m3s"), result.get("q_max_free_surface_m3s"),
                   "The flow is more than the pipe carries as free-surface flow")]
    out = []
    v, fr = result["velocity_ms"], result["fill_ratio"]
    if c.pipe_max_fill_ratio is not None:
        out.append(_f("pipe fill", fr <= c.pipe_max_fill_ratio, round(fr, 3), c.pipe_max_fill_ratio,
                      f"Depth is {fr:.0%} of the diameter, limit {c.pipe_max_fill_ratio:.0%}"))
    if c.pipe_min_velocity_ms is not None:
        out.append(_f("pipe minimum velocity", v >= c.pipe_min_velocity_ms, round(v, 3), c.pipe_min_velocity_ms,
                      f"Velocity {v:.2f} m/s, minimum {c.pipe_min_velocity_ms:g} m/s"))
    if c.pipe_max_velocity_ms is not None:
        out.append(_f("pipe maximum velocity", v <= c.pipe_max_velocity_ms, round(v, 3), c.pipe_max_velocity_ms,
                      f"Velocity {v:.2f} m/s, maximum {c.pipe_max_velocity_ms:g} m/s"))
    return out


def check_channel(c: Criteria, result: dict) -> list[dict]:
    out = []
    v, fr = result["velocity_ms"], result["froude"]
    if c.channel_max_velocity_ms is not None:
        out.append(_f("channel velocity", v <= c.channel_max_velocity_ms, round(v, 3), c.channel_max_velocity_ms,
                      f"Velocity {v:.2f} m/s, maximum {c.channel_max_velocity_ms:g} m/s"))
    if c.channel_max_froude is not None:
        out.append(_f("channel Froude number", fr <= c.channel_max_froude, round(fr, 3), c.channel_max_froude,
                      f"Froude number {fr:.2f}, maximum {c.channel_max_froude:g}"))
    return out


def check_culvert(c: Criteria, result: dict) -> list[dict]:
    out = []
    barrel, inlet = result["barrel"], result["inlet"]
    if barrel["status"] != "ok":
        out.append(_f("barrel capacity", False, result["inputs"]["flow_per_barrel_m3s"],
                      barrel["capacity_full_per_barrel_m3s"], "The flow is more than the barrel carries"))
    else:
        v = barrel["velocity_ms"]
        if c.culvert_min_velocity_ms is not None:
            out.append(_f("culvert minimum velocity", v >= c.culvert_min_velocity_ms, round(v, 3),
                          c.culvert_min_velocity_ms, f"Barrel velocity {v:.2f} m/s, minimum {c.culvert_min_velocity_ms:g}"))
        if c.culvert_max_velocity_ms is not None:
            out.append(_f("culvert maximum velocity", v <= c.culvert_max_velocity_ms, round(v, 3),
                          c.culvert_max_velocity_ms, f"Barrel velocity {v:.2f} m/s, maximum {c.culvert_max_velocity_ms:g}"))
    if c.culvert_max_hw_to_d is not None:
        r = inlet["headwater_to_depth"]
        out.append(_f("headwater to depth", r <= c.culvert_max_hw_to_d, round(r, 3), c.culvert_max_hw_to_d,
                      f"HW/D {r:.2f}, maximum {c.culvert_max_hw_to_d:g}"))
    return out


def check_wall(c: Criteria, result: dict) -> list[dict]:
    out = []
    if c.wall_min_fs_overturning is not None:
        f = result["factor_of_safety_overturning"]
        out.append(_f("overturning", f >= c.wall_min_fs_overturning, round(f, 3), c.wall_min_fs_overturning,
                      f"Factor of safety {f:.2f}, minimum {c.wall_min_fs_overturning:g}"))
    if c.wall_min_fs_sliding is not None:
        f = result["factor_of_safety_sliding"]
        out.append(_f("sliding", f >= c.wall_min_fs_sliding, round(f, 3), c.wall_min_fs_sliding,
                      f"Factor of safety {f:.2f}, minimum {c.wall_min_fs_sliding:g}"))
    if c.wall_require_middle_third:
        out.append(_f("resultant in middle third", result["resultant_within_middle_third"],
                      round(result["eccentricity_m"], 3), round(result["middle_third_limit_m"], 3),
                      f"Eccentricity {result['eccentricity_m']:.2f} m, limit {result['middle_third_limit_m']:.2f} m"))
    allow = result.get("allowable_bearing_kpa")
    if allow is not None:
        q = result["q_max_kpa"]
        out.append(_f("bearing pressure", q <= allow, round(q, 2), allow,
                      f"Maximum base pressure {q:.1f} kPa, allowable {allow:g} kPa"))
    return out


def summarize(findings: list[dict], criteria: Criteria) -> dict:
    n_fail = sum(1 for f in findings if f["status"] == "fail")
    n_pass = sum(1 for f in findings if f["status"] == "pass")
    return {"criteria_name": criteria.name, "passed": n_pass, "failed": n_fail, "findings": findings,
            "statement": ("Checked against the criteria you set. This is not a statement of compliance "
                          "with any design standard.")}
