"""
HTTP tests for POST /files/analyze and GET /files/capabilities (file_api.py):
who may call it, what is refused, and what comes back for each kind of file.

The vision model is replaced by a stand-in, so no key or network is needed.
STEP reading needs CadQuery and runs the real isolated reader.
"""
from __future__ import annotations

import base64
import io
import time

import pytest

pytestmark = pytest.mark.requires_cadquery


def _png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buf, format="PNG")
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 720, text)
    c.save()
    return buf.getvalue()


def _post(client, headers, name, data, mime="application/octet-stream"):
    return client.post("/files/analyze", files={"file": (name, data, mime)}, headers=headers)


FAKE_VISION = {
    "summary": "An L-bracket.", "part_type_guess": "L-bracket",
    "dimensions": [{"label": "width", "value_mm": 50.0, "raw": "50 mm", "confidence": "high"}],
    "features": ["4 holes of 5 mm"], "missing": ["thickness"], "notes": [],
    "description": "L-bracket, 50 mm wide with 4 holes of 5 mm.",
}


@pytest.fixture
def vision_on(monkeypatch):
    import file_api
    monkeypatch.setattr(file_api, "_vision_ready", lambda: True)
    calls = []
    monkeypatch.setattr(file_api, "_vision", lambda images, context: calls.append((len(images), context)) or FAKE_VISION)
    return calls


class TestAccess:
    def test_signed_out_callers_are_refused(self, client):
        assert client.get("/files/capabilities").status_code == 401
        assert _post(client, {}, "a.png", _png()).status_code == 401

    def test_capabilities(self, client, wallet_session):
        r = client.get("/files/capabilities", headers=wallet_session["auth_headers"]).json()
        assert r["max_upload_mb"] == 15 and r["cad_files"] is True and "step" in r["accepted"]
        assert r["images"] is False            # no vision key in the test environment
        assert isinstance(r["image_tracing"], bool) and isinstance(r["pdf_tracing"], bool)


class TestRefusals:
    @pytest.mark.parametrize("name,data,word", [
        ("a.exe", b"MZ\x90", "not supported"),
        ("a.step", b"this is not a step file", "does not look like"),
        ("a.pdf", b"MZ\x90\x00", "does not look like"),
        ("a.stl", b"solid x", "does not look like"),
        ("a.pdf", b"", "empty"),
    ])
    def test_bad_files_get_a_plain_message(self, client, wallet_session, name, data, word):
        r = _post(client, wallet_session["auth_headers"], name, data)
        assert r.status_code == 422 and word in r.json()["detail"]

    def test_a_file_over_the_limit_is_413_before_anything_is_read(self, client, wallet_session):
        r = _post(client, wallet_session["auth_headers"], "big.step", b"0" * (16 * 1024 * 1024))
        assert r.status_code == 413 and "15 MB" in r.json()["detail"]

    def test_images_say_so_when_no_reader_is_available(self, client, wallet_session, monkeypatch):
        import raster_trace
        monkeypatch.setattr(raster_trace, "available", lambda: False)      # no tracing and no vision model
        r = _post(client, wallet_session["auth_headers"], "a.png", _png())
        assert r.status_code == 503 and "not switched on" in r.json()["detail"]

    def test_a_png_that_is_not_an_image_is_refused(self, client, wallet_session, vision_on):
        r = _post(client, wallet_session["auth_headers"], "a.png", b"\x89PNG\r\n\x1a\n" + b"junk" * 20)
        assert r.status_code == 422 and "could not be opened" in r.json()["detail"]
        assert vision_on == []                 # never reached the model

    def test_the_daily_cap_is_enforced_per_account(self, client, wallet_session, vision_on):
        import file_api
        file_api._daily[wallet_session["wallet_address"].lower()] = (time.strftime("%Y-%m-%d", time.gmtime()),
                                                                     file_api.settings.FILE_DAILY_LIMIT_PER_ACCOUNT)
        r = _post(client, wallet_session["auth_headers"], "a.png", _png())
        assert r.status_code == 429 and "today" in r.json()["detail"]


class TestImagesAndPdfs:
    def test_an_image_is_read_by_the_vision_model(self, client, wallet_session, vision_on):
        r = _post(client, wallet_session["auth_headers"], "sketch.png", _png(), "image/png")
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["kind"] == "png" and d["description"].startswith("L-bracket") and d["analysis"]["vision"]["dimensions"][0]["value_mm"] == 50.0
        assert vision_on == [(1, "")] and d["stl_base64"] is None and d["warnings"]

    def test_a_pdf_gives_its_text_and_numbers_without_the_model(self, client, wallet_session):
        pytest.importorskip("pypdf")
        r = _post(client, wallet_session["auth_headers"], "drawing.pdf", _pdf("Length 120 mm hole 8.5 mm"), "application/pdf")
        assert r.status_code == 200, r.text
        a = r.json()["analysis"]
        assert "120 mm" in a["numbers_found"] and "8.5 mm" in a["numbers_found"] and a["vision"] is None
        assert r.json()["description"] is None and any("not switched on" in w for w in r.json()["warnings"])

    def test_a_pdf_with_the_model_on_also_gets_a_description(self, client, wallet_session, vision_on):
        pytest.importorskip("pypdf")
        pytest.importorskip("pypdfium2")
        r = _post(client, wallet_session["auth_headers"], "drawing.pdf", _pdf("Length 120 mm"), "application/pdf")
        assert r.status_code == 200, r.text
        assert r.json()["description"].startswith("L-bracket") and vision_on[0][0] >= 1 and "Length 120 mm" in vision_on[0][1]


class TestCadFiles:
    @pytest.fixture
    def plate_step(self, tmp_path):
        import cadquery as cq
        plate = (cq.Workplane("XY").box(120, 60, 10)
                 .faces(">Z").workplane().rect(80, 30, forConstruction=True).vertices().hole(8))
        path = tmp_path / "plate.step"
        cq.exporters.export(plate, str(path))
        return path.read_bytes()

    def test_a_step_file_is_read(self, client, wallet_session, plate_step):
        r = _post(client, wallet_session["auth_headers"], "plate.step", plate_step)
        assert r.status_code == 200, r.text
        d = r.json()
        a = d["analysis"]
        assert a["bbox_mm"]["x"] == pytest.approx(120, abs=1e-3) and a["solids"] == 1
        assert a["round_holes"] == [{"diameter_mm": 8.0, "count": 4}]
        assert "4 x 8 mm diameter" in d["description"]
        stl = base64.b64decode(d["stl_base64"])
        assert len(stl) > 84 and "bounding box" in " ".join(d["warnings"])

    def test_a_damaged_step_file_is_a_422(self, client, wallet_session):
        data = b"ISO-10303-21;\nHEADER;\nthis is not valid\nENDSEC;\n"
        r = _post(client, wallet_session["auth_headers"], "bad.step", data)
        assert r.status_code == 422 and "could not be read" in r.json()["detail"]


def _plate_png() -> bytes:
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    from PIL import Image
    img = np.full((800, 1200), 255, np.uint8)
    cv2.rectangle(img, (100, 100), (1100, 700), 0, -1)
    cv2.circle(img, (600, 400), 50, 255, -1)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG")
    return buf.getvalue()


def _post_form(client, headers, name, data, form):
    return client.post("/files/analyze", files={"file": (name, data, "image/png")}, data=form, headers=headers)


class TestTracedPictures:
    def test_a_picture_is_traced_into_an_editable_part_without_a_vision_model(self, client, wallet_session):
        r = _post_form(client, wallet_session["auth_headers"], "plate.png", _plate_png(), {"overall_width_mm": "100"})
        assert r.status_code == 200
        d = r.json()
        assert d["import_id"] and d["card"]["editable"] and d["card"]["preview_svg"].startswith("<svg")
        assert d["analysis"]["width_mm"] == pytest.approx(100, abs=0.05) and d["analysis"]["round_holes"][0]["diameter_mm"] == pytest.approx(10, abs=0.1)
        assert dict(d["card"]["rows"])["Size from"] == "the width you gave"

    def test_without_a_scale_it_says_the_size_is_not_set(self, client, wallet_session):
        d = _post(client, wallet_session["auth_headers"], "plate.png", _plate_png(), "image/png").json()
        assert d["analysis"]["trace"]["needs_scale"] and any("No scale" in w for w in d["warnings"])

    @pytest.mark.parametrize("form", [{"scale_mm_per_px": "0"}, {"overall_width_mm": "-5"}, {"trace_mode": "wireframe"}])
    def test_bad_trace_options_are_refused(self, client, wallet_session, form):
        assert _post_form(client, wallet_session["auth_headers"], "plate.png", _plate_png(), form).status_code == 422

    def test_the_capabilities_list_pictures_as_editable_when_tracing_is_on(self, client, wallet_session):
        pytest.importorskip("cv2")
        r = client.get("/files/capabilities", headers=wallet_session["auth_headers"]).json()
        assert r["image_tracing"] is True and "png" in r["editable"]

    def test_a_blank_picture_is_refused_with_the_reason(self, client, wallet_session):
        r = _post(client, wallet_session["auth_headers"], "blank.png", _png(), "image/png")
        assert r.status_code == 422 and "contrast" in r.json()["detail"]
