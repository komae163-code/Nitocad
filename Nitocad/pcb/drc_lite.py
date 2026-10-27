"""
Independent design-rule + connectivity checker.

Deliberately shares no geometry code with route.py, so it can catch router
bugs. It is a pre-flight gate that works without KiCad installed; it does NOT
replace KiCad's DRC (zone fills, silkscreen, courtyards, etc.).
"""
from __future__ import annotations

import math
from collections import defaultdict

from .model import RULES, Design, abs_pads, court_bbox

EPS = 1e-6


# ------------------------------------------------------------ primitives
class Prim:
    __slots__ = ("kind", "layers", "net", "geo", "tag")

    def __init__(self, kind, layers, net, geo, tag):
        self.kind, self.layers, self.net, self.geo, self.tag = kind, layers, net, geo, tag


def _pt_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _ccw(ax, ay, bx, by, cx, cy):
    return (cy - ay) * (bx - ax) - (by - ay) * (cx - ax)


def _seg_cross(a, b, c, d):
    d1 = _ccw(*c, *d, *a); d2 = _ccw(*c, *d, *b)
    d3 = _ccw(*a, *b, *c); d4 = _ccw(*a, *b, *d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _seg_seg(a, b, c, d):
    if _seg_cross(a, b, c, d):
        return 0.0
    return min(_pt_seg(*a, *c, *d), _pt_seg(*b, *c, *d), _pt_seg(*c, *a, *b), _pt_seg(*d, *a, *b))


def _pt_rect(px, py, r):
    x0, y0, x1, y1 = r
    dx = max(x0 - px, 0, px - x1)
    dy = max(y0 - py, 0, py - y1)
    return math.hypot(dx, dy)


def _seg_rect(a, b, r):
    x0, y0, x1, y1 = r
    # inside / crossing
    if _pt_rect(*a, r) == 0 or _pt_rect(*b, r) == 0:
        return 0.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    best = 1e18
    for i in range(4):
        best = min(best, _seg_seg(a, b, corners[i], corners[(i + 1) % 4]))
    return best


def _dist(p: Prim, q: Prim) -> float:
    """Edge-to-edge distance between two copper primitives (0 if touching)."""
    def kind(x):
        return x.kind
    ka, kb = kind(p), kind(q)
    if ka > kb:
        p, q = q, p
        ka, kb = kb, ka
    # kinds sort: 'pad' < 'track' < 'via'
    if ka == "pad" and kb == "pad":
        r1, r2 = p.geo, q.geo
        dx = max(r1[0] - r2[2], r2[0] - r1[2], 0)
        dy = max(r1[1] - r2[3], r2[1] - r1[3], 0)
        return math.hypot(dx, dy)
    if ka == "pad" and kb == "track":
        x1, y1, x2, y2, w = q.geo
        return max(0.0, _seg_rect((x1, y1), (x2, y2), p.geo) - w / 2)
    if ka == "pad" and kb == "via":
        x, y, r = q.geo
        return max(0.0, _pt_rect(x, y, p.geo) - r)
    if ka == "track" and kb == "track":
        a = p.geo; b = q.geo
        return max(0.0, _seg_seg((a[0], a[1]), (a[2], a[3]), (b[0], b[1]), (b[2], b[3])) - a[4] / 2 - b[4] / 2)
    if ka == "track" and kb == "via":
        a = p.geo; x, y, r = q.geo
        return max(0.0, _pt_seg(x, y, a[0], a[1], a[2], a[3]) - a[4] / 2 - r)
    x1, y1, r1 = p.geo; x2, y2, r2 = q.geo
    return max(0.0, math.hypot(x1 - x2, y1 - y2) - r1 - r2)


def collect(design: Design):
    prims, holes = [], []
    for c in design.components:
        for pad, cx, cy, w, h in abs_pads(c):
            if pad.kind == "np":
                holes.append((cx, cy, pad.drill, f"{c.ref}.{pad.number}"))
                continue
            net = c.pin_nets.get(pad.number)
            layers = ("F.Cu", "B.Cu") if pad.kind == "thru" else tuple(l for l in pad.layers if l in ("F.Cu", "B.Cu")) or ("F.Cu",)
            prims.append(Prim("pad", layers, net or f"~unconnected-{c.ref}.{pad.number}",
                              (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), f"{c.ref}.{pad.number}"))
            if pad.kind == "thru":
                holes.append((cx, cy, pad.drill, f"{c.ref}.{pad.number}"))
    for i, t in enumerate(design.tracks):
        prims.append(Prim("track", (t.layer,), t.net, (t.x1, t.y1, t.x2, t.y2, t.width), f"track#{i}"))
    for i, v in enumerate(design.vias):
        prims.append(Prim("via", ("F.Cu", "B.Cu"), v.net, (v.x, v.y, v.dia / 2), f"via#{i}"))
        holes.append((v.x, v.y, v.drill, f"via#{i}"))
    return prims, holes


# ------------------------------------------------------------ checks
def check_clearance(prims, clearance):
    errs = []
    by_layer = defaultdict(list)
    for p in prims:
        for l in p.layers:
            by_layer[l].append(p)
    for layer, ps in by_layer.items():
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                a, b = ps[i], ps[j]
                if a.net == b.net:
                    continue
                d = _dist(a, b)
                if d < clearance - EPS:
                    errs.append(f"clearance {d:.3f} mm < {clearance} mm on {layer}: "
                                f"{a.tag} ({a.net}) vs {b.tag} ({b.net})")
    return errs


def check_widths_and_vias(design):
    errs = []
    for i, t in enumerate(design.tracks):
        if t.width < 0.2 - EPS:
            errs.append(f"track#{i} width {t.width} below 0.2 mm")
    for i, v in enumerate(design.vias):
        if (v.dia - v.drill) / 2 < RULES["min_annular"] - EPS:
            errs.append(f"via#{i} annular ring {(v.dia - v.drill) / 2:.3f} mm too small")
    return errs


def check_edges(design, prims):
    x0, y0, x1, y1 = design.board
    ec = RULES["edge_clearance"]
    errs = []
    for p in prims:
        if p.kind == "pad":
            bx0, by0, bx1, by1 = p.geo
        elif p.kind == "track":
            x_a, y_a, x_b, y_b, w = p.geo
            bx0, by0 = min(x_a, x_b) - w / 2, min(y_a, y_b) - w / 2
            bx1, by1 = max(x_a, x_b) + w / 2, max(y_a, y_b) + w / 2
        else:
            x, y, r = p.geo
            bx0, by0, bx1, by1 = x - r, y - r, x + r, y + r
        if bx0 < x0 + ec - EPS or by0 < y0 + ec - EPS or bx1 > x1 - ec + EPS or by1 > y1 - ec + EPS:
            errs.append(f"{p.tag} ({p.net}) is closer than {ec} mm to the board edge")
    return errs


def check_holes(holes):
    errs = []
    gap = RULES["min_hole_gap"]
    for i in range(len(holes)):
        for j in range(i + 1, len(holes)):
            a, b = holes[i], holes[j]
            d = math.hypot(a[0] - b[0], a[1] - b[1]) - a[2] / 2 - b[2] / 2
            if d < gap - EPS:
                errs.append(f"holes {a[3]} and {b[3]} are {d:.3f} mm apart (min {gap})")
    return errs


def check_courtyards(design):
    errs = []
    boxes = [(c.ref, court_bbox(c)) for c in design.components]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (ra, a), (rb, b) = boxes[i], boxes[j]
            if a[0] < b[2] - EPS and b[0] < a[2] - EPS and a[1] < b[3] - EPS and b[1] < a[3] - EPS:
                errs.append(f"courtyards of {ra} and {rb} overlap")
    return errs


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def check_connectivity(design, prims):
    """Rebuild connectivity from the copper itself and compare with the netlist."""
    uf = _UF()
    plane = design.ground_mode == "plane"
    copper = [p for p in prims if not p.net.startswith("~")]
    for i in range(len(copper)):
        for j in range(i + 1, len(copper)):
            a, b = copper[i], copper[j]
            if not set(a.layers) & set(b.layers):
                continue
            if _dist(a, b) <= EPS:
                uf.union(a.tag, b.tag)
    if plane:
        # B.Cu ground pour joins everything ground that reaches the bottom layer
        for p in copper:
            if p.net == "GND" and "B.Cu" in p.layers:
                uf.union(p.tag, "GND-PLANE")
    errs = []
    comp_nets = defaultdict(set)
    for p in copper:
        comp_nets[uf.find(p.tag)].add(p.net)
    for root, nets in comp_nets.items():
        if len(nets) > 1:
            errs.append(f"SHORT between nets {sorted(nets)}")
    per_net = defaultdict(set)
    for p in copper:
        if p.kind == "pad":
            per_net[p.net].add(uf.find(p.tag))
    for net, roots in per_net.items():
        if len(roots) > 1:
            errs.append(f"net {net} is not fully connected ({len(roots)} separate pieces)")
    return errs


def run_drc_lite(design: Design) -> dict:
    prims, holes = collect(design)
    problems = {
        "clearance": check_clearance(prims, RULES["clearance"]),
        "width_via": check_widths_and_vias(design),
        "edge": check_edges(design, prims),
        "holes": check_holes(holes),
        "courtyard": check_courtyards(design),
        "connectivity": check_connectivity(design, prims),
    }
    if design.unrouted:
        problems["connectivity"].append(f"router could not complete nets: {design.unrouted}")
    total = sum(len(v) for v in problems.values())
    return {"passed": total == 0, "violations": total, "problems": problems}
