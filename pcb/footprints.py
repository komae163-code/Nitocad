"""
Footprint resolver.

Preference order:
  1. KiCad's own official footprint library, if installed (PCB_FOOTPRINT_DIR
     env var, or auto-discovered). Those footprints are community-reviewed,
     so they are the right source of truth for real parts.
  2. A small built-in set for the common parts we ship blocks for, so the
     pipeline (and its tests) work with no KiCad installed.

Anything else raises FootprintError - we never invent geometry.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .model import Footprint, Pad
from .sexpr import find, find_all, parse


class FootprintError(Exception):
    pass


_SEARCH_DIRS = (
    "/usr/share/kicad/footprints",
    "/usr/local/share/kicad/footprints",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints",
    "C:/Program Files/KiCad/9.0/share/kicad/footprints",
    "C:/Program Files/KiCad/8.0/share/kicad/footprints",
)


def library_dir() -> Path | None:
    env = os.environ.get("PCB_FOOTPRINT_DIR")
    cands = [env] if env else []
    cands += list(_SEARCH_DIRS)
    for c in cands:
        if c and Path(c).is_dir():
            return Path(c)
    return None


# ---------------------------------------------------------------- built-ins
def _chip(lib_id: str) -> Footprint:
    return Footprint(
        lib_id,
        [Pad("1", -0.825, 0, 0.8, 0.95, "roundrect"), Pad("2", 0.825, 0, 0.8, 0.95, "roundrect")],
        (-1.48, -0.73, 1.48, 0.73),
    )


def _sot23_5(lib_id: str) -> Footprint:
    return Footprint(
        lib_id,
        [
            Pad("1", -1.1375, 0.95, 1.325, 0.6, "roundrect"),
            Pad("2", -1.1375, 0.0, 1.325, 0.6, "roundrect"),
            Pad("3", -1.1375, -0.95, 1.325, 0.6, "roundrect"),
            Pad("4", 1.1375, -0.95, 1.325, 0.6, "roundrect"),
            Pad("5", 1.1375, 0.95, 1.325, 0.6, "roundrect"),
        ],
        (-2.05, -1.6, 2.05, 1.6),
    )


def _header(lib_id: str, n: int, pitch: float, pad: float, drill: float) -> Footprint:
    pads = [
        Pad(str(i + 1), 0.0, i * pitch, pad, pad, "rect" if i == 0 else "circle",
            "thru", drill, ("F.Cu", "B.Cu"))
        for i in range(n)
    ]
    half = pad / 2 + 0.3
    return Footprint(lib_id, pads, (-half, -half, half, (n - 1) * pitch + half))


def _terminal2(lib_id: str) -> Footprint:
    pads = [
        Pad("1", 0.0, 0.0, 2.6, 2.6, "rect", "thru", 1.4, ("F.Cu", "B.Cu")),
        Pad("2", 0.0, 5.08, 2.6, 2.6, "circle", "thru", 1.4, ("F.Cu", "B.Cu")),
    ]
    return Footprint(lib_id, pads, (-2.0, -2.0, 2.0, 7.1))


def _sot23(lib_id: str) -> Footprint:
    # KiCad Package_TO_SOT_SMD:SOT-23 - pin 1 bottom-left, pin 2 top-left, pin 3 right.
    return Footprint(
        lib_id,
        [
            Pad("1", -0.9375, 0.95, 1.475, 0.6, "roundrect"),
            Pad("2", -0.9375, -0.95, 1.475, 0.6, "roundrect"),
            Pad("3", 0.9375, 0.0, 1.475, 0.6, "roundrect"),
        ],
        (-1.8, -1.45, 1.8, 1.45),
    )


def _soic8(lib_id: str) -> Footprint:
    # KiCad Package_SO:SOIC-8_3.9x4.9mm_P1.27mm - pin 1 top-left, counter-clockwise.
    ys = [-1.905, -0.635, 0.635, 1.905]
    pads = [Pad(str(i + 1), -2.475, y, 1.95, 0.6, "roundrect") for i, y in enumerate(ys)]
    pads += [Pad(str(5 + i), 2.475, y, 1.95, 0.6, "roundrect") for i, y in enumerate(reversed(ys))]
    return Footprint(lib_id, pads, (-3.45, -3.2, 3.45, 3.2))


def _header2(lib_id: str, rows: int, pitch: float, pad: float, drill: float) -> Footprint:
    # KiCad numbering for 2xN vertical headers: odd pins left column, even pins right, row by row.
    pads = []
    for r in range(rows):
        for c in range(2):
            n = r * 2 + c + 1
            pads.append(Pad(str(n), c * pitch, r * pitch, pad, pad,
                            "rect" if n == 1 else "circle", "thru", drill, ("F.Cu", "B.Cu")))
    half = pad / 2 + 0.3
    return Footprint(lib_id, pads, (-half, -half, pitch + half, (rows - 1) * pitch + half))


_HDR2 = re.compile(r"^Connector_PinHeader_2\.54mm:PinHeader_2x(\d{2})_P2\.54mm_Vertical$")
_HDR = re.compile(r"^Connector_PinHeader_2\.54mm:PinHeader_1x(\d{2})_P2\.54mm_Vertical$")


def _builtin(lib_id: str) -> Footprint | None:
    if lib_id in ("Resistor_SMD:R_0603_1608Metric", "Capacitor_SMD:C_0603_1608Metric",
                  "LED_SMD:LED_0603_1608Metric"):
        return _chip(lib_id)
    if lib_id == "Package_TO_SOT_SMD:SOT-23-5":
        return _sot23_5(lib_id)
    m = _HDR.match(lib_id)
    if m and 1 <= int(m.group(1)) <= 40:
        return _header(lib_id, int(m.group(1)), 2.54, 1.7, 1.0)
    m2 = _HDR2.match(lib_id)
    if m2 and 1 <= int(m2.group(1)) <= 20:
        return _header2(lib_id, int(m2.group(1)), 2.54, 1.7, 1.0)
    if lib_id == "Package_TO_SOT_SMD:SOT-23":
        return _sot23(lib_id)
    if lib_id == "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm":
        return _soic8(lib_id)
    if lib_id == "nitocad:TerminalBlock_1x02_P5.08mm":
        return _terminal2(lib_id)
    return None


# -------------------------------------------------------- KiCad .kicad_mod
def _f(x) -> float:
    return float(x)


def _from_sexpr(lib_id: str, tree: list) -> Footprint:
    pads = []
    for p in find_all(tree, "pad"):
        number, kind, shape = str(p[1]), str(p[2]), str(p[3])
        at = find(p, "at")
        size = find(p, "size")
        if not at or not size:
            continue
        layers_node = find(p, "layers")
        layers = tuple(str(x) for x in (layers_node[1:] if layers_node else []))
        copper = tuple(l for l in layers if l.endswith(".Cu"))
        if "*.Cu" in layers:
            copper = ("F.Cu", "B.Cu")
        drill = 0.0
        d = find(p, "drill")
        if d:
            vals = [x for x in d[1:] if re.fullmatch(r"-?\d+(\.\d+)?", str(x))]
            drill = max(float(v) for v in vals) if vals else 0.0
        if kind == "np_thru_hole":
            k = "np"
            copper = ("F.Cu", "B.Cu")
        elif kind == "thru_hole":
            k = "thru"
            copper = copper or ("F.Cu", "B.Cu")
        else:
            k = "smd"
            copper = copper or ("F.Cu",)
        pads.append(Pad(number, _f(at[1]), _f(at[2]), _f(size[1]), _f(size[2]),
                        shape if shape in ("rect", "roundrect", "circle", "oval") else "rect",
                        k, drill, copper))
    if not pads:
        raise FootprintError(f"{lib_id}: no pads found in library footprint")

    xs, ys = [], []
    for node in tree:
        if not isinstance(node, list) or not node:
            continue
        if node[0] in ("fp_line", "fp_rect", "fp_circle", "fp_poly", "fp_arc"):
            lay = find(node, "layer")
            if not lay or str(lay[1]) != "F.CrtYd":
                continue
            for key in ("start", "end", "center", "mid"):
                s = find(node, key)
                if s:
                    xs.append(_f(s[1]))
                    ys.append(_f(s[2]))
            pts = find(node, "pts")
            if pts:
                for xy in find_all(pts, "xy"):
                    xs.append(_f(xy[1]))
                    ys.append(_f(xy[2]))
    if xs:
        court = (min(xs), min(ys), max(xs), max(ys))
    else:
        court = (min(p.x - p.w / 2 for p in pads) - 0.25, min(p.y - p.h / 2 for p in pads) - 0.25,
                 max(p.x + p.w / 2 for p in pads) + 0.25, max(p.y + p.h / 2 for p in pads) + 0.25)
    return Footprint(lib_id, pads, court, sexpr=tree, source="kicad-library")


def resolve(lib_id: str) -> Footprint:
    if ":" not in lib_id:
        raise FootprintError(f"bad footprint id {lib_id!r} (expected Library:Name)")
    lib, name = lib_id.split(":", 1)
    root = library_dir()
    if root is not None:
        path = root / f"{lib}.pretty" / f"{name}.kicad_mod"
        if path.is_file():
            try:
                return _from_sexpr(lib_id, parse(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001 - fall back to builtin if we have one
                fb = _builtin(lib_id)
                if fb is not None:
                    return fb
                raise FootprintError(f"{lib_id}: could not read library footprint: {exc}") from exc
    fb = _builtin(lib_id)
    if fb is not None:
        return fb
    raise FootprintError(
        f"footprint {lib_id!r} is not available (KiCad footprint library not found "
        f"or part missing, and no built-in version exists)"
    )
