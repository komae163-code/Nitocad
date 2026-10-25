"""Self-contained SVG preview of a routed board (no KiCad needed)."""
from __future__ import annotations

from html import escape

from .model import Design, abs_pads, court_bbox

F_COL, B_COL = "#d9534f", "#3a7bd5"


def render_svg(design: Design, scale: float = 12.0) -> str:
    x0, y0, x1, y1 = design.board
    W, H = (x1 - x0) * scale, (y1 - y0) * scale

    def X(v): return round((v - x0) * scale, 2)
    def Y(v): return round((v - y0) * scale, 2)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 -10 {W + 20:.1f} {H + 20:.1f}" '
           f'width="{W + 20:.0f}" height="{H + 20:.0f}" role="img" aria-label="{escape(design.title)}">',
           f'<rect x="0" y="0" width="{W:.1f}" height="{H:.1f}" fill="#1f5c3a" stroke="#d4c24a" stroke-width="1.5" rx="2"/>']
    if design.ground_mode == "plane":
        out.append(f'<rect x="1" y="1" width="{W - 2:.1f}" height="{H - 2:.1f}" fill="{B_COL}" opacity="0.10"/>')
    for t in design.tracks:
        col = F_COL if t.layer == "F.Cu" else B_COL
        out.append(f'<line x1="{X(t.x1)}" y1="{Y(t.y1)}" x2="{X(t.x2)}" y2="{Y(t.y2)}" stroke="{col}" '
                   f'stroke-width="{t.width * scale:.2f}" stroke-linecap="round" opacity="0.9"/>')
    for c in design.components:
        cx0, cy0, cx1, cy1 = court_bbox(c)
        out.append(f'<rect x="{X(cx0)}" y="{Y(cy0)}" width="{(cx1 - cx0) * scale:.2f}" height="{(cy1 - cy0) * scale:.2f}" '
                   f'fill="none" stroke="#ffffff" stroke-width="0.6" stroke-dasharray="3 2" opacity="0.5"/>')
        for pad, px, py, w, h in abs_pads(c):
            col = "#c8b04a" if pad.kind != "smd" else F_COL
            if pad.kind == "thru" and pad.shape == "circle":
                out.append(f'<circle cx="{X(px)}" cy="{Y(py)}" r="{w * scale / 2:.2f}" fill="{col}"/>')
            else:
                out.append(f'<rect x="{X(px - w / 2)}" y="{Y(py - h / 2)}" width="{w * scale:.2f}" height="{h * scale:.2f}" '
                           f'rx="{min(w, h) * scale * 0.15:.2f}" fill="{col}"/>')
            if pad.kind in ("thru", "np") and pad.drill:
                out.append(f'<circle cx="{X(px)}" cy="{Y(py)}" r="{pad.drill * scale / 2:.2f}" fill="#101010"/>')
        out.append(f'<text x="{X((cx0 + cx1) / 2)}" y="{Y(cy0) - 2}" font-size="7" fill="#f2f2f2" '
                   f'text-anchor="middle" font-family="monospace">{escape(c.ref)}</text>')
    for v in design.vias:
        out.append(f'<circle cx="{X(v.x)}" cy="{Y(v.y)}" r="{v.dia * scale / 2:.2f}" fill="#bbbbbb"/>'
                   f'<circle cx="{X(v.x)}" cy="{Y(v.y)}" r="{v.drill * scale / 2:.2f}" fill="#101010"/>')
    out.append("</svg>")
    return "\n".join(out)
