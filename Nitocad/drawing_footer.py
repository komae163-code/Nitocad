"""
The text and tables that make a drawing a drawing: general notes, the ISO 2768 general
tolerance table, hole call-outs with counts, centre marks, and the parts list for assemblies.

Pure Python with no CAD or drawing libraries. It produces a small list of PRIMITIVES (text and
lines in millimetres) that exporters.py draws into DXF and PDF alike, so both formats show the
same content and the layout can be tested without either library.

Primitive coordinates: origin at the footer's bottom-left, x right, y up.
    ("text", x, y, size_mm, bold, string)
    ("line", x0, y0, x1, y1)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ISO 2768-1 permitted deviations for LINEAR dimensions (mm). Each row is
# (over, up_to_and_including, f, m, c, v); None means the class does not cover that range.
ISO2768_LINEAR = [
    (0.5, 3, 0.05, 0.1, 0.2, None),
    (3, 6, 0.05, 0.1, 0.3, 0.5),
    (6, 30, 0.1, 0.2, 0.5, 1.0),
    (30, 120, 0.15, 0.3, 0.8, 1.5),
    (120, 400, 0.2, 0.5, 1.2, 2.5),
    (400, 1000, 0.3, 0.8, 2.0, 4.0),
    (1000, 2000, 0.5, 1.2, 3.0, 6.0),
    (2000, 4000, None, 2.0, 4.0, 8.0),
]
CLASSES = {"f": 2, "m": 3, "c": 4, "v": 5}
CLASS_NAMES = {"f": "fine", "m": "medium", "c": "coarse", "v": "very coarse"}

LINE_H = 4.4            # mm between text baselines
TEXT = 2.5              # mm text height
CHAR_W = 0.78 * TEXT    # conservative average character width, matches the DXF font and Helvetica
PAD = 3.0               # mm inside every box
GAP = 6.0               # mm between blocks


def fmt_number(value: float) -> str:
    """5.0 -> '5', 5.25 -> '5.25', 5.1 -> '5.1'. Never '5.0' and never scientific notation."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def iso2768_tolerance(nominal: float, tol_class: str = "m") -> float | None:
    """Permitted +/- deviation for a linear dimension, or None if the class does not cover it
    (below 0.5 mm the standard says to state the tolerance on the drawing)."""
    tol_class = tol_class.lower()
    if tol_class not in CLASSES:
        raise ValueError(f"tolerance class must be one of f, m, c, v, got {tol_class!r}")
    if not math.isfinite(nominal) or nominal <= 0.5:
        return None
    for lo, hi, *vals in ISO2768_LINEAR:
        if lo < nominal <= hi:
            return vals[CLASSES[tol_class] - 2]
    return None


def tolerance_rows(tol_class: str = "m") -> list[tuple[str, str]]:
    rows = []
    for lo, hi, *vals in ISO2768_LINEAR:
        v = vals[CLASSES[tol_class.lower()] - 2]
        if v is not None:
            rows.append((f"{fmt_number(lo)} to {fmt_number(hi)}", f"\u00b1{fmt_number(v)}"))
    return rows


def hole_label(diameter: float, count: int) -> str:
    """'4X \u00d85' for four identical holes, '\u00d85' for one. No 'THRU': whether a hole goes
    all the way through is not something we have measured."""
    base = f"\u00d8{fmt_number(diameter)}"
    return f"{count}X {base}" if count > 1 else base


def hole_groups(circles: list[tuple[float, float, float]], tol: float = 0.05) -> list[tuple[float, float, float, int]]:
    """Group circles of the same radius (within `tol` mm). Returns (cx, cy, r, count) with the
    position of the first circle of each group, in input order."""
    groups: list[list] = []
    for cx, cy, r in circles:
        for g in groups:
            if abs(g[2] - r) <= tol:
                g[3] += 1
                break
        else:
            groups.append([cx, cy, r, 1])
    return [(g[0], g[1], g[2], g[3]) for g in groups]


def center_mark(cx: float, cy: float, r: float, overshoot: float = 2.0) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """The two short crossing lines that mark a hole's centre, reaching `overshoot` mm past the rim."""
    reach = r + overshoot
    return [((cx - reach, cy), (cx + reach, cy)), ((cx, cy - reach), (cx, cy + reach))]


def leader_angle(cx: float, cy: float, r: float, others: list[tuple[float, float, float]],
                 bounds: tuple[float, float, float, float], reach: float = 12.0) -> float:
    """Degrees (0 = +x, counter-clockwise) for a hole's dimension leader: the direction whose
    label spot is clearest of the other holes and stays inside the view's outline.

    Prefers 45 degrees, the usual drafting angle, when it is clear."""
    u0, u1, v0, v1 = bounds
    best, best_score = 45.0, -1.0
    for angle in (45, 135, 315, 225, 0, 90, 180, 270):
        lx = cx + (r + reach) * math.cos(math.radians(angle))
        ly = cy + (r + reach) * math.sin(math.radians(angle))
        clearance = min(
            [math.hypot(lx - ox, ly - oy) - orr for ox, oy, orr in others if (ox, oy, orr) != (cx, cy, r)] or [1e9])
        edge = min(lx - u0, u1 - lx, ly - v0, v1 - ly)           # negative when the label would leave the view
        score = min(clearance, edge + 4.0)
        if score > best_score + (3.0 if angle == 45 else 0.0) * 0:   # strictly better wins; order breaks ties toward 45
            best, best_score = float(angle), score
    return best


def general_notes(tol_class: str = "m", scale: str = "1:1") -> list[str]:
    tol_class = tol_class.lower()
    return [
        "ALL DIMENSIONS IN MILLIMETRES.",
        f"GENERAL TOLERANCES ISO 2768-{tol_class} ({CLASS_NAMES[tol_class].upper()}) UNLESS STATED.",
        "THIRD ANGLE PROJECTION.",
        f"SCALE {scale}. DO NOT SCALE FROM THE DRAWING.",
        "MASS IS FROM NOMINAL VOLUME AND MATERIAL DENSITY.",
    ]


@dataclass
class Footer:
    notes: list[str]
    tolerance_title: str
    tolerance: list[tuple[str, str]]
    table_title: str | None = None
    table_header: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    table_footer: str | None = None


def build_footer(bom: dict | None = None, tol_class: str = "m", scale: str = "1:1") -> Footer:
    notes = general_notes(tol_class, scale)
    if bom is None:
        notes = [n for n in notes if not n.startswith("MASS")]
    footer = Footer(notes=notes, tolerance_title=f"GENERAL TOLERANCES ISO 2768-{tol_class.lower()}",
                    tolerance=tolerance_rows(tol_class))
    if bom and bom.get("rows"):
        footer.table_title = "PARTS LIST"
        footer.table_header = ["ITEM", "QTY", "PART", "MATERIAL", "MASS g"]
        for r in bom["rows"]:
            name = r["names"][0] if len(r["names"]) == 1 else f"{r['names'][0]} +{len(r['names']) - 1}"
            mass = fmt_number(r["total_mass_g"]) if r.get("total_mass_g") is not None else "-"
            footer.table_rows.append([str(r["item"]), str(r["quantity"]), f"{name} ({r['part_type']})"[:46], (r.get("material") or "-")[:22], mass])
        total = fmt_number(bom["total_mass_g"]) + (" g" if bom.get("mass_is_complete") else " g (PARTIAL)")
        footer.table_footer = f"TOTAL {bom['part_count']} PARTS, {total}"
    return footer


def _w(text: str) -> float:
    return len(text) * CHAR_W


def layout_footer(footer: Footer, min_width: float = 0.0) -> tuple[list[tuple], float, float]:
    """Return (primitives, width, height). Blocks sit side by side: notes, tolerance table,
    parts list. The whole footer is at least `min_width` wide (it is padded on the right)."""
    prims: list[tuple] = []
    x = 0.0
    heights: list[float] = []

    def box(x0: float, w: float, h: float) -> None:
        prims.extend([("line", x0, 0, x0 + w, 0), ("line", x0 + w, 0, x0 + w, h),
                      ("line", x0 + w, h, x0, h), ("line", x0, h, x0, 0)])

    # --- notes
    note_lines = [f"{i}. {n}" for i, n in enumerate(footer.notes, 1)]
    w = max([_w("NOTES")] + [_w(t) for t in note_lines]) + 2 * PAD
    h = (len(note_lines) + 2) * LINE_H + PAD
    heights.append(h)
    blocks = [("notes", x, w, h, note_lines)]
    x += w + GAP

    # --- tolerance table
    c1 = max([_w("OVER TO")] + [_w(a) for a, _ in footer.tolerance]) + 2 * PAD
    c2 = max([_w("+/-")] + [_w(b) for _, b in footer.tolerance]) + 2 * PAD
    w = max(c1 + c2, _w(footer.tolerance_title) + 2 * PAD)
    c2 = w - c1
    h = (len(footer.tolerance) + 3) * LINE_H + PAD
    heights.append(h)
    blocks.append(("tol", x, w, h, (c1, c2)))
    x += w + GAP

    # --- parts list
    if footer.table_title:
        cols = [max([_w(footer.table_header[i])] + [_w(r[i]) for r in footer.table_rows]) + 2 * PAD for i in range(len(footer.table_header))]
        w = max(sum(cols), _w(footer.table_footer or "") + 2 * PAD, _w(footer.table_title) + 2 * PAD)
        cols[2] += w - sum(cols)
        h = (len(footer.table_rows) + 4) * LINE_H + PAD
        heights.append(h)
        blocks.append(("table", x, w, h, cols))
        x += w + GAP

    total_h = max(heights)
    total_w = max(x - GAP, min_width)

    for kind, x0, w, h, data in blocks:
        top = total_h
        box(x0, w, total_h)
        if kind == "notes":
            prims.append(("text", x0 + PAD, top - LINE_H, TEXT, True, "NOTES"))
            prims.append(("line", x0, top - LINE_H - 1.2, x0 + w, top - LINE_H - 1.2))
            for i, t in enumerate(data):
                prims.append(("text", x0 + PAD, top - (i + 2.4) * LINE_H, TEXT, False, t))
        elif kind == "tol":
            c1, c2 = data
            prims.append(("text", x0 + PAD, top - LINE_H, TEXT, True, footer.tolerance_title))
            prims.append(("line", x0, top - LINE_H - 1.2, x0 + w, top - LINE_H - 1.2))
            prims.append(("text", x0 + PAD, top - 2.4 * LINE_H, TEXT, True, "OVER TO"))
            prims.append(("text", x0 + c1 + PAD, top - 2.4 * LINE_H, TEXT, True, "+/-"))
            prims.append(("line", x0 + c1, top - LINE_H - 1.2, x0 + c1, top - (len(footer.tolerance) + 2.4) * LINE_H - 1.5))
            for i, (a, b) in enumerate(footer.tolerance):
                prims.append(("text", x0 + PAD, top - (i + 3.4) * LINE_H, TEXT, False, a))
                prims.append(("text", x0 + c1 + PAD, top - (i + 3.4) * LINE_H, TEXT, False, b))
        else:
            cols = data
            prims.append(("text", x0 + PAD, top - LINE_H, TEXT, True, footer.table_title))
            prims.append(("line", x0, top - LINE_H - 1.2, x0 + w, top - LINE_H - 1.2))
            cx = x0
            edges = []
            for i, name in enumerate(footer.table_header):
                prims.append(("text", cx + PAD, top - 2.4 * LINE_H, TEXT, True, name))
                cx += cols[i]
                edges.append(cx)
            prims.append(("line", x0, top - 2.4 * LINE_H - 1.5, x0 + w, top - 2.4 * LINE_H - 1.5))
            for r_i, row in enumerate(footer.table_rows):
                cx = x0
                for i, cell in enumerate(row):
                    prims.append(("text", cx + PAD, top - (r_i + 3.4) * LINE_H, TEXT, False, cell))
                    cx += cols[i]
            body_bottom = top - (len(footer.table_rows) + 2.4) * LINE_H - 1.5
            for e in edges[:-1]:
                prims.append(("line", e, top - LINE_H - 1.2, e, body_bottom))
            prims.append(("line", x0, body_bottom, x0 + w, body_bottom))
            prims.append(("text", x0 + PAD, body_bottom - LINE_H, TEXT, True, footer.table_footer or ""))
    return prims, total_w, total_h
