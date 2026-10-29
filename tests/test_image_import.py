"""Reading an uploaded picture (image_import.py): tracing and the vision model working together.

The vision model and the file store are stand-ins, so no key, network or database is needed."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")
import file_analysis as fa  # noqa: E402
import image_import as II  # noqa: E402
import profiles as P  # noqa: E402


def png(arr) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def plate() -> bytes:
    img = np.full((800, 1200), 255, np.uint8)
    cv2.rectangle(img, (100, 100), (1100, 700), 0, -1)
    cv2.circle(img, (600, 400), 50, 255, -1)
    return png(img)


VISION = {"summary": "A plate.", "part_type_guess": "plate", "description": "A plate, 100 mm wide.", "features": [], "missing": [], "notes": [],
          "dimensions": [{"label": "overall length", "value_mm": 100.0, "raw": "100", "confidence": "high"}]}


class Store:
    def __init__(self):
        self.saved = []

    def __call__(self, kind, payload, meta, warnings):
        self.saved.append((kind, json.loads(payload), meta))
        return f"imp{len(self.saved)}"


def vision_ok(images, context):
    vision_ok.calls.append(len(images))
    return VISION


vision_ok.calls = []


def vision_down(images, context):
    raise RuntimeError("provider down")


class TestTracingOnly:
    def test_a_picture_becomes_an_editable_shape(self):
        store = Store()
        out = II.analyze_image(plate(), options={"overall_width_mm": 100}, vision=None, keep=store)
        a = out["analysis"]
        assert out["import_id"] == "imp1" and store.saved[0][0] == "image"
        assert a["width_mm"] == pytest.approx(100, abs=0.05) and a["round_holes"][0]["diameter_mm"] == pytest.approx(10, abs=0.05)
        assert a["trace"]["mode"] == "silhouette" and a["preview_svg"].startswith("<svg")
        assert out["description"].startswith("A flat part about 100.0 x 60.0 mm")

    def test_the_stored_copy_rebuilds_the_same_profile(self):
        store = Store()
        II.analyze_image(plate(), options={"mm_per_px": 0.1}, vision=None, keep=store)
        prof = P.Profile.from_json(store.saved[0][1])
        assert prof.stats()["width_mm"] == pytest.approx(100.1, abs=0.02)      # drawn 1001 pixels wide at 0.1 mm per pixel

    def test_unscaled_result_says_so(self):
        out = II.analyze_image(plate(), options={}, vision=None, keep=Store())
        assert out["analysis"]["trace"]["needs_scale"] and any("No scale" in w for w in out["warnings"])

    def test_an_untraceable_picture_is_refused_with_the_reason(self):
        with pytest.raises(fa.FileRejected, match="contrast"):
            II.analyze_image(png(np.full((200, 200), 180, np.uint8)), options={}, vision=None, keep=Store())

    def test_a_file_that_is_not_a_picture_never_reaches_any_reader(self):
        vision_ok.calls.clear()
        with pytest.raises(fa.FileRejected, match="could not be opened"):
            II.analyze_image(b"\x89PNG\r\n\x1a\n" + b"junk" * 20, options={}, vision=vision_ok, keep=Store())
        assert vision_ok.calls == []

    def test_nothing_available_is_reported_as_unavailable(self):
        with pytest.raises(II.ReaderUnavailable):
            II.analyze_image(plate(), options={}, vision=None, keep=Store(), tracing=False)

    def test_a_full_account_is_a_warning_not_a_failure(self):
        def full(kind, payload, meta, warnings):
            warnings.append("Your account is full")
            return None
        out = II.analyze_image(plate(), options={}, vision=None, keep=full)
        assert out["import_id"] is None and "Your account is full" in out["warnings"]


class TestWithVision:
    def test_both_readers_contribute(self):
        out = II.analyze_image(plate(), options={"mm_per_px": 0.1}, vision=vision_ok, keep=Store())
        assert out["analysis"]["vision"]["part_type_guess"] == "plate" and out["analysis"]["width_mm"] == pytest.approx(100.1, abs=0.02)
        assert out["description"].startswith("A flat part")            # the measured shape wins over the model's words

    def test_a_printed_dimension_is_suggested_never_applied(self):
        out = II.analyze_image(plate(), options={}, vision=vision_ok, keep=Store())
        assert out["analysis"]["width_mm"] == pytest.approx(1000, abs=2)           # still unscaled
        assert any("overall length" in w and "100" in w for w in out["warnings"])

    def test_no_suggestion_when_the_person_already_gave_a_scale(self):
        out = II.analyze_image(plate(), options={"overall_width_mm": 100}, vision=vision_ok, keep=Store())
        assert not any("make it" in w for w in out["warnings"])

    def test_vision_failure_keeps_the_traced_shape(self):
        out = II.analyze_image(plate(), options={"mm_per_px": 0.1}, vision=vision_down, keep=Store())
        assert out["import_id"] and "vision" not in out["analysis"] and any("did not answer" in w for w in out["warnings"])

    def test_a_failed_trace_falls_back_to_the_vision_model(self):
        out = II.analyze_image(png(np.full((200, 200), 180, np.uint8)), options={}, vision=vision_ok, keep=Store())
        assert out["import_id"] is None and out["analysis"]["vision"] and any("could not be traced" in w for w in out["warnings"])

    def test_both_failing_is_a_gateway_error(self):
        with pytest.raises(II.ReaderFailed):
            II.analyze_image(png(np.full((200, 200), 180, np.uint8)), options={}, vision=vision_down, keep=Store())


class TestCard:
    def test_the_card_shows_scale_accuracy_and_preview(self):
        out = II.analyze_image(plate(), options={"overall_width_mm": 100}, vision=None, keep=Store())
        card = fa.card_from_result("part.png", "png", 1234, out)
        rows = dict(card["rows"])
        assert rows["Traced as"] == "a filled shape" and rows["Size from"] == "the width you gave" and rows["Accuracy"].startswith("about ")
        assert card["editable"] and card["preview_svg"].startswith("<svg") and "Flat size" in rows

    def test_unscaled_card_says_the_size_is_not_set(self):
        out = II.analyze_image(plate(), options={}, vision=None, keep=Store())
        assert "set the real size" in dict(fa.card_from_result("p.png", "png", 1, out)["rows"])["Size from"]


class TestOptions:
    def test_only_known_options_pass_and_empty_ones_drop(self):
        assert II.clean_options({"mm_per_px": None, "overall_width_mm": 50, "mode": "auto", "largest_only": False, "evil": 1}) == \
            {"overall_width_mm": 50, "mode": "auto"}


class TestPdf:
    def test_a_traced_pdf_page_is_editable(self):
        pytest.importorskip("pypdfium2")
        canvas = pytest.importorskip("reportlab.pdfgen.canvas")
        mm = pytest.importorskip("reportlab.lib.units").mm
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(210 * mm, 297 * mm))
        c.rect(30 * mm, 150 * mm, 100 * mm, 60 * mm)
        c.save()
        store = Store()
        got = II.trace_pdf(buf.getvalue(), options={}, keep=store)
        assert got["import_id"] == "imp1" and got["analysis"]["width_mm"] == pytest.approx(100, abs=0.6)
        assert any("1:1" in w for w in got["warnings"])
