"""
Pictures to flat shapes: photos, scans and screenshots of a part or a 2D drawing.

This is the missing step between "image" and "CAD". It turns a PNG, JPG or WEBP into a
profiles.Profile, the same structure the DXF and SVG readers produce, so a traced picture
extrudes, edits and exports exactly like any other drawing.

Two kinds of picture are understood and the right one is picked automatically:

  silhouette   a filled part against a plain background (a photo from above, a cut-out, a
               transparent PNG). Each filled region becomes an outline and each enclosed gap
               becomes a hole.
  outline      a line drawing (a scan, a screenshot, a plotted drawing). The closed regions the
               lines enclose are found and moved out to the middle of the line. Something
               enclosed inside another outline is a hole, the same even-odd rule the DXF reader
               uses. A sheet border that frames the whole picture is dropped.

What comes out is a proposal the person reviews, never geometry trusted blindly:

  * Size comes only from a number the person gives (pixels per mm, or the real width or height of
    the whole part). With none, the shape is returned at 1 pixel = 1 mm and flagged, so it can be
    sized afterwards with the normal "make it 150 mm long" edit. Nothing is guessed.
  * Round shapes that are round within the pixel noise stay exact circles, so a hole reads as
    "6 mm diameter" and not as a 40-sided polygon. Everything else is a polygon that follows the
    edge to about one pixel.
  * Accuracy is about half a pixel. The report says what that is in millimetres.
  * Text, dimension marks and specks leave small closed shapes (the inside of an "O"). Anything
    below a small area is ignored and counted.

Straight-on views only. A tilted photo or a lens-distorted one traces as a tilted or distorted
shape, and the warnings say so.

Heavy libraries (OpenCV, NumPy, Pillow, pypdfium2) are imported only when a function needs them,
so this module imports cleanly without them. `available()` says whether tracing can run.
"""
from __future__ import annotations

import importlib.util
import io
import math
from typing import Any

from profiles import Point, Profile, ProfileError, build_profile, signed_area


class TraceUnavailable(RuntimeError):
    """OpenCV, NumPy or Pillow is not installed on this server. Safe to show."""


MAX_INPUT_PIXELS = 60_000_000      # refuse decompression bombs before decoding
WORK_LONGEST_SIDE = 2000           # pictures are shrunk to this before tracing
MIN_SIDE_PX = 64
SIMPLIFY_PX = 1.0                  # polygon edges follow the picture to this many pixels
CIRCLE_MIN_RADIUS_PX = 4.0
CIRCLE_MAX_DEVIATION = 0.04        # largest radial error, as a fraction of the radius
MIN_MARK_FRACTION = 0.0003         # smaller closed shapes than this share of the picture are marks, not parts
MIN_MARK_PX = 150
FRAME_FRACTION = 0.90              # a region this big in both directions is the sheet border
MAX_SHAPES = 400


def available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("cv2", "numpy", "PIL"))


def _libs():
    try:
        import cv2
        import numpy as np
        from PIL import Image, ImageOps
    except ImportError as e:                                    # pragma: no cover - depends on the server
        raise TraceUnavailable("Tracing pictures is not available on this server") from e
    return cv2, np, Image, ImageOps


# ------------------------------------------------------------------ reading the picture
def _decode(data: bytes):
    """Return (gray uint8, alpha uint8 or None, (width, height) of the original)."""
    cv2, np, Image, ImageOps = _libs()
    Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:                                      # noqa: BLE001 - any decoder failure is "cannot open"
        raise ProfileError("That image could not be opened") from e
    img = ImageOps.exif_transpose(img)
    w, h = img.size
    if min(w, h) < MIN_SIDE_PX:
        raise ProfileError(f"That image is too small to trace. Use one at least {MIN_SIDE_PX} pixels on each side")
    alpha = None
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        a = np.array(rgba)[:, :, 3]
        solid = float((a >= 128).mean())
        if 0.001 < solid < 0.999:                               # a real cut-out: the alpha channel is the shape
            alpha = a
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[3])
        img = flat
    gray = np.array(img.convert("L"))
    return gray, alpha, (w, h)


def _foreground(gray, alpha) -> tuple[Any, str]:
    """Boolean mask of the part (or of the lines) and how it was found."""
    cv2, np, _, _ = _libs()
    if alpha is not None:
        return alpha >= 128, "transparency"
    if int(gray.max()) - int(gray.min()) < 30:
        raise ProfileError("The picture has almost no contrast, so the shape cannot be told from the background")
    blur = cv2.GaussianBlur(gray, (3, 3), 0)         # enough for sensor noise, gentle enough for 1 pixel lines
    t, _ = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = blur <= t
    h, w = dark.shape
    m = max(2, int(0.02 * min(h, w)))
    border = np.concatenate([dark[:m].ravel(), dark[-m:].ravel(), dark[:, :m].ravel(), dark[:, -m:].ravel()])
    fg = ~dark if float(border.mean()) > 0.5 else dark         # the background is whatever holds the border
    return fg, "brightness"


def _ink_ridge(fg):
    """Distance from every ink pixel to the nearest paper pixel, and the ridge (middle line) of the strokes."""
    cv2, np, _, _ = _libs()
    dt = cv2.distanceTransform(fg.astype(np.uint8), cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    ridge = (dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8)) - 1e-6) & (dt > 0.5)
    return dt, ridge


def _half_width(values, default: float) -> float:
    """Half the line width from ridge distances. A line w pixels wide has a ridge distance of (w + 1) / 2."""
    _, np, _, _ = _libs()
    return max(0.0, float(np.median(values)) - 0.5) if len(values) >= 5 else default


def _is_outline(fg) -> bool:
    """Thin strokes vanish under a small erosion, a filled part does not."""
    cv2, np, _, _ = _libs()
    ink = int(fg.sum())
    if ink == 0:
        return False
    r = max(2, int(round(0.004 * min(fg.shape))))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    solid = cv2.erode(fg.astype(np.uint8), k)
    return float(solid.sum()) / ink < 0.25


# --------------------------------------------------------------------- one region to a shape
def _offset_polygon(pts: list[Point], d: float) -> list[Point]:
    """Move a polygon outward by d (negative = inward) along its corner bisectors, with a miter limit."""
    n = len(pts)
    if n < 3 or d == 0:
        return pts
    ring = pts if signed_area(pts) > 0 else pts[::-1]
    flipped = ring is not pts
    out: list[Point] = []
    for i in range(n):
        p0, p1, p2 = ring[i - 1], ring[i], ring[(i + 1) % n]
        e1x, e1y = p1[0] - p0[0], p1[1] - p0[1]
        e2x, e2y = p2[0] - p1[0], p2[1] - p1[1]
        l1, l2 = math.hypot(e1x, e1y), math.hypot(e2x, e2y)
        if l1 < 1e-9 or l2 < 1e-9:
            out.append(p1)
            continue
        n1 = (e1y / l1, -e1x / l1)
        n2 = (e2y / l2, -e2x / l2)
        k = max(1.0 + n1[0] * n2[0] + n1[1] * n2[1], 0.5)       # miter limit: a sharp corner moves at most 2d
        out.append((p1[0] + d * (n1[0] + n2[0]) / k, p1[1] + d * (n1[1] + n2[1]) / k))
    return out[::-1] if flipped else out


def _region_shape(mask, ox: int, oy: int, grow: float = 0.0):
    """Outline one filled region (a uint8 crop at offset ox, oy), optionally grown outward by `grow` pixels.

    Returns ("circle", cx, cy, r, area_px) or ("polygon", points, area_px) in picture pixels with
    y pointing down, or None if the region is empty. The region is traced at twice the picture's
    resolution so the edge is placed within a quarter pixel. A circle's size comes from counting
    pixels, which has no edge bias at all."""
    cv2, np, _, _ = _libs()
    if not mask.any():
        return None
    up = cv2.resize(mask, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    g2 = int(round(2.0 * grow))
    if g2 >= 1:
        up = cv2.dilate(up, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g2 + 1, 2 * g2 + 1)))
    area = float(up.sum()) / 4.0
    cnts, _ = cv2.findContours(up, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    c = max(cnts, key=lambda q: len(q))
    pts = [(float(p[0][0]) / 2.0 - 0.25 + ox, float(p[0][1]) / 2.0 - 0.25 + oy) for p in c]
    m = cv2.moments(up, binaryImage=True)
    cx, cy = m["m10"] / m["m00"] / 2.0 - 0.25 + ox, m["m01"] / m["m00"] / 2.0 - 0.25 + oy
    r = math.sqrt(area / math.pi)
    if r >= CIRCLE_MIN_RADIUS_PX and len(pts) >= 12:
        dev = max(abs(math.hypot(x - cx, y - cy) - r) for x, y in pts) / r
        if dev <= max(CIRCLE_MAX_DEVIATION, 1.0 / r):
            return ("circle", cx, cy, r, area)
    approx = cv2.approxPolyDP(np.array(pts, dtype=np.float32).reshape(-1, 1, 2), SIMPLIFY_PX, True)
    poly = [(float(p[0][0]), float(p[0][1])) for p in approx]
    if len(poly) < 3:
        return None
    return ("polygon", _offset_polygon(poly, 0.25), area)


def _components(mask, connectivity: int):
    cv2, np, _, _ = _libs()
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=connectivity)
    return n, lab, stats


def _guard_count(stats, min_mark: int) -> None:
    """Refuse a noisy picture before the per-region work starts."""
    _, np, _, _ = _libs()
    if int((stats[1:, 4] >= min_mark).sum()) > 4 * MAX_SHAPES:
        raise ProfileError("The picture has too many separate shapes to trace. Crop it to the part, or use a cleaner scan")


def _crop(lab, stats, i: int, pad: int):
    _, np, _, _ = _libs()
    x, y, w, h = (int(stats[i, k]) for k in (0, 1, 2, 3))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(lab.shape[1], x + w + pad), min(lab.shape[0], y + h + pad)
    return (lab[y0:y1, x0:x1] == i).astype(np.uint8), x0, y0


def _silhouette_regions(fg, min_mark: int, notes: list[str]):
    """Filled parts and their holes. Returns (regions, marks ignored)."""
    cv2, np, _, _ = _libs()
    h, w = fg.shape
    regions: list[Any] = []
    ignored = 0
    n, lab, stats = _components(fg, 8)
    _guard_count(stats, min_mark)
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if area < min_mark:
            ignored += 1
            continue
        if bw >= FRAME_FRACTION * w and bh >= FRAME_FRACTION * h:
            raise ProfileError("The part fills the whole picture, or the background is darker than the part. "
                               "Use a plain, even background and leave a margin around the part")
        m, ox, oy = _crop(lab, stats, i, 1)
        filled = m.copy()
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(filled, cnts, -1, 1, thickness=cv2.FILLED)
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            notes.append("A shape touches the edge of the picture, so it may be cut off")
        reg = _region_shape(filled, ox, oy)
        if reg:
            regions.append(reg)
    nb, labb, statsb = _components(~fg, 4)
    _guard_count(statsb, min_mark)
    for i in range(1, nb):
        x, y, bw, bh, area = (int(v) for v in statsb[i])
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            continue                                            # touches the border: outside the part
        if area < min_mark:
            ignored += 1
            continue
        m, ox, oy = _crop(labb, statsb, i, 1)
        reg = _region_shape(m, ox, oy)
        if reg:
            regions.append(reg)
    return regions, ignored


def _outline_regions(fg, min_mark: int, notes: list[str]):
    """Closed regions enclosed by drawn lines, moved out to the middle of the line.

    The line width is measured around each region, so a thin dimension line and a thick outline in the
    same drawing each get their own offset."""
    cv2, np, _, _ = _libs()
    h, w = fg.shape
    dt, ridge = _ink_ridge(fg)
    sr_global = _half_width(dt[ridge], 1.0)
    band = int(math.ceil(2.0 * sr_global)) + 3
    kb = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
    regions: list[Any] = []
    ignored = 0
    frame = False
    nb, labb, statsb = _components(~fg, 4)
    _guard_count(statsb, min_mark)
    for i in range(1, nb):
        x, y, bw, bh, area = (int(v) for v in statsb[i])
        if x == 0 or y == 0 or x + bw >= w or y + bh >= h:
            continue                                            # open to the outside: paper, not a shape
        if area < min_mark:
            ignored += 1
            continue
        if bw >= FRAME_FRACTION * w and bh >= FRAME_FRACTION * h:
            frame = True                                        # the border of the sheet
            continue
        m, ox, oy = _crop(labb, statsb, i, band + 3)
        near = cv2.dilate(m, kb).astype(bool) & ~m.astype(bool)
        sl = (slice(oy, oy + m.shape[0]), slice(ox, ox + m.shape[1]))
        grow = _half_width(dt[sl][ridge[sl] & near], sr_global)
        reg = _region_shape(m, ox, oy, grow)
        if reg:
            regions.append(reg)
    return regions, ignored, frame, sr_global


# ----------------------------------------------------------------------- scale and output
def _area(o: dict[str, Any]) -> float:
    return math.pi * o["r"] ** 2 if o["type"] == "circle" else abs(signed_area([tuple(p) for p in o["points"]]))


def _extent(prof: Profile) -> tuple[float, float, float, float]:
    return prof.bbox()


def _rescale(prof: Profile, s: float, x0: float, y0: float) -> Profile:
    """Move the lower left corner to the origin and multiply every length by s."""
    def one(o: dict[str, Any]) -> dict[str, Any]:
        if o["type"] == "circle":
            return {"type": "circle", "cx": round((o["cx"] - x0) * s, 6), "cy": round((o["cy"] - y0) * s, 6), "r": round(o["r"] * s, 6)}
        return {"type": "polygon", "points": [[round((px - x0) * s, 6), round((py - y0) * s, 6)] for px, py in o["points"]]}
    shapes = [{"outer": one(sh["outer"]), "holes": [one(h) for h in sh["holes"]]} for sh in prof.shapes]
    return Profile(shapes=shapes, warnings=list(prof.warnings), source=prof.source, suggested_thickness_mm=prof.suggested_thickness_mm)


def _check_positive(name: str, v: float | None) -> None:
    if v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 or v > 1e6):
        raise ProfileError(f"{name} must be a positive number")


def trace_image(data: bytes, *, mm_per_px: float | None = None, overall_width_mm: float | None = None,
                overall_height_mm: float | None = None, mode: str = "auto", largest_only: bool = False,
                thickness_mm: float = 3.0) -> tuple[Profile, dict[str, Any]]:
    """Trace a picture into a Profile. Returns (profile, report).

    Size, in order of preference: mm_per_px (millimetres per pixel of the picture as uploaded), or the
    real width or height of the whole traced part. With neither, 1 pixel = 1 mm and the report says
    needs_scale. mode is "auto", "silhouette" or "outline". largest_only keeps only the biggest shape
    (useful when a drawing has a title block or several views)."""
    cv2, np, _, _ = _libs()
    for name, v in (("mm_per_px", mm_per_px), ("overall_width_mm", overall_width_mm), ("overall_height_mm", overall_height_mm)):
        _check_positive(name, v)
    if mode not in ("auto", "silhouette", "outline"):
        raise ProfileError("mode must be auto, silhouette or outline")

    gray, alpha, (ow, oh) = _decode(data)
    f = min(1.0, WORK_LONGEST_SIDE / float(max(ow, oh)))
    if f < 1.0:
        size = (max(1, int(round(ow * f))), max(1, int(round(oh * f))))
        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
        if alpha is not None:
            alpha = cv2.resize(alpha, size, interpolation=cv2.INTER_AREA)
    h, w = gray.shape
    f = w / float(ow)                                           # the real shrink factor after rounding

    fg, found_by = _foreground(gray, alpha)
    frac = float(fg.mean())
    if frac < 0.0005 or frac > 0.95:
        raise ProfileError("The shape cannot be told from the background. Use a dark part on a plain light "
                           "background, or a drawing with dark lines on white")
    picked = mode
    if mode == "auto":
        picked = "silhouette" if found_by == "transparency" else ("outline" if _is_outline(fg) else "silhouette")
    min_mark = max(MIN_MARK_PX, int(MIN_MARK_FRACTION * w * h))
    notes: list[str] = []
    frame = False
    stroke_px = None
    if picked == "outline":
        regions, ignored, frame, stroke_px = _outline_regions(fg, min_mark, notes)
    else:
        regions, ignored = _silhouette_regions(fg, min_mark, notes)
    if not regions:
        raise ProfileError("No closed shape was found in the picture. For a photo or silhouette use a dark part on a "
                           "plain light background. For a line drawing, make sure the outline is one closed line")
    if ignored > 500 and ignored > 20 * len(regions):
        raise ProfileError("The picture is too noisy or textured to trace. Use a clean scan, or a photo of the part on a plain background")
    if len(regions) > MAX_SHAPES:
        raise ProfileError("The picture has too many separate shapes to trace. Crop it to the part")

    loops: list[list[Point]] = []
    circles: list[tuple[float, float, float]] = []
    for reg in regions:
        if reg[0] == "circle":
            _, cx, cy, r, _a = reg
            circles.append((cx, -cy, r))                       # y up from here on
        else:
            loops.append([(x, -y) for x, y in reg[1]])
    prof = build_profile(loops, circles, source="image", warnings=notes, thickness=thickness_mm)

    if largest_only and len(prof.shapes) > 1:
        keep = max(prof.shapes, key=lambda sh: _area(sh["outer"]))
        dropped = len(prof.shapes) - 1
        prof = Profile(shapes=[keep], warnings=list(prof.warnings), source="image", suggested_thickness_mm=thickness_mm)
        prof.warnings.append(f"Kept the largest shape and dropped {dropped} other{'s' if dropped != 1 else ''}")

    x0, y0, x1, y1 = _extent(prof)
    wpx, hpx = x1 - x0, y1 - y0
    warnings = list(prof.warnings)
    scale_source, needs_scale = "none", False
    if mm_per_px is not None:
        s, scale_source = mm_per_px / f, "mm_per_px"
    elif overall_width_mm is not None:
        s, scale_source = overall_width_mm / wpx, "overall_width"
        if overall_height_mm is not None and abs(hpx * s - overall_height_mm) > 0.04 * overall_height_mm:
            warnings.append(f"The width and height you gave do not match the picture's shape (the height comes out "
                            f"{hpx * s:.1f} mm, not {overall_height_mm:g} mm). The picture may be stretched or tilted")
    elif overall_height_mm is not None:
        s, scale_source = overall_height_mm / hpx, "overall_height"
    else:
        s, needs_scale = 1.0 / f, True
        warnings.append("No scale was given, so 1 pixel of the picture is 1 mm. Set the real size before building, "
                        "for example \"make it 150 mm long\"")
    out = _rescale(prof, s, x0, y0)
    out.warnings = warnings
    if len(out.shapes) > 1:
        out.warnings.append(f"{len(out.shapes)} separate shapes were found. If you only want the part, "
                            f"trace again with the largest shape only")
    if frame:
        out.warnings.append("The sheet border was ignored")
    if ignored:
        out.warnings.append(f"{ignored} small mark{'s' if ignored != 1 else ''} (text, specks or dimension marks) ignored")
    out.warnings.append("Traced from a picture, so a tilted view or lens distortion shows up as a skewed shape. "
                        "Check the size and the holes against your drawing before you build")
    s_orig = s * f                                              # millimetres per pixel of the uploaded picture
    report = {
        "mode": picked, "found_by": found_by, "scale_source": scale_source, "needs_scale": needs_scale,
        "mm_per_px": s_orig, "accuracy_mm": round(0.5 * s_orig, 4), "image_px": [ow, oh], "shapes": len(out.shapes),
        "round_shapes": len(circles), "ignored_marks": ignored, "frame_removed": frame,
        "stroke_px": None if stroke_px is None else round((2 * stroke_px) / f, 2),
    }
    return out, report


def trace_pdf_page(data: bytes, *, page: int = 0, dpi: float = 200.0, **opts: Any) -> tuple[Profile, dict[str, Any]]:
    """Trace one page of a PDF drawing. The page is drawn to a picture at a known size, so the scale
    is the page's own: it is exact only if the sheet was printed at 1:1, and the report says so."""
    try:
        import pypdfium2 as pdfium
    except ImportError as e:                                    # pragma: no cover
        raise TraceUnavailable("Reading PDF pages as pictures is not available on this server") from e
    try:
        pdf = pdfium.PdfDocument(data)
        pg = pdf[page]
        w_pt, h_pt = pg.get_size()
        scale = min(dpi / 72.0, 4000.0 / max(w_pt, h_pt))
        png = io.BytesIO()
        pg.render(scale=scale).to_pil().convert("RGB").save(png, "PNG")
    except Exception as e:                                      # noqa: BLE001
        raise ProfileError("That PDF page could not be drawn") from e
    mm_per_px = (25.4 / 72.0) / scale
    if opts.get("mm_per_px") is None and opts.get("overall_width_mm") is None and opts.get("overall_height_mm") is None:
        opts = {**opts, "mm_per_px": mm_per_px}
        prof, rep = trace_image(png.getvalue(), **opts)
        prof.warnings.append("The size is the size of the printed sheet. It is right only if the drawing was printed at 1:1, "
                             "so check the scale in the title block")
        rep["scale_source"] = "pdf_page_1_to_1"
        return prof, rep
    return trace_image(png.getvalue(), **opts)


# ----------------------------------------------------------------------------- preview
def preview_svg(prof: Profile, max_points: int = 3000) -> str:
    """A small SVG of the traced shape for the person to review. Built from numbers only."""
    x0, y0, x1, y1 = prof.bbox()
    pad = max(x1 - x0, y1 - y0) * 0.04 + 1e-6
    total = sum(len(o["points"]) for sh in prof.shapes for o in [sh["outer"], *sh["holes"]] if o["type"] == "polygon")
    stride = max(1, math.ceil(total / max_points))

    def path(o: dict[str, Any]) -> str:
        if o["type"] == "circle":
            cx, cy, r = o["cx"], o["cy"], o["r"]
            return f"M{cx - r:.2f} {cy:.2f}a{r:.2f} {r:.2f} 0 1 0 {2 * r:.2f} 0a{r:.2f} {r:.2f} 0 1 0 {-2 * r:.2f} 0Z"
        pts = o["points"][::stride]
        return "M" + "L".join(f"{p[0]:.2f} {p[1]:.2f}" for p in pts) + "Z"

    d = "".join(path(o) for sh in prof.shapes for o in [sh["outer"], *sh["holes"]])
    vb = f"{x0 - pad:.2f} {-(y1 + pad):.2f} {x1 - x0 + 2 * pad:.2f} {y1 - y0 + 2 * pad:.2f}"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">'
            f'<g transform="scale(1,-1)"><path d="{d}" fill="#4f8cff" fill-opacity="0.25" fill-rule="evenodd" '
            f'stroke="#4f8cff" stroke-width="1.5" vector-effect="non-scaling-stroke"/></g></svg>')
