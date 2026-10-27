"""
A PCB as a 3D solid description - the bridge between the PCB pack and NitoCAD's CAD pipeline.

This module is pure Python (no CadQuery): it turns a placed design into a list of boxes. The
`pcb_board` CAD template (cad_templates/pcb.py) turns those boxes into a CadQuery solid, and from
there everything the CAD side already does works unchanged: free STL preview, STEP / IGES / DXF / PDF
export, credits, on-chain anchoring.

What the solid is, and is not: a 1.6 mm FR-4 slab with every part drawn as a block at its courtyard
size and a typical height. It is for fitting the board into an enclosure and judging size, not a
detailed component model. Copper, vias and holes are not modelled (the KiCad package has those).

Coordinates: millimetres, board centred on x = y = 0, underside at z = 0, +Y up (KiCad's Y axis points
down, so it is flipped here, which makes the top view match what KiCad shows).
"""
from __future__ import annotations

import re

from .blocks import BLOCKS_HARDWARE_PROVEN
from .compose import SpecError, compose
from .model import court_bbox
from .place import place
from .planner import PlanError, keyword_plan

BOARD_THICKNESS = 1.6
BODY_INSET = 0.2          # courtyard -> body outline
EMBED = 0.05              # parts sink this far into the board so the union fuses into one solid

_CP_RADIAL = re.compile(r"CP_Radial_D(\d+(?:\.\d+)?)mm")
_INDUCTOR_H = re.compile(r"_H(\d+(?:\.\d+)?)mm")

_PREFIX_HEIGHT = {"R": 0.55, "C": 1.0, "D": 1.0, "U": 1.5, "J": 8.0, "L": 4.0, "Q": 1.1}


def component_height_mm(c) -> float:
    """Typical body height above the board. Heuristic from the footprint name, then the reference
    prefix; it errs towards the real part so an enclosure designed around it is not too shallow."""
    lib = c.lib_id
    m = _CP_RADIAL.search(lib)
    if m:                                         # electrolytic: about 1.5x its diameter
        return round(float(m.group(1)) * 1.5, 2)
    if "TerminalBlock" in lib:
        return 10.0
    if "PinHeader" in lib or "PinSocket" in lib:
        return 8.5
    if "USB" in lib:
        return 3.3
    if "TO-263" in lib:
        return 4.6
    if lib.startswith("Inductor"):
        h = _INDUCTOR_H.search(lib)
        return float(h.group(1)) if h else (6.0 if "SRR1260" in lib else 4.0)
    if "SOIC-8" in lib:
        return 1.75
    if "TQFP" in lib:
        return 1.2
    if "SOT-23" in lib:
        return 1.1
    if "D_SMA" in lib:
        return 2.4
    if "LED" in lib:
        return 0.8
    if "C_0603" in lib:
        return 0.9
    if "R_0603" in lib:
        return 0.55
    return _PREFIX_HEIGHT.get(c.ref[:1], 2.0)


def design_from_params(params: dict):
    """(placed Design, notes) from a pcb_board part's parameters.

    `spec` (a validated block spec, stored when an AI planned it) wins; otherwise the free offline
    planner reads `description`. Raises ValueError with a plain-language reason when the board cannot
    be built, so the validator and the template can show it to the person."""
    spec = params.get("spec")
    notes: list[str] = []
    if not isinstance(spec, dict):
        desc = str(params.get("description") or "").strip()
        if not desc:
            raise ValueError("Describe the circuit board you want, for example \"5V board with a status LED\".")
        try:
            result = keyword_plan(desc)
        except PlanError as exc:
            raise ValueError(" ".join(exc.errors)[:400]) from exc
        spec, notes = result.spec, list(result.notes)
    try:
        design = compose(spec)
    except SpecError as exc:
        raise ValueError(" ".join(exc.errors)[:400]) from exc
    place(design)
    return design, notes


def design_warnings(design, notes: list[str]) -> list[str]:
    out = list(notes) + list(design.warnings) + list(design.review_items)
    if not BLOCKS_HARDWARE_PROVEN:
        out.append("These circuit blocks have not been built and tested on hardware yet. "
                   "Treat the board as a prototype and check pin-outs against the datasheets before ordering.")
    return out


def solid_boxes(design) -> list[dict]:
    """Boxes for the 3D solid, board first. Each: {name, cx, cy, sx, sy, z0, sz} in the CAD frame."""
    bx0, by0, bx1, by1 = design.board
    ox, oy = (bx0 + bx1) / 2, (by0 + by1) / 2
    boxes = [{"name": "board", "cx": 0.0, "cy": 0.0, "sx": round(bx1 - bx0, 3), "sy": round(by1 - by0, 3),
              "z0": 0.0, "sz": BOARD_THICKNESS}]
    for c in design.components:
        x0, y0, x1, y1 = court_bbox(c)
        sx, sy = max(x1 - x0 - 2 * BODY_INSET, 0.4), max(y1 - y0 - 2 * BODY_INSET, 0.4)
        boxes.append({
            "name": c.ref,
            "cx": round((x0 + x1) / 2 - ox, 3), "cy": round(-((y0 + y1) / 2 - oy), 3),   # flip Y
            "sx": round(sx, 3), "sy": round(sy, 3),
            "z0": BOARD_THICKNESS - EMBED, "sz": round(component_height_mm(c) + EMBED, 3),
        })
    return boxes
