"""Picture to flat shape (raster_trace.py).

Every picture here is drawn by the test with known sizes, so the expected answer is worked out
from how it was drawn, not read back from the tracer. Needs OpenCV, NumPy and Pillow (skipped
where they are missing). The PDF test also needs pypdfium2 and reportlab."""
from __future__ import annotations

import io
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
import profiles as P  # noqa: E402
import raster_trace as R  # noqa: E402

PX_PER_MM = 10          # the plates below are drawn at 10 pixels per millimetre
X0, Y0 = 100, 100       # and sit inside a larger canvas


def png(arr, mode=None) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr, mode).save(buf, "PNG")
    return buf.getvalue()


def jpeg(arr, quality=60) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def plate_silhouette() -> "np.ndarray":
    """A 100 x 60 mm plate with two 8 mm holes, filled dark on white."""
    img = np.full((800, 1200), 255, np.uint8)
    cv2.rectangle(img, (X0, Y0), (X0 + 1000, Y0 + 600), 0, -1)
    for cx in (250, 750):
        cv2.circle(img, (X0 + cx, Y0 + 300), 40, 255, -1)
    return img


def plate_drawing() -> "np.ndarray":
    """The same plate as a line drawing with a sheet border and a line of text."""
    img = np.full((800, 1200), 255, np.uint8)
    cv2.rectangle(img, (10, 10), (1189, 789), 0, 3)
    cv2.rectangle(img, (X0, Y0), (X0 + 1000, Y0 + 600), 0, 3)
    for cx in (250, 750):
        cv2.circle(img, (X0 + cx, Y0 + 300), 40, 0, 3)
    cv2.putText(img, "PART 06 A", (100, 760), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 0, 3)
    return img


class TestSilhouette:
    def test_size_holes_and_scale_from_the_overall_width(self):
        prof, rep = R.trace_image(png(plate_silhouette()), overall_width_mm=100)
        st = prof.stats()
        assert rep["mode"] == "silhouette" and rep["scale_source"] == "overall_width" and not rep["needs_scale"]
        assert st["width_mm"] == pytest.approx(100, abs=0.01) and st["height_mm"] == pytest.approx(60, abs=0.15)
        assert st["islands"] == 1 and st["other_cutouts"] == 0
        assert len(st["round_holes"]) == 1 and st["round_holes"][0]["count"] == 2
        assert st["round_holes"][0]["diameter_mm"] == pytest.approx(8.0, abs=0.05)

    def test_holes_are_exact_circles_not_polygons(self):
        prof, _ = R.trace_image(png(plate_silhouette()), mm_per_px=0.1)
        assert all(h["type"] == "circle" for h in prof.shapes[0]["holes"])

    def test_a_rectangle_has_four_corners(self):
        img = np.full((600, 900), 255, np.uint8)
        cv2.rectangle(img, (100, 100), (800, 500), 0, -1)
        prof, _ = R.trace_image(png(img), mm_per_px=0.5)
        assert len(prof.shapes[0]["outer"]["points"]) == 4

    def test_origin_is_the_lower_left_corner_and_y_points_up(self):
        img = np.full((600, 900), 255, np.uint8)
        cv2.rectangle(img, (100, 100), (400, 200), 0, -1)         # 300 x 100 px, near the top of the picture
        cv2.rectangle(img, (100, 100), (150, 400), 0, -1)         # a leg going down the picture: an L
        prof, _ = R.trace_image(png(img), mm_per_px=1.0)
        pts = prof.shapes[0]["outer"]["points"]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        assert min(xs) == pytest.approx(0, abs=0.01) and min(ys) == pytest.approx(0, abs=0.01)
        # the leg points down in the picture, so after the flip the long thin part is at the bottom of y = 0..300
        wide = [p for p in pts if p[0] > 250]
        assert wide and all(p[1] >= 200 - 1 for p in wide)

    def test_white_part_on_a_black_background_is_found_by_the_border(self):
        img = np.zeros((800, 1200), np.uint8)
        cv2.rectangle(img, (X0, Y0), (X0 + 1000, Y0 + 600), 255, -1)
        cv2.circle(img, (X0 + 500, Y0 + 300), 50, 0, -1)
        prof, _ = R.trace_image(png(img), mm_per_px=0.1)
        assert prof.stats()["width_mm"] == pytest.approx(100, abs=0.15)
        assert prof.stats()["round_holes"][0]["diameter_mm"] == pytest.approx(10, abs=0.05)

    def test_a_transparent_png_uses_its_alpha_as_the_shape(self):
        rgba = np.zeros((800, 1200, 4), np.uint8)
        rgba[Y0:Y0 + 600, X0:X0 + 1000] = (30, 30, 30, 255)
        cv2.circle(rgba, (X0 + 500, Y0 + 300), 60, (0, 0, 0, 0), -1)
        prof, rep = R.trace_image(png(rgba, "RGBA"), mm_per_px=0.1)
        assert rep["found_by"] == "transparency"
        assert prof.stats()["round_holes"][0]["diameter_mm"] == pytest.approx(12, abs=0.05)

    def test_noisy_jpeg_of_an_l_shape_keeps_area_and_corners(self):
        rng = np.random.default_rng(1)
        img = np.full((800, 1200), 235, np.uint8)
        cv2.fillPoly(img, [np.array([[200, 100], [1000, 100], [1000, 300], [500, 300], [500, 700], [200, 700]], np.int32)], 40)
        noisy = np.clip(img.astype(int) + rng.normal(0, 12, img.shape), 0, 255).astype(np.uint8)
        prof, _ = R.trace_image(jpeg(noisy), mm_per_px=0.5)
        outer = prof.shapes[0]["outer"]
        area = abs(P.signed_area([tuple(p) for p in outer["points"]]))
        assert area == pytest.approx((800 * 200 + 300 * 400) * 0.25, rel=0.01)
        assert len(outer["points"]) <= 8

    def test_a_tilted_rectangle_keeps_its_area(self):
        img = np.full((900, 1200), 255, np.uint8)
        box = cv2.boxPoints(((600, 450), (600, 300), 17)).astype(np.int32)
        cv2.fillPoly(img, [box], 0)
        prof, _ = R.trace_image(png(img), mm_per_px=1.0)
        assert abs(P.signed_area([tuple(p) for p in prof.shapes[0]["outer"]["points"]])) == pytest.approx(600 * 300, rel=0.01)

    def test_uneven_lighting_does_not_move_the_edges(self):
        sil = np.full((800, 1200), 230, np.uint8)
        cv2.rectangle(sil, (200, 150), (1000, 650), 50, -1)
        cv2.circle(sil, (600, 400), 70, 230, -1)
        lit = np.clip(sil * np.linspace(0.75, 1.1, 1200)[None, :], 0, 255).astype(np.uint8)
        prof, _ = R.trace_image(jpeg(lit), mm_per_px=0.25)
        st = prof.stats()
        assert st["width_mm"] == pytest.approx(200, abs=0.5) and st["round_holes"][0]["diameter_mm"] == pytest.approx(35, abs=0.3)

    def test_a_part_touching_the_edge_is_flagged(self):
        img = np.full((600, 800), 240, np.uint8)
        cv2.rectangle(img, (0, 100), (500, 500), 40, -1)
        prof, _ = R.trace_image(png(img))
        assert any("edge of the picture" in w for w in prof.warnings)


class TestLineDrawing:
    def test_frame_text_and_holes(self):
        prof, rep = R.trace_image(png(plate_drawing()), overall_width_mm=100)
        st = prof.stats()
        assert rep["mode"] == "outline" and rep["frame_removed"] and rep["ignored_marks"] >= 1
        assert st["islands"] == 1 and st["width_mm"] == pytest.approx(100, abs=0.01) and st["height_mm"] == pytest.approx(60, abs=0.15)
        assert st["round_holes"][0]["count"] == 2 and st["round_holes"][0]["diameter_mm"] == pytest.approx(8, abs=0.05)

    def test_the_line_width_is_measured(self):
        _, rep = R.trace_image(png(plate_drawing()), mm_per_px=0.1)
        assert rep["stroke_px"] == pytest.approx(5, abs=0.6)         # OpenCV draws thickness 3 about 5 pixels wide

    def test_thick_and_thin_lines_both_land_on_the_line_centre(self):
        for thick in (1, 6):
            img = np.full((800, 1200), 255, np.uint8)
            cv2.rectangle(img, (X0, Y0), (X0 + 1000, Y0 + 600), 0, thick)
            cv2.circle(img, (X0 + 500, Y0 + 300), 100, 0, thick)
            prof, _ = R.trace_image(png(img), mm_per_px=0.1)
            st = prof.stats()
            assert st["width_mm"] == pytest.approx(100, abs=0.25), thick
            assert st["round_holes"][0]["diameter_mm"] == pytest.approx(20, abs=0.25), thick

    def test_a_dash_dot_centre_line_does_not_split_the_part(self):
        img = plate_drawing()
        for x in range(X0 - 40, X0 + 1040, 26):
            cv2.line(img, (x, Y0 + 300), (x + 16, Y0 + 300), 0, 1, cv2.LINE_AA)
        prof, _ = R.trace_image(jpeg(img, 80), mm_per_px=0.1)
        assert prof.stats()["islands"] == 1

    def test_a_title_block_adds_shapes_unless_only_the_largest_is_kept(self):
        img = plate_drawing()
        cv2.rectangle(img, (900, 650), (1150, 780), 0, 3)
        cv2.line(img, (900, 715), (1150, 715), 0, 3)
        every, _ = R.trace_image(png(img), mm_per_px=0.1)
        biggest, _ = R.trace_image(png(img), mm_per_px=0.1, largest_only=True)
        assert len(every.shapes) >= 2 and len(biggest.shapes) == 1
        assert biggest.stats()["width_mm"] == pytest.approx(100, abs=0.1)


class TestScale:
    def test_millimetres_per_pixel_refers_to_the_uploaded_picture_even_when_it_is_shrunk(self):
        big = cv2.resize(plate_silhouette(), None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)       # 3600 px wide
        prof, rep = R.trace_image(png(big), mm_per_px=1 / 30)
        assert rep["image_px"] == [3600, 2400] and prof.stats()["width_mm"] == pytest.approx(100, abs=0.2)
        assert rep["mm_per_px"] == pytest.approx(1 / 30)

    def test_no_scale_is_one_pixel_one_millimetre_and_says_so(self):
        prof, rep = R.trace_image(png(plate_silhouette()))
        assert rep["needs_scale"] and rep["scale_source"] == "none"
        assert prof.stats()["width_mm"] == pytest.approx(1000, abs=1.5)
        assert any("No scale" in w for w in prof.warnings)

    def test_overall_height_alone_sets_the_scale(self):
        prof, rep = R.trace_image(png(plate_silhouette()), overall_height_mm=60)
        assert rep["scale_source"] == "overall_height" and prof.stats()["width_mm"] == pytest.approx(100, abs=0.2)

    def test_width_and_height_that_disagree_warn(self):
        prof, _ = R.trace_image(png(plate_silhouette()), overall_width_mm=100, overall_height_mm=90)
        assert any("do not match" in w for w in prof.warnings)

    @pytest.mark.parametrize("opts", [{"mm_per_px": 0}, {"mm_per_px": -1}, {"overall_width_mm": float("nan")},
                                      {"overall_height_mm": float("inf")}, {"mode": "wireframe"}])
    def test_bad_options_are_refused(self, opts):
        with pytest.raises(P.ProfileError):
            R.trace_image(png(plate_silhouette()), **opts)

    def test_accuracy_is_half_a_pixel(self):
        _, rep = R.trace_image(png(plate_silhouette()), mm_per_px=0.1)
        assert rep["accuracy_mm"] == pytest.approx(0.05)


class TestRefusals:
    @pytest.mark.parametrize("name,arr", [
        ("blank", np.full((300, 300), 200, np.uint8)),
        ("tiny", np.zeros((20, 20), np.uint8)),
    ])
    def test_unusable_pictures_get_a_plain_message(self, name, arr):
        with pytest.raises(P.ProfileError):
            R.trace_image(png(arr))

    def test_not_an_image(self):
        with pytest.raises(P.ProfileError, match="could not be opened"):
            R.trace_image(b"definitely not a picture")

    def test_a_part_that_fills_the_whole_picture(self):
        img = np.full((600, 800), 255, np.uint8)
        cv2.rectangle(img, (5, 5), (794, 594), 0, -1)
        cv2.circle(img, (400, 300), 40, 255, -1)
        with pytest.raises(P.ProfileError):
            R.trace_image(png(img))

    def test_random_noise_is_refused_not_traced(self):
        noise = (np.random.default_rng(3).random((900, 1200)) * 255).astype(np.uint8)
        with pytest.raises(P.ProfileError):
            R.trace_image(png(noise))

    def test_an_open_outline_is_not_a_shape(self):
        img = np.full((600, 800), 255, np.uint8)
        cv2.polylines(img, [np.array([[100, 100], [700, 100], [700, 500], [100, 500]], np.int32)], False, 0, 3)   # one side missing
        with pytest.raises(P.ProfileError, match="closed"):
            R.trace_image(png(img))


class TestPreview:
    def test_preview_is_well_formed_and_carries_no_script(self):
        prof, _ = R.trace_image(png(plate_silhouette()), mm_per_px=0.1)
        svg = R.preview_svg(prof)
        root = ET.fromstring(svg)
        names = {el.tag.split("}")[-1] for el in root.iter()}
        assert names <= {"svg", "g", "path"} and "script" not in svg.lower() and len(svg) < 20000

    def test_big_outlines_are_thinned_for_the_preview(self):
        prof = P.Profile(shapes=[{"outer": {"type": "polygon", "points": [[math.cos(t / 5000 * 6.283) * 50, math.sin(t / 5000 * 6.283) * 50] for t in range(5000)]},
                                  "holes": []}])
        assert len(R.preview_svg(prof, max_points=1000)) < 30000


class TestPdf:
    def test_a_one_to_one_vector_drawing_comes_out_in_millimetres(self):
        pytest.importorskip("pypdfium2")
        canvas = pytest.importorskip("reportlab.pdfgen.canvas")
        mm = pytest.importorskip("reportlab.lib.units").mm
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(210 * mm, 297 * mm))
        c.setLineWidth(0.5)
        c.rect(30 * mm, 150 * mm, 100 * mm, 60 * mm)
        c.circle(55 * mm, 180 * mm, 4 * mm)
        c.circle(105 * mm, 180 * mm, 4 * mm)
        c.save()
        prof, rep = R.trace_pdf_page(buf.getvalue())
        st = prof.stats()
        assert rep["scale_source"] == "pdf_page_1_to_1"
        assert st["width_mm"] == pytest.approx(100, abs=0.6) and st["height_mm"] == pytest.approx(60, abs=0.6)
        assert sum(h["count"] for h in st["round_holes"]) == 2
        assert all(h["diameter_mm"] == pytest.approx(8, abs=0.3) for h in st["round_holes"])
        assert any("1:1" in w for w in prof.warnings)
