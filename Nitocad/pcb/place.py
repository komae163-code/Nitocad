"""Deterministic placement.

Inside each block, parts keep the hand-set relative layout from blocks.py
(that is the point of pre-designed blocks). The placer only packs whole blocks
into rows and draws the board outline around them.
"""
from __future__ import annotations

from collections import OrderedDict

from .model import RULES, Design, court_bbox

BLOCK_GAP = 3.0
NEAR_GAP = 0.6
ORIGIN = (20.0, 20.0)


def _snap(v: float, step: float = 0.01) -> float:
    return round(round(v / step) * step, 4)


def _resolve_near(comps):
    by_ref = {c.ref: c for c in comps}
    for c in comps:
        if not c.near:
            continue
        parent_ref, side, along = c.near
        p = by_ref[parent_ref]
        px0, py0, px1, py1 = court_bbox(p)
        c.x = c.y = 0.0
        cx0, cy0, cx1, cy1 = court_bbox(c)   # extents around its own origin
        hw, hh = (cx1 - cx0) / 2, (cy1 - cy0) / 2
        ccx, ccy = (cx0 + cx1) / 2, (cy0 + cy1) / 2     # courtyard centre relative to the part origin
        pcx = (px0 + px1) / 2
        pcy = (py0 + py1) / 2
        if side == "below":
            tx, ty = pcx + along, py1 + NEAR_GAP + hh
        elif side == "above":
            tx, ty = pcx + along, py0 - NEAR_GAP - hh
        elif side == "right":
            tx, ty = px1 + NEAR_GAP + hw, pcy + along
        elif side == "left":
            tx, ty = px0 - NEAR_GAP - hw, pcy + along
        else:
            raise ValueError(f"unknown side {side!r}")
        c.x, c.y = tx - ccx, ty - ccy      # centre the COURTYARD (not the origin) on the target


def place(design: Design) -> Design:
    max_w = getattr(design, "_max_width", 80.0)
    margin = RULES["board_margin"]
    groups: OrderedDict[str, list] = OrderedDict()
    for c in design.components:
        groups.setdefault(c.block_id, []).append(c)

    boxes = {}
    for bid, comps in groups.items():
        _resolve_near(comps)
        bb = [court_bbox(c) for c in comps]
        x0 = min(b[0] for b in bb)
        y0 = min(b[1] for b in bb)
        x1 = max(b[2] for b in bb)
        y1 = max(b[3] for b in bb)
        for c in comps:       # normalise block to its own (0,0)
            c.x -= x0
            c.y -= y0
        boxes[bid] = (x1 - x0, y1 - y0)

    # row packing
    x = y = row_h = 0.0
    inner_w = max_w - 2 * margin
    for bid, comps in groups.items():
        w, h = boxes[bid]
        if x > 0 and x + w > inner_w:
            x = 0.0
            y += row_h + BLOCK_GAP
            row_h = 0.0
        for c in comps:
            c.x += x
            c.y += y
        x += w + BLOCK_GAP
        row_h = max(row_h, h)

    bb = [court_bbox(c) for c in design.components]
    bx0, by0 = min(b[0] for b in bb), min(b[1] for b in bb)
    bx1, by1 = max(b[2] for b in bb), max(b[3] for b in bb)
    ox = ORIGIN[0] + margin - bx0
    oy = ORIGIN[1] + margin - by0
    for c in design.components:
        c.x = _snap(c.x + ox)
        c.y = _snap(c.y + oy)
    w = bx1 - bx0 + 2 * margin
    h = by1 - by0 + 2 * margin
    w = -(-w // 0.5) * 0.5
    h = -(-h // 0.5) * 0.5
    design.board = (ORIGIN[0], ORIGIN[1], ORIGIN[0] + w, ORIGIN[1] + h)
    if w > max_w + 1e-6:
        design.warnings.append(f"board is {w:g} mm wide, wider than the requested {max_w:g} mm")
    return design
