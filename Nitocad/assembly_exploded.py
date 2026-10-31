"""
Exploded view for any assembly. Vehicle-agnostic, pure Python, no CadQuery.

What it does
  * works out an offset for every part (radial from a reference, or an explicit direction you give),
  * grows the explosion until the part boxes no longer overlap, and says which pairs it could not separate,
  * lists parts that have no usable direction (for example an axle exactly at the centre of its wheel),
  * gives an assembly sequence (innermost / fixed parts first),
  * writes a schematic SVG (front, top or side).

What it is not
  * The SVG is drawn from BOUNDING BOXES. It is a layout aid, not a true projected drawing of the solids.
    A real hidden-line exploded drawing needs the CAD kernel; the offsets computed here are what you would
    feed to it. The SVG says so on its face.
  * Axes follow the project convention: X long, Y lateral, Z vertical, millimetres.
"""
from __future__ import annotations

import math
from typing import Any, Iterable
from xml.sax.saxutils import escape

from assembly_checks import _overlap_amount

BBox = dict
VIEWS = {"front": (0, 2), "top": (0, 1), "side": (1, 2)}   # which two world axes become paper x, paper y
MIN_GAP_MM = 2.0


def _center(b: BBox) -> tuple[float, float, float]:
    return tuple((b["min"][i] + b["max"][i]) / 2.0 for i in range(3))


def _shift(b: BBox, d: Iterable[float]) -> BBox:
    d = tuple(d)
    return {"min": tuple(b["min"][i] + d[i] for i in range(3)), "max": tuple(b["max"][i] + d[i] for i in range(3))}


def _unit(v: Iterable[float]) -> tuple[float, float, float] | None:
    v = tuple(float(x) for x in v)
    n = math.sqrt(sum(x * x for x in v))
    return None if n < 1e-9 else tuple(x / n for x in v)


def _separated(a: BBox, b: BBox, min_gap: float) -> bool:
    return -_overlap_amount(a, b) >= min_gap


def explode(
    boxes: dict[str, BBox],
    *,
    fixed: str | None = None,
    directions: dict[str, Iterable[float]] | None = None,
    base_distance: float | None = None,
    max_factor: float = 8.0,
    min_gap: float = MIN_GAP_MM,
    intended_contact: Iterable[tuple[str, str]] = (),
) -> dict[str, Any]:
    """Compute exploded offsets.

    boxes             {name: {"min": (x,y,z), "max": (x,y,z)}} in the assembled pose
    fixed             a part that stays put (e.g. the chassis); other parts move relative to its centre
    directions        {name: (dx,dy,dz)} explicit direction for a part. Overrides the radial default.
                      Use this for parts that sit on the reference centre, like an axle through a wheel.
    base_distance     the distance a part moves at factor 1.0 when it has an explicit direction
                      (default: 40% of the assembly's largest dimension)
    intended_contact  pairs allowed to stay overlapping (not reported as unseparated)
    """
    if not boxes:
        return {"ok": False, "errors": ["no parts"], "offsets": {}, "boxes": {}}
    if fixed is not None and fixed not in boxes:
        return {"ok": False, "errors": [f"fixed part {fixed!r} is not in the assembly"], "offsets": {}, "boxes": {}}
    bad = [n for n in (directions or {}) if n not in boxes]
    if bad:
        return {"ok": False, "errors": [f"directions given for unknown parts: {bad}"], "offsets": {}, "boxes": {}}

    names = list(boxes)
    lo = [min(b["min"][i] for b in boxes.values()) for i in range(3)]
    hi = [max(b["max"][i] for b in boxes.values()) for i in range(3)]
    span = max(hi[i] - lo[i] for i in range(3))
    base = base_distance if base_distance is not None else 0.4 * span
    ref = _center(boxes[fixed]) if fixed else tuple((lo[i] + hi[i]) / 2 for i in range(3))

    dirs: dict[str, tuple[float, float, float]] = {}
    mag: dict[str, float] = {}                      # distance per unit factor
    no_direction: list[str] = []
    for n in names:
        if n == fixed:
            continue
        explicit = _unit((directions or {}).get(n, ())) if (directions or {}).get(n) is not None else None
        if explicit is not None:
            dirs[n], mag[n] = explicit, base
            continue
        c = _center(boxes[n])
        rad = tuple(c[i] - ref[i] for i in range(3))
        u = _unit(rad)
        if u is None:
            no_direction.append(n)
            continue
        dirs[n], mag[n] = u, math.sqrt(sum(x * x for x in rad))   # radial: factor scales the existing offset

    allowed = {frozenset(p) for p in intended_contact}

    def place(f: float) -> dict[str, BBox]:
        out = {}
        for n in names:
            if n in dirs:
                # radial parts move (f - 1) * their distance from the reference; explicit ones move f * base
                is_explicit = (directions or {}).get(n) is not None and _unit((directions or {})[n]) is not None
                dist = f * mag[n] if is_explicit else (f - 1.0) * mag[n]
                out[n] = _shift(boxes[n], tuple(x * dist for x in dirs[n]))
            else:
                out[n] = boxes[n]
        return out

    def leftover(placed: dict[str, BBox]) -> list[tuple[str, str]]:
        bad_pairs = []
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if frozenset((a, b)) in allowed:
                    continue
                if not _separated(placed[a], placed[b], min_gap):
                    bad_pairs.append((a, b))
        return bad_pairs

    factor = 1.0
    placed = place(factor)
    bad_pairs = leftover(placed)
    while bad_pairs and factor < max_factor:
        factor = round(factor + 0.25, 2)
        placed = place(factor)
        bad_pairs = leftover(placed)

    offsets = {n: tuple(round(placed[n]["min"][i] - boxes[n]["min"][i], 4) for i in range(3)) for n in names}
    # Parts that never left the reference cannot be told apart by the layout; list them too.
    unseparated = [list(p) for p in bad_pairs]
    return {
        "ok": True,
        "factor": factor,
        "offsets": offsets,
        "boxes": placed,
        "unseparated": unseparated,
        "no_direction": sorted(no_direction),
        "sequence": assembly_sequence(boxes, offsets, fixed),
        "note": ("Offsets are computed from bounding boxes. Pairs under 'unseparated' still overlap even at the "
                 "largest explosion factor tried; give them an explicit direction."),
    }


def assembly_sequence(boxes: dict[str, BBox], offsets: dict[str, tuple], fixed: str | None) -> list[str]:
    """Order to assemble in: the fixed part first, then parts that moved the least (they sit deepest inside),
    ties broken by name so the order is stable. This is a heuristic, not a validated process plan."""
    def moved(n: str) -> float:
        return math.sqrt(sum(x * x for x in offsets[n]))
    rest = sorted((n for n in boxes if n != fixed), key=lambda n: (round(moved(n), 3), n))
    return ([fixed] if fixed else []) + rest


def apply_to_parts(parts: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Copies of the parts with their placement.position moved by the exploded offset. Same shape as
    the rest of NitoCAD ({"placement": {"position": [x,y,z], "rotation": [rx,ry,rz]}})."""
    out = []
    for p in parts:
        q = {**p, "placement": dict(p.get("placement") or {})}
        pos = list(q["placement"].get("position", [0, 0, 0]))
        off = result["offsets"].get(p["name"], (0, 0, 0))
        q["placement"]["position"] = [round(pos[i] + off[i], 4) for i in range(3)]
        q["placement"].setdefault("rotation", [0, 0, 0])
        out.append(q)
    return out


def to_svg(boxes: dict[str, BBox], result: dict[str, Any], *, view: str = "front", title: str = "Exploded view",
           width_px: int = 1000) -> str:
    """Schematic exploded view from bounding boxes: ghost outline where each part sits assembled, solid box
    where it sits exploded, a leader line between them and a numbered balloon."""
    if view not in VIEWS:
        raise ValueError(f"view must be one of {sorted(VIEWS)}")
    ax, ay = VIEWS[view]
    exploded = result["boxes"]
    allb = list(boxes.values()) + list(exploded.values())
    x0 = min(b["min"][ax] for b in allb); x1 = max(b["max"][ax] for b in allb)
    y0 = min(b["min"][ay] for b in allb); y1 = max(b["max"][ay] for b in allb)
    pad = 0.06 * max(x1 - x0, y1 - y0, 1.0)
    x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - pad, y1 + pad
    s = width_px / max(x1 - x0, 1e-6)
    h_px = (y1 - y0) * s
    legend_h = 20 * (len(boxes) + 2) + 30

    def X(v): return (v - x0) * s
    def Y(v): return (y1 - v) * s                  # model up -> paper up

    names = list(boxes)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width_px} {h_px + legend_h + 60:.0f}" '
           f'font-family="sans-serif" font-size="12">',
           f'<rect width="100%" height="100%" fill="white"/>',
           f'<text x="10" y="20" font-size="16" font-weight="bold">{escape(title)} ({view} view)</text>',
           f'<text x="10" y="38" fill="#b03a2e">SCHEMATIC from bounding boxes. Not a projected drawing of the solids. '
           f'Axes: X long, Y lateral, Z vertical, mm.</text>',
           f'<g transform="translate(0,50)">']
    for i, n in enumerate(names, start=1):
        a, e = boxes[n], exploded[n]
        out.append(f'<rect x="{X(a["min"][ax]):.1f}" y="{Y(a["max"][ay]):.1f}" width="{(a["max"][ax]-a["min"][ax])*s:.1f}" '
                   f'height="{(a["max"][ay]-a["min"][ay])*s:.1f}" fill="none" stroke="#aaa" stroke-dasharray="4 3"/>')
        ca = (X((a["min"][ax] + a["max"][ax]) / 2), Y((a["min"][ay] + a["max"][ay]) / 2))
        ce = (X((e["min"][ax] + e["max"][ax]) / 2), Y((e["min"][ay] + e["max"][ay]) / 2))
        if math.hypot(ca[0] - ce[0], ca[1] - ce[1]) > 1:
            out.append(f'<line x1="{ca[0]:.1f}" y1="{ca[1]:.1f}" x2="{ce[0]:.1f}" y2="{ce[1]:.1f}" stroke="#b03a2e" stroke-dasharray="2 2"/>')
        out.append(f'<rect x="{X(e["min"][ax]):.1f}" y="{Y(e["max"][ay]):.1f}" width="{(e["max"][ax]-e["min"][ax])*s:.1f}" '
                   f'height="{(e["max"][ay]-e["min"][ay])*s:.1f}" fill="#dfe9f5" fill-opacity="0.6" stroke="#1a1a1a"/>')
        out.append(f'<circle cx="{ce[0]:.1f}" cy="{ce[1]:.1f}" r="9" fill="white" stroke="#1a1a1a"/>'
                   f'<text x="{ce[0]:.1f}" y="{ce[1] + 4:.1f}" text-anchor="middle">{i}</text>')
    out.append('</g>')
    ly = h_px + 80
    out.append(f'<text x="10" y="{ly:.0f}" font-weight="bold">Parts (assembly order shown by sequence column)</text>')
    seq = {n: k for k, n in enumerate(result.get("sequence", []), start=1)}
    for i, n in enumerate(names, start=1):
        ly += 20
        out.append(f'<text x="10" y="{ly:.0f}">{i}. {escape(n)}  (assemble step {seq.get(n, "?")})</text>')
    if result.get("unseparated"):
        ly += 24
        out.append(f'<text x="10" y="{ly:.0f}" fill="#b03a2e">Not separated: '
                   f'{escape(", ".join("/".join(p) for p in result["unseparated"]))}</text>')
    out.append('</svg>')
    return "\n".join(out)


def to_viewer_json(boxes: dict[str, BBox], result: dict[str, Any], title: str = "Exploded view") -> dict[str, Any]:
    """The JSON that frontend/exploded.html loads (Load JSON button). Boxes only, in the assembled pose."""
    step = {n: i for i, n in enumerate(result.get("sequence", []))}
    return {"title": title, "parts": [
        {"name": n, "min": list(b["min"]), "max": list(b["max"]),
         "offset": list(result["offsets"][n]), "step": step.get(n, i)}
        for i, (n, b) in enumerate(boxes.items())]}
