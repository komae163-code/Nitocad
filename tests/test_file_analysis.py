"""
Tests for file_analysis.py (uploads to CAD Studio).

Most of these need no libraries. The CAD-kernel tests build a real part, write it
as STEP, and read it back, so they run where CadQuery is installed and are skipped
elsewhere. The process-isolation test uses time.sleep as the "hostile reader".
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import file_analysis as fa  # noqa: E402

HAS_CQ = importlib.util.find_spec("cadquery") is not None
needs_cq = pytest.mark.skipif(not HAS_CQ, reason="requires CadQuery")

IGES_HEAD = b" " * 72 + b"S      1\n" + b"x" * 40


class TestSniffing:
    @pytest.mark.parametrize("name,data,kind", [
        ("a.STEP", b"ISO-10303-21;\nHEADER;" + b"x" * 80, "step"),
        ("a.stp", b"junk ISO-10303-21; more", "step"),
        ("a.igs", IGES_HEAD, "iges"),
        ("a.brep", b"DBRep_DrawableShape\n" + b"0" * 20, "brep"),
        ("a.pdf", b"%PDF-1.7\n", "pdf"),
        ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 10, "png"),
        ("a.jpeg", b"\xff\xd8\xff\xe0" + b"0" * 10, "jpg"),
        ("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
    ])
    def test_accepts_real_files(self, name, data, kind):
        assert fa.sniff_kind(name, data) == kind

    @pytest.mark.parametrize("name,data", [
        ("a.step", b"this is not a step file"),
        ("a.pdf", b"MZ\x90\x00"),                         # an exe renamed to .pdf
        ("a.png", b"\xff\xd8\xff\xe0jpegdata"),            # right family, wrong type
        ("a.exe", b"MZ"),
        ("noextension", b"%PDF-1.4"),
        ("a.pdf", b""),
    ])
    def test_rejects_wrong_or_fake_files(self, name, data):
        with pytest.raises(fa.FileRejected):
            fa.sniff_kind(name, data)

    @pytest.mark.parametrize("name,data,kind", [
        ("a.dxf", b"0\nSECTION\n2\nHEADER\n", "dxf"),
        ("a.svg", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', "svg"),
        ("b.kicad_pcb", b"(kicad_pcb (version 20221018)", "kicad"),
        ("a.gko", b"%FSLAX24Y24*%\nG01*", "gerber"), ("a.GTL", b"G04 test*\n%MOMM*%", "gerber"),
        ("a.drl", b"M48\nMETRIC\n", "drill"), ("fab.zip", b"PK\x03\x04rest", "gerber_zip"),
        ("a.stl", b"solid x\n facet normal 0 0 0\n", "stl"), ("a.obj", b"# o\nv 0 0 0\nv 1 0 0\nf 1 2 2\n", "obj"),
    ])
    def test_drawing_and_mesh_files_are_accepted(self, name, data, kind):
        assert fa.sniff_kind(name, data) == kind

    def test_a_binary_stl_is_recognised_by_its_size(self):
        import struct
        assert fa.sniff_kind("m.stl", b"x" * 80 + struct.pack("<I", 1) + b"0" * 50) == "stl"

    @pytest.mark.parametrize("name,data", [
        ("a.dxf", b"hello world"), ("a.svg", b"<html>"), ("a.stl", b"junk" * 30), ("a.obj", b"\x00\x01binary"),
        ("a.zip", b"notzip"), ("a.kicad_pcb", b"(something_else"), ("a.drl", b"no header here"),
    ])
    def test_fake_drawing_and_mesh_files_are_refused(self, name, data):
        with pytest.raises(fa.FileRejected):
            fa.sniff_kind(name, data)

    @pytest.mark.parametrize("name,word", [("a.kicad_sch", "schematic"), ("a.kicad_pro", "project"), ("m.glb", "GLB"), ("m.gltf", "GLTF"), ("m.3mf", "3MF")])
    def test_formats_that_cannot_be_used_say_what_to_do_instead(self, name, word):
        with pytest.raises(fa.FileRejected) as e:
            fa.sniff_kind(name, b"data")
        assert word in str(e.value)


class TestVisionAnswerCleaning:
    def test_numbers_are_validated_and_text_is_bounded(self):
        c = fa.clean_vision({
            "summary": "s" * 900, "part_type_guess": "L-bracket",
            "dimensions": [
                {"label": "width", "value_mm": "50", "raw": "50 mm", "confidence": "high"},
                {"label": "negative", "value_mm": -3},
                {"label": "infinite", "value_mm": float("inf")},
                {"label": "text", "value_mm": "wide"},
                {"label": "", "value_mm": 4},
                "not a dict",
            ],
            "features": ["4 holes", {"nested": 1}, "  spaced   out  "],
        })
        assert len(c["summary"]) == 400
        assert [d["label"] for d in c["dimensions"]] == ["width", "negative", "infinite", "text"]
        assert c["dimensions"][0]["value_mm"] == 50.0 and c["dimensions"][0]["confidence"] == "high"
        assert all(d["value_mm"] is None for d in c["dimensions"][1:])
        assert all(d["confidence"] == "low" for d in c["dimensions"][1:])
        assert c["features"] == ["4 holes", "spaced out"]

    def test_description_is_built_from_dimensions_when_missing(self):
        c = fa.clean_vision({"part_type_guess": "flange",
                             "dimensions": [{"label": "outer diameter", "value_mm": 120}, {"label": "bore", "value_mm": 40}]})
        assert "outer diameter 120 mm" in c["description"] and c["description"].startswith("Flange")

    def test_dimension_count_is_capped(self):
        many = [{"label": f"d{i}", "value_mm": i + 1} for i in range(100)]
        assert len(fa.clean_vision({"dimensions": many})["dimensions"]) == 40

    def test_garbage_input_does_not_raise(self):
        c = fa.clean_vision({"dimensions": "nope", "features": 5, "summary": None})
        assert c["dimensions"] == [] and c["features"] == [] and c["part_type_guess"] == "unknown"

    @pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! {"a": 1} hope that helps'])
    def test_json_is_found_in_messy_replies(self, text):
        assert fa._extract_json(text) == {"a": 1}

    @pytest.mark.parametrize("text", ["no json here", "[1, 2]", "{broken"])
    def test_unusable_replies_raise(self, text):
        with pytest.raises(fa.FileRejected):
            fa._extract_json(text)

    def test_the_prompt_tells_the_model_to_ignore_instructions_in_files(self):
        assert "ignore them" in fa.VISION_SYSTEM and "Never invent a dimension" in fa.VISION_SYSTEM


class TestTextHelpers:
    def test_numbers_with_units(self):
        found = fa.numbers_with_units('Length 120 mm, hole 8.5mm, gap 2", 3,5 cm and 120 mm again, ref 120mmx')
        assert found == ["120 mm", "8.5 mm", '2 "', "3,5 cm"]

    def test_cad_description(self):
        d = fa.cad_description({"bbox_mm": {"x": 120, "y": 60, "z": 10}, "round_holes": [{"diameter_mm": 8, "count": 4}]})
        assert d == "A part about 120.0 x 60.0 x 10.0 mm with round holes: 4 x 8 mm diameter."
        assert fa.cad_description({"bbox_mm": {"x": 1, "y": 2, "z": 3}}).endswith("mm.")


class TestIsolation:
    def test_a_runaway_reader_is_stopped(self):
        t0 = time.time()
        with pytest.raises(fa.FileRejected) as e:
            fa.run_isolated(time.sleep, (30,), 1.0)
        assert "too long" in str(e.value) and time.time() - t0 < 15

    def test_a_reader_that_raises_is_reported_not_propagated(self):
        with pytest.raises(fa.FileRejected) as e:
            fa.run_isolated(int, ("not a number",), 20.0)
        assert "could not be read" in str(e.value)

    def test_a_normal_result_comes_back(self):
        assert fa.run_isolated(max, ([3, 9, 4],), 20.0) == 9


@needs_cq
class TestCadFiles:
    @pytest.fixture(scope="class")
    def plate_step(self, tmp_path_factory):
        import cadquery as cq
        plate = (cq.Workplane("XY").box(120, 60, 10)
                 .faces(">Z").workplane().rect(80, 30, forConstruction=True).vertices().hole(8))
        path = tmp_path_factory.mktemp("cad") / "plate.step"
        cq.exporters.export(plate, str(path))
        return path

    def test_step_is_read_back_with_size_volume_and_holes(self, plate_step):
        a = fa.analyze_cad_file(str(plate_step), "step")
        b = a["bbox_mm"]
        assert (b["x"], b["y"], b["z"]) == (pytest.approx(120, abs=1e-3), pytest.approx(60, abs=1e-3), pytest.approx(10, abs=1e-3))
        expected = 120 * 60 * 10 - 4 * 3.141592653589793 * 4 ** 2 * 10
        assert a["volume_mm3"] == pytest.approx(expected, rel=1e-3)
        assert a["solids"] == 1
        assert a["round_holes"] == [{"diameter_mm": 8.0, "count": 4}]
        assert a["stl"] and a["stl"][:5] in (b"solid", ) or len(a["stl"]) > 84

    def test_isolated_read_gives_the_same_answer(self, plate_step):
        a = fa.run_isolated(fa.analyze_cad_file, (str(plate_step), "step"), 60.0)
        assert a["round_holes"] == [{"diameter_mm": 8.0, "count": 4}]

    def test_a_corrupt_step_is_rejected(self, tmp_path):
        bad = tmp_path / "bad.step"
        bad.write_bytes(b"ISO-10303-21;\nHEADER;\nthis is not valid\nENDSEC;\n")
        with pytest.raises(fa.FileRejected):
            fa.run_isolated(fa.analyze_cad_file, (str(bad), "step"), 60.0)

    def test_description_is_buildable_from_a_real_read(self, plate_step):
        d = fa.cad_description(fa.analyze_cad_file(str(plate_step), "step"))
        assert "120.0 x 60.0 x 10.0 mm" in d and "4 x 8 mm diameter" in d


class TestCards:
    def test_a_cad_card(self):
        card = fa.card_from_result("plate.step", "step", 2048, {
            "analysis": {"bbox_mm": {"x": 120, "y": 60, "z": 10}, "volume_mm3": 64000.4, "solids": 1, "faces": 14,
                         "round_holes": [{"diameter_mm": 8.0, "count": 4}], "round_bosses": []},
            "description": "A part.", "stl_base64": "AAAA", "import_id": "a" * 32, "warnings": ["w"]})
        assert ["Overall size", "120 x 60 x 10 mm"] in card["rows"] and ["Round hole", "4 x 8 mm diameter"] in card["rows"]
        assert card["editable"] and card["has_preview"] and card["import_id"] == "a" * 32

    def test_a_drawing_card_lists_each_hole_once(self):
        card = fa.card_from_result("p.dxf", "dxf", 100, {"analysis": {"width_mm": 100, "height_mm": 60, "islands": 1, "thickness_mm": 3,
                                   "round_holes": [{"diameter_mm": 8.0, "count": 1}], "other_cutouts": 0}, "description": "d", "import_id": "b" * 32})
        assert sum(1 for r in card["rows"] if r[0] == "Round hole") == 1 and ["Thickness used", "3 mm (you can change it)"] in card["rows"]

    def test_a_mesh_card(self):
        card = fa.card_from_result("m.stl", "stl", 684, {"analysis": {"triangles": 12, "size_mm": [10, 10, 10], "watertight": True, "volume_mm3": 1000.0},
                                   "description": "d", "import_id": "c" * 32})
        assert ["Closed (watertight)", "yes"] in card["rows"] and ["Triangles", "12"] in card["rows"]

    def test_a_file_that_cannot_be_edited_says_so_by_having_no_import_id(self):
        card = fa.card_from_result("x.png", "png", 10, {"analysis": {}, "description": None, "import_id": None})
        assert card["editable"] is False and card["import_id"] is None

    def test_descriptions(self):
        assert fa.drawing_description({"width_mm": 100, "height_mm": 60, "round_holes": [{"diameter_mm": 8, "count": 4}]}, 3) == \
            "A flat part about 100.0 x 60.0 mm, extruded 3 mm thick, with round holes: 4 x 8 mm diameter."
        assert fa.mesh_description({"size_mm": [10, 20, 30], "triangles": 1200}) == "A mesh about 10.0 x 20.0 x 30.0 mm with 1,200 triangles."
