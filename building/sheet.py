"""
Drawing sheets: one neutral list of drawing entities, three renderers (SVG, PDF, DXF).

Entities are in MODEL millimetres (x east, y up); text heights are in PAPER millimetres so a
sheet prints at a true, standard scale (1:50, 1:100, ...). The scale is chosen automatically as
the largest standard scale that fits the paper, and is printed in the title block.

Layers follow common AIA-style names so the DXF opens sensibly in any CAD package.
"""
from __future__ import annotations

import io
import math
from typing import Any, Iterable
from xml.sax.saxutils import escape

LAYERS: dict[str, dict[str, Any]] = {
    "A-WALL":      {"color": "#1a1a1a", "weight": 0.50, "dxf": 7},
    "A-WALL-CUT":  {"color": "#1a1a1a", "weight": 0.70, "dxf": 7},
    "A-DOOR":      {"color": "#222222", "weight": 0.25, "dxf": 3},
    "A-GLAZ":      {"color": "#1f4e8c", "weight": 0.18, "dxf": 5},
    "A-STRS":      {"color": "#222222", "weight": 0.25, "dxf": 4},
    "A-STRS-ARRW": {"color": "#b03a2e", "weight": 0.25, "dxf": 1},
    "A-FURN":      {"color": "#7a7a7a", "weight": 0.18, "dxf": 8},
    "A-SLAB":      {"color": "#444444", "weight": 0.50, "dxf": 7},
    "A-ROOF":      {"color": "#7a2e12", "weight": 0.35, "dxf": 6},
    "A-ELEV":      {"color": "#222222", "weight": 0.35, "dxf": 7},
    "A-ELEV-BEYOND": {"color": "#999999", "weight": 0.18, "dxf": 8},
    "A-GRND":      {"color": "#000000", "weight": 0.70, "dxf": 7},
    "A-ANNO-TEXT": {"color": "#111111", "weight": 0.18, "dxf": 7},
    "A-ANNO-DIMS": {"color": "#333333", "weight": 0.18, "dxf": 2},
    "A-ANNO-SECT": {"color": "#b03a2e", "weight": 0.35, "dxf": 1},
    "A-FNDN":      {"color": "#5a4632", "weight": 0.35, "dxf": 30},
}
STANDARD_SCALES = [20, 50, 100, 200, 500, 1000]  # metric architectural scales
PAPERS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0)}
TITLE_BLOCK_H = 34.0
MARGIN = 10.0
PAD = 12.0


class Sheet:
    def __init__(self, title: str, project: str, sheet_no: str = "A-101", note: str = ""):
        self.title, self.project, self.sheet_no, self.note = title, project, sheet_no, note
        self.entities: list[dict[str, Any]] = []

    # -- builders
    def line(self, p, q, layer):
        self.entities.append({"t": "line", "p": tuple(p), "q": tuple(q), "layer": layer})

    def poly(self, pts: Iterable, layer: str, closed: bool = True, fill: str | None = None):
        self.entities.append({"t": "poly", "pts": [tuple(p) for p in pts], "layer": layer, "closed": closed, "fill": fill})

    def rect(self, x0, y0, x1, y1, layer, fill=None):
        self.poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], layer, True, fill)

    def arc(self, c, r, a0, a1, layer):
        self.entities.append({"t": "arc", "c": tuple(c), "r": r, "a0": a0, "a1": a1, "layer": layer})

    def text(self, p, s, h, layer="A-ANNO-TEXT", anchor="middle", rot=0.0, bold=False):
        self.entities.append({"t": "text", "p": tuple(p), "s": str(s), "h": h, "layer": layer,
                              "anchor": anchor, "rot": rot, "bold": bold})

    def dim_h(self, x0, x1, y, label=None, off=0.0):
        """Horizontal dimension line with ticks and text, drawn at model height y."""
        yy = y + off
        self.line((x0, yy), (x1, yy), "A-ANNO-DIMS")
        for x in (x0, x1):
            self.line((x, yy - 120), (x, yy + 120), "A-ANNO-DIMS")
        self.text(((x0 + x1) / 2, yy + 330), label or f"{abs(x1 - x0):,.0f}", 2.5, "A-ANNO-DIMS", "middle")

    def dim_v(self, y0, y1, x, label=None):
        self.line((x, y0), (x, y1), "A-ANNO-DIMS")
        for y in (y0, y1):
            self.line((x - 120, y), (x + 120, y), "A-ANNO-DIMS")
        self.text((x + 330, (y0 + y1) / 2), label or f"{abs(y1 - y0):,.0f}", 2.5, "A-ANNO-DIMS", "middle", rot=90)

    # -- geometry
    def bounds(self) -> tuple[float, float, float, float]:
        xs: list[float] = []
        ys: list[float] = []
        for e in self.entities:
            t = e["t"]
            if t == "poly":
                xs += [p[0] for p in e["pts"]]; ys += [p[1] for p in e["pts"]]
            elif t == "line":
                xs += [e["p"][0], e["q"][0]]; ys += [e["p"][1], e["q"][1]]
            elif t == "arc":
                xs += [e["c"][0] - e["r"], e["c"][0] + e["r"]]; ys += [e["c"][1] - e["r"], e["c"][1] + e["r"]]
            elif t == "text":
                xs.append(e["p"][0]); ys.append(e["p"][1])
        if not xs:
            return (0.0, 0.0, 1.0, 1.0)
        return (min(xs), min(ys), max(xs), max(ys))


def choose_layout(sheet: Sheet, target_scale: int = 50) -> tuple[str, int]:
    """Smallest standard paper on which the sheet reaches the target scale (A1 at the best scale otherwise)."""
    for paper in ("A3", "A2", "A1"):
        f = fit(sheet, paper)
        if f["scale"] <= target_scale:
            return paper, f["scale"]
    return "A1", fit(sheet, "A1")["scale"]


def fit(sheet: Sheet, paper: str | None = "A3", scale: int | None = None) -> dict[str, Any]:
    if paper is None:
        paper, auto_scale = choose_layout(sheet)
        scale = scale or auto_scale
    pw, ph = PAPERS[paper]
    x0, y0, x1, y1 = sheet.bounds()
    bw, bh = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    avail_w = pw - 2 * MARGIN - 2 * PAD
    avail_h = ph - 2 * MARGIN - TITLE_BLOCK_H - 2 * PAD
    if scale is None:
        scale = next((s for s in STANDARD_SCALES if bw / s <= avail_w and bh / s <= avail_h), STANDARD_SCALES[-1])
    off_x = MARGIN + PAD + (avail_w - bw / scale) / 2
    off_y = MARGIN + PAD + (avail_h - bh / scale) / 2
    return {"paper": paper, "pw": pw, "ph": ph, "scale": scale, "x0": x0, "y1": y1, "off_x": off_x, "off_y": off_y,
            "fits": bw / scale <= avail_w + 1e-6 and bh / scale <= avail_h + 1e-6}


def _px(f: dict[str, Any], x: float) -> float:
    return f["off_x"] + (x - f["x0"]) / f["scale"]


def _py(f: dict[str, Any], y: float) -> float:        # paper y, measured down from the top edge
    return f["off_y"] + (f["y1"] - y) / f["scale"]


def _title_block(sheet: Sheet, f: dict[str, Any]) -> list[tuple]:
    pw, ph = f["pw"], f["ph"]
    top = ph - MARGIN - TITLE_BLOCK_H
    return [("rect", MARGIN, MARGIN, pw - MARGIN, ph - MARGIN),
            ("rect", MARGIN, top, pw - MARGIN, ph - MARGIN),
            ("line", pw * 0.52, top, pw * 0.52, ph - MARGIN),
            ("line", pw * 0.78, top, pw * 0.78, ph - MARGIN),
            ("text", MARGIN + 4, top + 7, "PROJECT", 2.0, "start", False),
            ("text", MARGIN + 4, top + 17, sheet.project, 5.0, "start", True),
            ("text", MARGIN + 4, top + 28, sheet.note or "CONCEPT DESIGN. NOT FOR CONSTRUCTION. Building-code compliance: UNKNOWN (not assessed).", 2.2, "start", False),
            ("text", pw * 0.52 + 4, top + 7, "DRAWING", 2.0, "start", False),
            ("text", pw * 0.52 + 4, top + 19, sheet.title, 5.0, "start", True),
            ("text", pw * 0.78 + 4, top + 7, "SCALE", 2.0, "start", False),
            ("text", pw * 0.78 + 4, top + 17, f"1:{f['scale']}  ({f['paper']})", 5.0, "start", True),
            ("text", pw * 0.78 + 4, top + 28, f"Sheet {sheet.sheet_no}   Rev A", 3.0, "start", False)]


# ----------------------------------------------------------------------------- SVG
def to_svg(sheet: Sheet, paper: str | None = None, scale: int | None = None) -> str:
    f = fit(sheet, paper, scale)
    pw, ph = f["pw"], f["ph"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{pw}mm" height="{ph}mm" viewBox="0 0 {pw} {ph}" '
           f'font-family="Helvetica, Arial, sans-serif">', '<rect width="100%" height="100%" fill="white"/>']
    for e in sheet.entities:
        lay = LAYERS.get(e["layer"], LAYERS["A-ANNO-TEXT"])
        sw = lay["weight"]
        col = lay["color"]
        if e["t"] == "poly":
            pts = " ".join(f"{_px(f, x):.2f},{_py(f, y):.2f}" for x, y in e["pts"])
            fill = e["fill"] or "none"
            tag = "polygon" if e["closed"] else "polyline"
            out.append(f'<{tag} points="{pts}" fill="{fill}" stroke="{col}" stroke-width="{sw}" stroke-linejoin="round"/>')
        elif e["t"] == "line":
            out.append(f'<line x1="{_px(f, e["p"][0]):.2f}" y1="{_py(f, e["p"][1]):.2f}" x2="{_px(f, e["q"][0]):.2f}" '
                       f'y2="{_py(f, e["q"][1]):.2f}" stroke="{col}" stroke-width="{sw}"/>')
        elif e["t"] == "arc":
            cx, cy, r = e["c"][0], e["c"][1], e["r"]
            p0 = (cx + r * math.cos(math.radians(e["a0"])), cy + r * math.sin(math.radians(e["a0"])))
            p1 = (cx + r * math.cos(math.radians(e["a1"])), cy + r * math.sin(math.radians(e["a1"])))
            large = 1 if (e["a1"] - e["a0"]) % 360 > 180 else 0
            rp = r / f["scale"]
            out.append(f'<path d="M {_px(f, p0[0]):.2f} {_py(f, p0[1]):.2f} A {rp:.2f} {rp:.2f} 0 {large} 1 '
                       f'{_px(f, p1[0]):.2f} {_py(f, p1[1]):.2f}" fill="none" stroke="{col}" stroke-width="{sw}"/>')
        elif e["t"] == "text":
            x, y = _px(f, e["p"][0]), _py(f, e["p"][1])
            anchor = {"start": "start", "middle": "middle", "end": "end"}[e["anchor"]]
            rot = f' transform="rotate({-e["rot"]:.1f} {x:.2f} {y:.2f})"' if e["rot"] else ""
            weight = ' font-weight="bold"' if e["bold"] else ""
            out.append(f'<text x="{x:.2f}" y="{y + e["h"] * 0.35:.2f}" font-size="{e["h"]}" fill="{col}" '
                       f'text-anchor="{anchor}"{weight}{rot}>{escape(e["s"])}</text>')
    for item in _title_block(sheet, f):
        if item[0] == "rect":
            out.append(f'<rect x="{item[1]}" y="{item[2]}" width="{item[3] - item[1]}" height="{item[4] - item[2]}" '
                       f'fill="none" stroke="#000" stroke-width="0.5"/>')
        elif item[0] == "line":
            out.append(f'<line x1="{item[1]}" y1="{item[2]}" x2="{item[3]}" y2="{item[4]}" stroke="#000" stroke-width="0.35"/>')
        else:
            _, x, y, s, h, anchor, bold = item
            w = ' font-weight="bold"' if bold else ""
            out.append(f'<text x="{x}" y="{y}" font-size="{h}" text-anchor="{anchor}"{w} fill="#000">{escape(s)}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ----------------------------------------------------------------------------- PDF
def _pdf_page(c, sheet: Sheet, paper: str | None, scale: int | None) -> None:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.units import mm

    f = fit(sheet, paper, scale)
    pw, ph = f["pw"], f["ph"]
    c.setPageSize((pw * mm, ph * mm))

    def P(x, y):  # paper mm from top-left -> PDF points from bottom-left
        return x * mm, (ph - y) * mm

    for e in sheet.entities:
        lay = LAYERS.get(e["layer"], LAYERS["A-ANNO-TEXT"])
        c.setStrokeColor(HexColor(lay["color"]))
        c.setLineWidth(lay["weight"] * mm)
        if e["t"] == "poly":
            path = c.beginPath()
            first = True
            for x, y in e["pts"]:
                px, py = P(_px(f, x), _py(f, y))
                path.moveTo(px, py) if first else path.lineTo(px, py)
                first = False
            if e["closed"]:
                path.close()
            if e["fill"]:
                c.setFillColor(HexColor(e["fill"]))
            c.drawPath(path, stroke=1, fill=1 if e["fill"] else 0)
        elif e["t"] == "line":
            x1, y1 = P(_px(f, e["p"][0]), _py(f, e["p"][1]))
            x2, y2 = P(_px(f, e["q"][0]), _py(f, e["q"][1]))
            c.line(x1, y1, x2, y2)
        elif e["t"] == "arc":
            cx, cy = P(_px(f, e["c"][0]), _py(f, e["c"][1]))
            r = e["r"] / f["scale"] * mm
            c.arc(cx - r, cy - r, cx + r, cy + r, startAng=e["a0"], extent=(e["a1"] - e["a0"]) % 360 or 360)
        elif e["t"] == "text":
            _draw_text(c, *P(_px(f, e["p"][0]), _py(f, e["p"][1])), e["s"], e["h"], e["anchor"], e["rot"], e["bold"],
                       lay["color"], mm)
    c.setStrokeColor(HexColor("#000000"))
    for item in _title_block(sheet, f):
        if item[0] == "rect":
            c.setLineWidth(0.5 * mm); c.rect(item[1] * mm, (ph - item[4]) * mm, (item[3] - item[1]) * mm, (item[4] - item[2]) * mm)
        elif item[0] == "line":
            c.setLineWidth(0.35 * mm); c.line(item[1] * mm, (ph - item[2]) * mm, item[3] * mm, (ph - item[4]) * mm)
        else:
            _, x, y, s, h, anchor, bold = item
            _draw_text(c, x * mm, (ph - y) * mm, s, h, anchor, 0.0, bold, "#000000", mm)
    c.showPage()


def to_pdf(sheet: Sheet, paper: str | None = None, scale: int | None = None) -> bytes:
    return to_pdf_multi([sheet], paper, scale)


def to_pdf_multi(sheets: list[Sheet], paper: str | None = None, scale: int | None = None) -> bytes:
    """One PDF, one page per sheet, each at its own true standard scale."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle(f"{sheets[0].project} - drawing set")
    c.setAuthor("NitoCAD")
    c.setSubject("Concept design, not for construction. Building-code compliance: UNKNOWN.")
    for sh in sheets:
        _pdf_page(c, sh, paper, scale)
    c.save()
    return buf.getvalue()


def merge_sheets(sheets: list[Sheet], title: str = "DRAWING SET", gap: float = 4000.0) -> Sheet:
    """All sheets in one model space (two columns) for a single DXF, each labelled with its drawing title."""
    merged = Sheet(title, sheets[0].project)
    x_cursor, y_cursor, row_h, col = 0.0, 0.0, 0.0, 0
    for sh in sheets:
        x0, y0, x1, y1 = sh.bounds()
        if col == 2:
            x_cursor, y_cursor, row_h, col = 0.0, y_cursor - row_h - gap, 0.0, 0
        dx, dy = x_cursor - x0, y_cursor - y1
        for e in sh.entities:
            n = dict(e)
            if e["t"] == "poly":
                n["pts"] = [(x + dx, y + dy) for x, y in e["pts"]]
            elif e["t"] == "line":
                n["p"], n["q"] = (e["p"][0] + dx, e["p"][1] + dy), (e["q"][0] + dx, e["q"][1] + dy)
            elif e["t"] in ("arc",):
                n["c"] = (e["c"][0] + dx, e["c"][1] + dy)
            elif e["t"] == "text":
                n["p"] = (e["p"][0] + dx, e["p"][1] + dy)
            merged.entities.append(n)
        merged.text((x_cursor, y_cursor + 1500), f"{sh.sheet_no}  {sh.title}", 5.0, "A-ANNO-TEXT", "start", bold=True)
        row_h = max(row_h, (y1 - y0) + 1500)
        x_cursor += (x1 - x0) + gap
        col += 1
    return merged


def _draw_text(c, x, y, s, h, anchor, rot, bold, color, mm):
    from reportlab.lib.colors import HexColor
    c.saveState()
    c.setFillColor(HexColor(color))
    c.setFont("Helvetica-Bold" if bold else "Helvetica", h * mm)
    c.translate(x, y)
    if rot:
        c.rotate(rot)
    c.translate(0, -h * mm * 0.35)
    if anchor == "middle":
        c.drawCentredString(0, 0, s)
    elif anchor == "end":
        c.drawRightString(0, 0, s)
    else:
        c.drawString(0, 0, s)
    c.restoreState()


# ----------------------------------------------------------------------------- DXF
def to_dxf(sheet: Sheet, scale: int | None = None) -> str:
    """Model space at full size in millimetres. Needs ezdxf (already a project dependency)."""
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    den = scale or fit(sheet)["scale"]
    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4  # millimetres
    for name, lay in LAYERS.items():
        doc.layers.add(name, color=lay["dxf"])
    msp = doc.modelspace()
    align = {"start": TextEntityAlignment.LEFT, "middle": TextEntityAlignment.CENTER, "end": TextEntityAlignment.RIGHT}
    for e in sheet.entities:
        attr = {"layer": e["layer"]}
        if e["t"] == "poly":
            if e["fill"]:
                hatch = msp.add_hatch(color=LAYERS.get(e["layer"], LAYERS["A-WALL"])["dxf"], dxfattribs=attr)
                hatch.paths.add_polyline_path(e["pts"], is_closed=True)
            else:
                msp.add_lwpolyline(e["pts"], close=e["closed"], dxfattribs=attr)
        elif e["t"] == "line":
            msp.add_line(e["p"], e["q"], dxfattribs=attr)
        elif e["t"] == "arc":
            msp.add_arc(e["c"], e["r"], e["a0"], e["a1"], dxfattribs=attr)
        elif e["t"] == "text":
            t = msp.add_text(e["s"], dxfattribs={**attr, "height": e["h"] * den, "rotation": e["rot"]})
            t.set_placement(e["p"], align=align[e["anchor"]])
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue()
