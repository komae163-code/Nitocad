"""
Design-for-manufacture checks driven by a part's template parameters.

Rules of thumb from common shop guidelines (typical values, not a specific shop's limits). Each
finding carries the value, the limit and a plain message, and severity is one of:
  error    cannot be made by this process as drawn
  warning  makeable but costly, fragile or likely to disappoint
  info     worth knowing

Only what the parameters actually tell us is checked: a rule whose input is not in the spec is
skipped, never guessed. Hole positions, overhang angles and internal corner radii are not in the
templates' parameters, so those rules are not here (manufacturability.py adds the ones that can be
read from the part's mesh).

Two processes, injection molding and resin printing, are checked on request only. They are not part
of PROCESSES, so applicable() and best_fit() (which rank the original four for a chat question) are
unchanged.
"""
from __future__ import annotations

import math
from typing import Any

import materials

PROCESSES = ("cnc_milling", "turning", "fdm_print", "sheet_metal")
EXTRA_PROCESSES = ("injection_molding", "resin_print")
ALL_PROCESSES = PROCESSES + EXTRA_PROCESSES

# Longest side a typical machine takes, per process. The size rules below use these numbers, and
# manufacturability.py applies the same limits to a part's measured mesh.
SIZE_LIMIT_MM = {"cnc_milling": 500.0, "turning": 500.0, "fdm_print": 220.0, "resin_print": 185.0, "injection_molding": 500.0}
_ROTATIONAL = {"shaft", "spacer", "washer", "tube", "pulley", "sphere", "hex_standoff", "bearing", "sprocket", "gear", "cam"}
_SHEET = {"l_bracket", "t_bracket", "channel_bracket", "flat_plate", "angle"}


def _f(p: dict, *keys: str) -> float | None:
    for k in keys:
        v = p.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0:
            return float(v)
    return None


def features(part_type: str, p: dict) -> dict[str, Any]:
    """Process-independent measurements read from the parameters. Missing ones are absent."""
    out: dict[str, Any] = {}
    wall = _f(p, "wall_thickness_mm")
    thickness = _f(p, "thickness_mm")
    if part_type == "tube" and wall is None:
        wall = 2.0
    if part_type in ("spacer", "washer") and _f(p, "outer_diameter_mm") and _f(p, "inner_diameter_mm"):
        wall = (p["outer_diameter_mm"] - p["inner_diameter_mm"]) / 2
    if part_type == "hex_standoff" and _f(p, "across_flats_mm") and p.get("inner_diameter_mm") is not None:
        wall = (p["across_flats_mm"] - p["inner_diameter_mm"]) / 2
    if part_type == "flange" and _f(p, "outer_diameter_mm") and p.get("inner_diameter_mm") is not None:
        wall = (p["outer_diameter_mm"] - p["inner_diameter_mm"]) / 2
    if part_type in ("l_bracket", "t_bracket", "flat_plate", "angle", "structural_beam", "channel_bracket"):
        wall = wall or thickness
    if wall:
        out["min_wall_mm"] = wall
    if thickness:
        out["thickness_mm"] = thickness
    hole = _f(p, "hole_diameter_mm", "mount_hole_diameter_mm")
    if hole and (p.get("hole_count", p.get("mount_hole_count", 1)) or 0) > 0:
        out["hole_diameter_mm"] = hole
        depth = thickness or wall
        if part_type == "flange" and thickness:
            depth = thickness
        if depth:
            out["hole_depth_ratio"] = round(depth / hole, 2)
    dims = [v for k in ("length_mm", "width_mm", "height_mm", "outer_diameter_mm", "diameter_mm", "leg1_mm", "leg2_mm",
                        "cap_width_mm", "stem_height_mm", "base_radius_mm") if (v := _f(p, k))]
    if _f(p, "base_radius_mm"):
        dims = [d * 2 if d == p["base_radius_mm"] else d for d in dims]
    if dims:
        out["largest_dimension_mm"] = max(dims)
    if part_type in ("shaft", "spacer", "tube") and (_f(p, "diameter_mm", "outer_diameter_mm") and _f(p, "length_mm")):
        out["slenderness"] = round(p["length_mm"] / _f(p, "diameter_mm", "outer_diameter_mm"), 2)
    if part_type in ("l_bracket", "flat_plate") and _f(p, "width_mm") and thickness:
        out["width_to_thickness"] = round(p["width_mm"] / thickness, 1)
    fillet = _f(p, "fillet_radius_mm")
    if fillet:
        out["fillet_radius_mm"] = fillet
    return out


def _finding(rule, severity, message, value=None, limit=None):
    return {"rule": rule, "severity": severity, "message": message, "value": value, "limit": limit}


def applicable(part_type: str) -> list[str]:
    procs = ["cnc_milling", "fdm_print"]
    if part_type in _ROTATIONAL:
        procs.insert(1, "turning")
    if part_type in _SHEET:
        procs.append("sheet_metal")
    return procs


def check(part_type: str, params: dict, process: str, material: str | None = None) -> list[dict]:
    """The problems found, worst rules first as the rules are written. Rules that passed are not listed."""
    return _check_impl(part_type, params, process, material, None)


def check_with_passes(part_type: str, params: dict, process: str, material: str | None = None) -> tuple[list[dict], list[dict]]:
    """(findings, passed). `passed` lists every rule that was evaluated and held, with the value and
    the limit it was held to, so a report can say what is fine as well as what is not."""
    passed: list[dict] = []
    return _check_impl(part_type, params, process, material, passed), passed


def _check_impl(part_type: str, params: dict, process: str, material: str | None, passes: list[dict] | None) -> list[dict]:
    if process not in ALL_PROCESSES:
        raise ValueError(f"process must be one of {', '.join(ALL_PROCESSES)}, got {process!r}")
    mat = materials.lookup(material)
    ft = features(part_type, params)
    out: list[dict] = []
    wall, big = ft.get("min_wall_mm"), ft.get("largest_dimension_mm")

    def held(rule: str, message: str, value=None, limit=None) -> None:
        if passes is not None:
            passes.append(_finding(rule, "ok", message, value, limit))

    if process in EXTRA_PROCESSES:
        _extra_rules(process, part_type, ft, mat, out, held)
        return out
    if process not in applicable(part_type):
        return [_finding("process_fit", "error", f"A {part_type.replace('_', ' ')} is not normally made by {process.replace('_', ' ')}.")]
    if mat is not None:
        before = len(out)
        if process == "fdm_print" and mat.category == "metal":
            out.append(_finding("material_fit", "error", f"{mat.key} cannot be printed on a desktop FDM printer."))
        if process == "sheet_metal" and mat.category != "metal":
            out.append(_finding("material_fit", "error", f"Sheet metal work needs a metal, got {mat.key}."))
        if process in ("cnc_milling", "turning") and mat.category == "wood":
            out.append(_finding("material_fit", "info", "Wood is machined on a router, not a metal mill."))
        if len(out) == before:
            held("material_fit", f"{mat.key} suits this process.")

    if process in ("cnc_milling", "turning"):
        limit = 1.5 if mat and mat.category == "polymer" else 0.8
        if wall is not None and wall < limit:
            out.append(_finding("min_wall", "warning" if wall >= limit / 2 else "error",
                                f"A {wall:g} mm wall will chatter or deflect when machined. Aim for {limit:g} mm or more.", wall, limit))
        elif wall is not None:
            held("min_wall", f"The thinnest wall is {wall:g} mm, above the {limit:g} mm that machines cleanly.", wall, limit)
        hole = ft.get("hole_diameter_mm")
        if hole is not None and hole < 1.0:
            out.append(_finding("min_hole", "warning", f"A {hole:g} mm hole needs a micro drill and breaks easily.", hole, 1.0))
        elif hole is not None:
            held("min_hole", f"The {hole:g} mm hole is a normal drill size.", hole, 1.0)
        ratio = ft.get("hole_depth_ratio")
        if ratio is not None and ratio > 10:
            out.append(_finding("hole_depth", "error", f"A hole {ratio:g} times deeper than its diameter needs gun drilling.", ratio, 10))
        elif ratio is not None and ratio > 5:
            out.append(_finding("hole_depth", "warning", f"A hole {ratio:g} times deeper than its diameter needs peck drilling.", ratio, 5))
        elif ratio is not None:
            held("hole_depth", f"The hole is {ratio:g} times deeper than wide, which a standard drill reaches.", ratio, 5)
        if big is not None and big > 500:
            out.append(_finding("work_envelope", "warning", f"At {big:g} mm this exceeds a typical 500 mm mill travel.", big, 500))
        elif big is not None:
            held("work_envelope", f"The longest side is {big:g} mm, inside a typical 500 mm mill travel.", big, 500)
        wt = ft.get("width_to_thickness")
        if process == "cnc_milling" and wt is not None and wt > 50:
            out.append(_finding("flatness", "warning", f"A plate {wt:g} times wider than it is thick tends to warp as material is removed.", wt, 50))
        elif process == "cnc_milling" and wt is not None:
            held("flatness", f"The plate is {wt:g} times wider than thick, which holds flat while machining.", wt, 50)
    if process == "turning":
        s = ft.get("slenderness")
        if s is not None and s > 20:
            out.append(_finding("slenderness", "error", f"Length is {s:g} times the diameter. Too slender to turn without bending.", s, 20))
        elif s is not None and s > 10:
            out.append(_finding("slenderness", "warning", f"Length is {s:g} times the diameter and needs a steady rest or tailstock support.", s, 10))
        elif s is not None:
            held("slenderness", f"Length is {s:g} times the diameter, which turns without extra support.", s, 10)

    if process == "fdm_print":
        if wall is not None and wall < 0.8:
            out.append(_finding("min_wall", "error", f"A {wall:g} mm wall is thinner than one 0.4 mm nozzle pass can hold reliably.", wall, 0.8))
        elif wall is not None and wall < 1.2:
            out.append(_finding("min_wall", "warning", f"A {wall:g} mm wall is only two perimeters. 1.2 mm or more is sturdier.", wall, 1.2))
        elif wall is not None:
            held("min_wall", f"The thinnest wall is {wall:g} mm, at least three perimeters of a 0.4 mm nozzle.", wall, 1.2)
        hole = ft.get("hole_diameter_mm")
        if hole is not None and hole < 2.0:
            out.append(_finding("min_hole", "warning", f"A {hole:g} mm hole usually prints undersized or closed. Plan to drill it.", hole, 2.0))
        elif hole is not None:
            held("min_hole", f"The {hole:g} mm hole is large enough to print open.", hole, 2.0)
        if big is not None and big > 220:
            out.append(_finding("build_volume", "warning", f"At {big:g} mm this is larger than a typical 220 mm print bed. It must be split.", big, 220))
        elif big is not None:
            held("build_volume", f"The longest side is {big:g} mm, inside a typical 220 mm print bed.", big, 220)
        s = ft.get("slenderness")
        if s is not None and s > 8:
            out.append(_finding("slenderness", "warning", f"A part {s:g} times longer than wide is fragile across its layers.", s, 8))
        elif s is not None:
            held("slenderness", f"The part is {s:g} times longer than wide, which is sturdy enough across layers.", s, 8)
        if mat is not None and mat.category == "polymer" and mat.note:
            out.append(_finding("anisotropy", "info", f"{mat.key}: {mat.note}."))

    if process == "sheet_metal":
        t = ft.get("thickness_mm")
        if t is None:
            out.append(_finding("thickness", "info", "No thickness given, so sheet-metal rules were not checked."))
        else:
            if not 0.5 <= t <= 6.0:
                out.append(_finding("thickness", "warning", f"{t:g} mm is outside the 0.5 to 6 mm range most shops bend.", t, 6.0 if t > 6 else 0.5))
            else:
                held("thickness", f"{t:g} mm sheet is inside the 0.5 to 6 mm range most shops bend.", t, 6.0)
            hole = ft.get("hole_diameter_mm")
            if hole is not None and hole < t:
                out.append(_finding("min_hole", "warning", f"A {hole:g} mm hole is smaller than the {t:g} mm sheet. Punching it will be hard.", hole, t))
            elif hole is not None:
                held("min_hole", f"The {hole:g} mm hole is at least as wide as the {t:g} mm sheet.", hole, t)
            fillet = ft.get("fillet_radius_mm")
            if fillet is not None and fillet < t:
                out.append(_finding("bend_radius", "warning", f"A {fillet:g} mm bend radius is tighter than the sheet thickness {t:g} mm and risks cracking.", fillet, t))
            elif fillet is not None:
                held("bend_radius", f"The {fillet:g} mm bend radius is at least the sheet thickness.", fillet, t)
            if part_type == "l_bracket":
                leg = min(v for v in (_f(params, "width_mm"), _f(params, "height_mm")) if v) if (_f(params, "width_mm") or _f(params, "height_mm")) else None
                if leg is not None and leg < 4 * t:
                    out.append(_finding("flange_length", "warning", f"A {leg:g} mm leg is shorter than 4 times the thickness, which a press brake cannot grip.", leg, 4 * t))
                elif leg is not None:
                    held("flange_length", f"The shortest leg is {leg:g} mm, long enough for a press brake to grip.", leg, 4 * t)
    return out


def _extra_rules(process: str, part_type: str, ft: dict, mat, out: list[dict], held) -> None:
    """Injection molding and resin printing. Same rule of thumb spirit as above: typical values."""
    wall, big = ft.get("min_wall_mm"), ft.get("largest_dimension_mm")
    hole, ratio = ft.get("hole_diameter_mm"), ft.get("hole_depth_ratio")

    if mat is not None:
        if process == "injection_molding" and mat.category != "polymer":
            what = "Wood" if mat.category == "wood" else f"{mat.key}"
            out.append(_finding("material_fit", "error", f"{what} is not injection molded. That needs a thermoplastic. Metal parts use die casting or metal injection molding."))
        elif process == "resin_print" and mat.category != "polymer":
            what = "Wood" if mat.category == "wood" else f"{mat.key}"
            out.append(_finding("material_fit", "error", f"{what} cannot be printed in resin. Resin printers cure a photopolymer."))
        else:
            held("material_fit", f"{mat.key} suits this process.")

    if process == "injection_molding":
        if wall is not None:
            if wall < 0.5:
                out.append(_finding("min_wall", "error", f"A {wall:g} mm wall will not fill. Most plastics need 0.8 mm or more.", wall, 0.8))
            elif wall < 1.0:
                out.append(_finding("min_wall", "warning", f"A {wall:g} mm wall is hard to fill on all but the thinnest-flowing plastics. Aim for 1 to 3 mm.", wall, 1.0))
            else:
                held("min_wall", f"The thinnest wall is {wall:g} mm, above the 1 mm that fills reliably.", wall, 1.0)
            if wall > 4.0:
                out.append(_finding("thick_section", "warning", f"A {wall:g} mm section will sink, void and cool slowly. Core it out to 2 to 3 mm walls with ribs.", wall, 4.0))
            else:
                held("thick_section", f"No section is thicker than {max(wall, 0):g} mm, which cools evenly.", wall, 4.0)
        if hole is not None and hole < 1.0:
            out.append(_finding("min_hole", "warning", f"A {hole:g} mm hole needs a core pin that thin, which bends or breaks in the tool.", hole, 1.0))
        elif hole is not None:
            held("min_hole", f"The {hole:g} mm hole can be formed by a normal core pin.", hole, 1.0)
        if ratio is not None and ratio > 10:
            out.append(_finding("hole_depth", "error", f"A hole {ratio:g} times deeper than wide needs a core pin that will deflect and snap.", ratio, 10))
        elif ratio is not None and ratio > 4:
            out.append(_finding("hole_depth", "warning", f"A hole {ratio:g} times deeper than wide needs a long core pin that can deflect. 4 times is a safe limit.", ratio, 4))
        elif ratio is not None:
            held("hole_depth", f"The hole is {ratio:g} times deeper than wide, which a core pin holds.", ratio, 4)
        fillet = ft.get("fillet_radius_mm")
        if fillet is not None and wall is not None and fillet < 0.5 * wall:
            out.append(_finding("fillet", "warning", f"A {fillet:g} mm radius on a {wall:g} mm wall concentrates stress. Use at least half the wall thickness.", fillet, round(0.5 * wall, 2)))
        elif fillet is not None and wall is not None:
            held("fillet", f"The {fillet:g} mm radius is at least half the wall thickness.", fillet, round(0.5 * wall, 2))
        if big is not None and big > 500:
            out.append(_finding("tool_size", "warning", f"At {big:g} mm this needs a large press and an expensive tool.", big, 500))
        elif big is not None:
            held("tool_size", f"The longest side is {big:g} mm, which fits a mid-size press.", big, 500)

    if process == "resin_print":
        if wall is not None:
            if wall < 0.4:
                out.append(_finding("min_wall", "error", f"A {wall:g} mm wall is thinner than most resins print reliably.", wall, 0.4))
            elif wall < 0.8:
                out.append(_finding("min_wall", "warning", f"A {wall:g} mm wall is brittle and may break while it is cleaned or supported. Aim for 0.8 mm or more.", wall, 0.8))
            else:
                held("min_wall", f"The thinnest wall is {wall:g} mm, above the 0.8 mm that prints sturdily.", wall, 0.8)
        if hole is not None and hole < 0.5:
            out.append(_finding("min_hole", "warning", f"A {hole:g} mm hole tends to clog with uncured resin or close up.", hole, 0.5))
        elif hole is not None:
            held("min_hole", f"The {hole:g} mm hole is wide enough to stay open.", hole, 0.5)
        if big is not None and big > 185:
            out.append(_finding("build_volume", "warning", f"At {big:g} mm this is larger than a typical 185 mm resin build height. It must be split.", big, 185))
        elif big is not None:
            held("build_volume", f"The longest side is {big:g} mm, inside a typical 185 mm resin build volume.", big, 185)
        s = ft.get("slenderness")
        if s is not None and s > 10:
            out.append(_finding("slenderness", "warning", f"A part {s:g} times longer than wide tends to warp and needs many supports.", s, 10))
        elif s is not None:
            held("slenderness", f"The part is {s:g} times longer than wide, which holds its shape.", s, 10)


def best_fit(part_type: str, params: dict, material: str | None = None) -> list[dict]:
    """Applicable processes ranked by (errors, warnings), fewest first."""
    ranked = []
    for proc in applicable(part_type):
        findings = check(part_type, params, proc, material)
        ranked.append({"process": proc, "errors": sum(f["severity"] == "error" for f in findings),
                       "warnings": sum(f["severity"] == "warning" for f in findings), "findings": findings})
    return sorted(ranked, key=lambda r: (r["errors"], r["warnings"], PROCESSES.index(r["process"])))
