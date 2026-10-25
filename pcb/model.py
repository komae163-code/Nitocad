"""Shared data model for the PCB pipeline (stdlib only, easy to test)."""
from __future__ import annotations

from dataclasses import dataclass, field

# Fab rules the whole pipeline designs to (typical 2-layer low-cost fab).
RULES = {
    "clearance": 0.20,
    "track_width": 0.25,
    "via_dia": 0.60,
    "via_drill": 0.30,
    "min_annular": 0.13,
    "edge_clearance": 0.30,
    "min_hole_gap": 0.25,
    "board_margin": 3.0,
}


@dataclass
class Pad:
    number: str
    x: float          # relative to footprint origin, rotation 0
    y: float
    w: float
    h: float
    shape: str = "rect"      # rect | roundrect | circle | oval
    kind: str = "smd"        # smd | thru | np (non-plated mounting hole)
    drill: float = 0.0
    layers: tuple = ("F.Cu",)


@dataclass
class Footprint:
    lib_id: str
    pads: list
    court: tuple             # (x0, y0, x1, y1) relative, rotation 0
    sexpr: list | None = None   # parsed .kicad_mod when it came from KiCad's library
    source: str = "builtin"      # builtin | kicad-library


@dataclass
class Component:
    ref: str
    value: str
    lib_id: str
    block_id: str
    pin_nets: dict           # pad number -> net name (missing = unconnected)
    x: float = 0.0
    y: float = 0.0
    rot: int = 0
    mpn: str | None = None
    review: bool = False
    fp: Footprint | None = None
    near: tuple | None = None   # (parent ref, side, along_mm) - resolved by the placer


@dataclass
class Track:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str
    width: float
    net: str


@dataclass
class Via:
    x: float
    y: float
    dia: float
    drill: float
    net: str


@dataclass
class Design:
    title: str
    components: list = field(default_factory=list)
    nets: list = field(default_factory=list)
    board: tuple = (0.0, 0.0, 50.0, 30.0)
    tracks: list = field(default_factory=list)
    vias: list = field(default_factory=list)
    ground_mode: str = "plane"       # plane | traces
    warnings: list = field(default_factory=list)
    review_items: list = field(default_factory=list)
    unrouted: list = field(default_factory=list)
    net_current: dict = field(default_factory=dict)   # net -> amps the net must carry (from blocks)
    net_width: dict = field(default_factory=dict)     # net -> track width mm, only nets wider than default


def rotate(x: float, y: float, rot: int) -> tuple:
    """KiCad convention (y down, positive angle = counter-clockwise on screen)."""
    r = rot % 360
    if r == 0:
        return x, y
    if r == 90:
        return y, -x
    if r == 180:
        return -x, -y
    if r == 270:
        return -y, x
    raise ValueError(f"only 0/90/180/270 rotations supported, got {rot}")


def abs_pads(c: Component):
    """Pads of a placed component in board coordinates:
    yields (pad, cx, cy, w, h)."""
    out = []
    for p in c.fp.pads:
        rx, ry = rotate(p.x, p.y, c.rot)
        w, h = (p.w, p.h) if c.rot % 180 == 0 else (p.h, p.w)
        out.append((p, c.x + rx, c.y + ry, w, h))
    return out


def court_bbox(c: Component) -> tuple:
    x0, y0, x1, y1 = c.fp.court
    pts = [rotate(x0, y0, c.rot), rotate(x1, y1, c.rot)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (c.x + min(xs), c.y + min(ys), c.x + max(xs), c.y + max(ys))


MAX_TRACK_MM = 3.0


def width_for_current(amps: float) -> float:
    """Track width for an external 1 oz copper trace carrying `amps` at a 10 C rise
    (IPC-2221), with 25 % margin, rounded up to 0.05 mm and never below the default width.
    Our own pour/trace limits: capped at MAX_TRACK_MM - beyond that the design needs a pour."""
    import math
    if amps <= 0:
        return RULES["track_width"]
    area_mil2 = (amps / (0.048 * 10 ** 0.44)) ** (1 / 0.725)
    mm = area_mil2 / 1.378 * 0.0254 * 1.25
    mm = math.ceil(mm / 0.05 - 1e-9) * 0.05
    return round(min(max(mm, RULES["track_width"]), MAX_TRACK_MM), 2)
