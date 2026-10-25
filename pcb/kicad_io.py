"""Write KiCad board/project files from a routed Design.

Footprints from KiCad's own library are embedded verbatim (only the
placement, net assignment and reference/value text are changed). Built-in
footprints have their rotation baked into pad coordinates, so the file never
depends on how a given KiCad version stores pad angles.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

from .model import RULES, Design, Footprint, court_bbox, rotate
from .sexpr import Q, dump, find, find_all, parse

DEFAULT_VERSION = 20221018


def _u() -> str:
    return str(uuid.uuid4())


def _board_version(design: Design) -> int:
    v = DEFAULT_VERSION
    for c in design.components:
        if c.fp and c.fp.sexpr:
            ver = find(c.fp.sexpr, "version")
            if ver:
                try:
                    v = max(v, int(ver[1]))
                except ValueError:
                    pass
    return v


def _fmt_ver(ver: int) -> str:
    return ">=8" if ver >= 20240108 else "7"


def _stroke(ver, width):
    return [["stroke", ["width", width], ["type", "solid"]]] if ver >= 20240108 else [["width", width]]


def _effects(size=0.8):
    return ["effects", ["font", ["size", size, size], ["thickness", 0.12]]]


def _text_nodes(ver, ref, value, ty_ref, ty_val):
    if ver >= 20240108:
        return [
            ["property", Q("Reference"), Q(ref), ["at", 0, ty_ref, 0], ["layer", Q("F.SilkS")], _effects()],
            ["property", Q("Value"), Q(value), ["at", 0, ty_val, 0], ["layer", Q("F.Fab")], _effects()],
        ]
    return [
        ["fp_text", "reference", Q(ref), ["at", 0, ty_ref], ["layer", Q("F.SilkS")], _effects()],
        ["fp_text", "value", Q(value), ["at", 0, ty_val], ["layer", Q("F.Fab")], _effects()],
    ]


def _builtin_footprint(c, ver, net_index) -> list:
    fp = c.fp
    thru = any(p.kind != "smd" for p in fp.pads)
    node = ["footprint", Q(c.lib_id), ["layer", Q("F.Cu")], ["uuid", Q(_u())] if ver >= 20240108 else ["tstamp", Q(_u())],
            ["at", c.x, c.y], ["attr", "through_hole" if thru else "smd"]]
    x0, y0, x1, y1 = (0, 0, 0, 0)
    # courtyard after baking the rotation
    cx0, cy0, cx1, cy1 = court_bbox(type("T", (), {"fp": fp, "rot": c.rot, "x": 0.0, "y": 0.0})())
    node += _text_nodes(ver, c.ref, c.value, round(cy0 - 0.9, 3), round(cy1 + 0.9, 3))
    for a, b in (((cx0, cy0), (cx1, cy0)), ((cx1, cy0), (cx1, cy1)), ((cx1, cy1), (cx0, cy1)), ((cx0, cy1), (cx0, cy0))):
        node.append(["fp_line", ["start", a[0], a[1]], ["end", b[0], b[1]], *_stroke(ver, 0.05), ["layer", Q("F.CrtYd")]])
    for p in fp.pads:
        rx, ry = rotate(p.x, p.y, c.rot)
        w, h = (p.w, p.h) if c.rot % 180 == 0 else (p.h, p.w)
        if p.kind == "np":
            node.append(["pad", Q(p.number), "np_thru_hole", "circle", ["at", rx, ry], ["size", p.drill, p.drill],
                         ["drill", p.drill], ["layers", Q("*.Cu"), Q("*.Mask")]])
            continue
        net = c.pin_nets.get(p.number)
        pad = ["pad", Q(p.number), "smd" if p.kind == "smd" else "thru_hole", p.shape, ["at", rx, ry], ["size", w, h]]
        if p.kind == "thru":
            pad.append(["drill", p.drill])
            pad.append(["layers", Q("*.Cu"), Q("*.Mask")])
        else:
            pad.append(["layers", Q("F.Cu"), Q("F.Paste"), Q("F.Mask")])
        if p.shape == "roundrect":
            pad.append(["roundrect_rratio", 0.25])
        if net:
            pad.append(["net", net_index[net], Q(net)])
        node.append(pad)
    return node


def _library_footprint(c, ver, net_index) -> list:
    src = copy.deepcopy(c.fp.sexpr)
    node = ["footprint", Q(c.lib_id)]
    for child in src[2:] if len(src) > 2 and not isinstance(src[1], list) else src[1:]:
        if isinstance(child, list) and child and child[0] in ("version", "generator", "generator_version"):
            continue
        node.append(child)
    # placement
    at = ["at", c.x, c.y] + ([c.rot] if c.rot else [])
    layer_idx = next((i for i, ch in enumerate(node) if isinstance(ch, list) and ch and ch[0] == "layer"), None)
    if layer_idx is None:
        node.insert(2, ["layer", Q("F.Cu")])
        layer_idx = 2
    node.insert(layer_idx + 1, at)
    for ch in node:
        if not isinstance(ch, list) or not ch:
            continue
        if ch[0] == "fp_text" and len(ch) > 2 and ch[1] == "reference":
            ch[2] = Q(c.ref)
        elif ch[0] == "fp_text" and len(ch) > 2 and ch[1] == "value":
            ch[2] = Q(c.value)
        elif ch[0] == "property" and len(ch) > 2 and ch[1] == "Reference":
            ch[2] = Q(c.ref)
        elif ch[0] == "property" and len(ch) > 2 and ch[1] == "Value":
            ch[2] = Q(c.value)
        elif ch[0] == "pad":
            pat = find(ch, "at")
            if pat is not None and c.rot:
                ang = float(pat[3]) if len(pat) > 3 else 0.0
                new = (ang + c.rot) % 360
                if len(pat) > 3:
                    pat[3] = new
                else:
                    pat.append(new)
            net = c.pin_nets.get(str(ch[1]))
            for old in find_all(ch, "net"):
                ch.remove(old)
            if net:
                ch.append(["net", net_index[net], Q(net)])
    return node


def build_board_tree(design: Design) -> tuple[list, int]:
    ver = _board_version(design)
    net_index = {n: i + 1 for i, n in enumerate(design.nets)}
    root = ["kicad_pcb", ["version", ver]]
    if ver >= 20240108:
        root += [["generator", Q("nitocad")], ["generator_version", Q("1.0")]]
    else:
        root += [["generator", "nitocad"]]
    root += [
        ["general", ["thickness", 1.6]],
        ["paper", Q("A4")],
        ["layers",
         [0, Q("F.Cu"), "signal"], [31, Q("B.Cu"), "signal"],
         [32, Q("B.Adhes"), "user", Q("B.Adhesive")], [33, Q("F.Adhes"), "user", Q("F.Adhesive")],
         [34, Q("B.Paste"), "user"], [35, Q("F.Paste"), "user"],
         [36, Q("B.SilkS"), "user", Q("B.Silkscreen")], [37, Q("F.SilkS"), "user", Q("F.Silkscreen")],
         [38, Q("B.Mask"), "user"], [39, Q("F.Mask"), "user"],
         [40, Q("Dwgs.User"), "user", Q("User.Drawings")], [41, Q("Cmts.User"), "user", Q("User.Comments")],
         [44, Q("Edge.Cuts"), "user"], [45, Q("Margin"), "user"],
         [46, Q("B.CrtYd"), "user", Q("B.Courtyard")], [47, Q("F.CrtYd"), "user", Q("F.Courtyard")],
         [48, Q("B.Fab"), "user"], [49, Q("F.Fab"), "user"]],
        ["setup", ["pad_to_mask_clearance", 0.05]],
        ["net", 0, Q("")],
    ]
    for n in design.nets:
        root.append(["net", net_index[n], Q(n)])
    for c in design.components:
        root.append(_library_footprint(c, ver, net_index) if (c.fp.sexpr and c.fp.source == "kicad-library")
                    else _builtin_footprint(c, ver, net_index))
    x0, y0, x1, y1 = design.board
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        root.append(["gr_line", ["start", a[0], a[1]], ["end", b[0], b[1]], *_stroke(ver, 0.1), ["layer", Q("Edge.Cuts")]])
    for t in design.tracks:
        root.append(["segment", ["start", t.x1, t.y1], ["end", t.x2, t.y2], ["width", t.width],
                     ["layer", Q(t.layer)], ["net", net_index[t.net]]])
    for v in design.vias:
        root.append(["via", ["at", v.x, v.y], ["size", v.dia], ["drill", v.drill],
                     ["layers", Q("F.Cu"), Q("B.Cu")], ["net", net_index[v.net]]])
    if design.ground_mode == "plane" and "GND" in net_index:
        i = 0.25
        pts = [["xy", x0 + i, y0 + i], ["xy", x1 - i, y0 + i], ["xy", x1 - i, y1 - i], ["xy", x0 + i, y1 - i]]
        root.append(["zone", ["net", net_index["GND"]], ["net_name", Q("GND")], ["layer", Q("B.Cu")],
                     ["hatch", "edge", 0.5], ["connect_pads", ["clearance", RULES["clearance"]]],
                     ["min_thickness", 0.2],
                     ["fill", "yes", ["thermal_gap", 0.3], ["thermal_bridge_width", 0.4]],
                     ["polygon", ["pts", *pts]]])
    return root, ver


def write_board(design: Design, path: Path) -> dict:
    tree, ver = build_board_tree(design)
    text = dump(tree) + "\n"
    parse(text)    # round-trip sanity: balanced and well formed
    path.write_text(text, encoding="utf-8")
    return {"format": _fmt_ver(ver), "version": ver}


def write_project(path: Path) -> None:
    """Minimal .kicad_pro carrying the fab rules so KiCad's DRC checks what we designed to."""
    r = RULES
    pro = {
        "board": {"design_settings": {"rules": {
            "min_clearance": r["clearance"], "min_track_width": 0.2,
            "min_via_diameter": 0.5, "min_through_hole_diameter": 0.3,
            "min_via_annular_width": r["min_annular"], "min_copper_edge_clearance": r["edge_clearance"],
            "min_hole_to_hole": r["min_hole_gap"]}}},
        "net_settings": {"classes": [{"name": "Default", "clearance": r["clearance"],
                                       "track_width": r["track_width"], "via_diameter": r["via_dia"],
                                       "via_drill": r["via_drill"]}], "meta": {"version": 3}},
        "meta": {"filename": path.name, "version": 1},
    }
    path.write_text(json.dumps(pro, indent=2), encoding="utf-8")
