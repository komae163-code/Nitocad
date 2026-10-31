"""
Re-exporting stored assemblies.

Before: GET /export/{fmt}/{job_id} refused any job whose part_type was "assembly",
because an assembly was rebuilt by parsing its description again and so was not
guaranteed to come back the same.

Now: the parts list used for the build is saved with the job (its `parameters`),
and a later export rebuilds from that list. These tests check the parts list is
checked and kept, that every format can be exported from a stored job, that the
export follows the stored list and not the text, and that older jobs with no
stored list are handled safely.

The list-checking tests need no CAD kernel. The rest need CadQuery and a temp DB.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_CQ = importlib.util.find_spec("cadquery") is not None
needs_cq = pytest.mark.skipif(not HAS_CQ, reason="requires CadQuery")


def _load_assembly():
    """The real assembly module, or (without CadQuery) the same file loaded with
    stand-ins for the CAD kernel so the list-checking code can still be tested."""
    if HAS_CQ:
        import assembly
        return assembly
    saved = {k: sys.modules.get(k) for k in ("cadquery", "cad_templates", "logging_config")}
    cq = types.ModuleType("cadquery"); cq.Assembly = object; cq.Workplane = object; cq.Shape = object; cq.Location = object
    tpl = types.ModuleType("cad_templates"); tpl.TEMPLATES = {}
    lg = types.ModuleType("logging_config")
    import logging
    lg.get_logger = lambda name: logging.getLogger(name)
    sys.modules.update({"cadquery": cq, "cad_templates": tpl, "logging_config": lg})
    try:
        spec = importlib.util.spec_from_file_location("assembly_under_test", ROOT / "assembly.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


asm = _load_assembly()

PLATE = {"name": "base_plate", "part_type": "flat_plate",
         "parameters": {"length_mm": 100.0, "width_mm": 80.0, "thickness_mm": 5.0},
         "position": (0, 0, 0), "rotation": (0, 0, 0)}
BRACKET = {"name": "bracket", "part_type": "l_bracket",
           "parameters": {"width_mm": 40.0, "height_mm": 50.0, "depth_mm": 30.0, "thickness_mm": 3.0},
           "position": (0, 0, 5.0), "rotation": (0, 0, 0)}


class TestNormalizeSpec:
    def test_good_spec_comes_back_with_values_unchanged(self):
        out = asm.normalize_assembly_spec([PLATE, BRACKET])
        assert [p["name"] for p in out] == ["base_plate", "bracket"]
        assert out[1]["position"] == (0.0, 0.0, 5.0) and out[0]["parameters"]["length_mm"] == 100.0

    def test_input_is_not_modified(self):
        before = json.dumps([PLATE, BRACKET])
        asm.normalize_assembly_spec([PLATE, BRACKET])
        assert json.dumps([PLATE, BRACKET]) == before

    def test_survives_a_json_round_trip(self):
        once = asm.normalize_assembly_spec([PLATE, BRACKET])
        twice = asm.normalize_assembly_spec(json.loads(json.dumps(once)))
        assert once == twice

    def test_missing_name_and_vectors_get_defaults(self):
        out = asm.normalize_assembly_spec([{"part_type": "shaft", "parameters": {}}])
        assert out[0]["name"] == "part_1" and out[0]["position"] == (0.0, 0.0, 0.0) and out[0]["rotation"] == (0.0, 0.0, 0.0)

    @pytest.mark.parametrize("bad", [
        None, [], "plate", {"part_type": "x"}, [1], [{"parameters": {}}], [{"part_type": 5}],
        [{"part_type": "x", "parameters": []}],
        [{"part_type": "x", "position": (1, 2)}],
        [{"part_type": "x", "position": ("a", 0, 0)}],
        [{"part_type": "x", "rotation": (0, 0, float("nan"))}],
        [{"part_type": "x", "position": (math.inf, 0, 0)}],
        [{"part_type": "x", "position": (5_000_000, 0, 0)}],
    ])
    def test_unusable_specs_raise_value_error(self, bad):
        with pytest.raises(ValueError):
            asm.normalize_assembly_spec(bad)

    def test_no_part_cap_by_default_but_a_runaway_guard(self):
        assert asm.MAX_ASSEMBLY_PARTS is None
        assert len(asm.normalize_assembly_spec([{"part_type": "shaft"} for _ in range(300)])) == 300
        with pytest.raises(ValueError):
            asm.normalize_assembly_spec([{"part_type": "shaft"} for _ in range(asm.RUNAWAY_PARTS + 1)])

    def test_part_cap_can_still_be_switched_on(self, monkeypatch):
        monkeypatch.setattr(asm, "MAX_ASSEMBLY_PARTS", 3)
        with pytest.raises(ValueError):
            asm.normalize_assembly_spec([{"part_type": "shaft"} for _ in range(4)])

    def test_long_names_are_trimmed(self):
        assert len(asm.normalize_assembly_spec([{"part_type": "x", "name": "n" * 500}])[0]["name"]) == 80


# ---------------------------------------------------------------- with CadQuery

@pytest.fixture
def generator(tmp_db, tmp_output_dir):
    from cad_generator import CADGenerator
    return CADGenerator(output_dir=str(tmp_output_dir))


def _make_assembly_job(generator, text="assemble a base plate and an l bracket together"):
    r = generator.generate_from_text(text, use_deepseek=False,
                                     formats=["stl"], user_id="tester")
    assert r["success"], r.get("error")
    import db
    return r["job_id"], db.get_job(r["job_id"])


@needs_cq
@pytest.mark.requires_cadquery
class TestStoredAssemblyExport:
    def test_the_parts_list_is_stored_with_the_job(self, generator):
        job_id, job = _make_assembly_job(generator)
        assert job["part_type"] == "assembly"
        parts = json.loads(job["parameters"])["parts"]
        assert [p["name"] for p in parts] == ["base_plate", "l_bracket"]

    @pytest.mark.parametrize("fmt", ["step", "iges", "stl", "dxf", "pdf"])
    def test_every_format_exports_from_the_stored_job(self, generator, fmt):
        job_id, _ = _make_assembly_job(generator)
        path, part_type = generator.export_format_for_job(job_id, fmt)
        assert part_type == "assembly" and path.exists() and path.stat().st_size > 0
        if fmt == "step":
            assert b"ISO-10303-21" in path.read_bytes()[:200]
        if fmt == "pdf":
            assert path.read_bytes().startswith(b"%PDF")

    def test_a_second_export_is_served_from_the_cache(self, generator):
        job_id, _ = _make_assembly_job(generator)
        a, _ = generator.export_format_for_job(job_id, "step")
        b, _ = generator.export_format_for_job(job_id, "step")
        assert a == b

    def test_step_keeps_the_named_parts(self, generator):
        job_id, _ = _make_assembly_job(generator)
        path, _ = generator.export_format_for_job(job_id, "step")
        text = path.read_text(errors="ignore")
        assert "base_plate" in text and "bracket" in text

    def test_the_export_follows_the_stored_list_not_the_text(self, generator):
        import cadquery as cq
        import db
        job_a, _ = _make_assembly_job(generator)
        job_b, job = _make_assembly_job(generator)
        parts = json.loads(job["parameters"])["parts"]
        parts[0]["parameters"]["length_mm"] = parts[0]["parameters"].get("length_mm", 100.0) + 60.0   # change only the stored list
        with db.get_conn() as conn:
            conn.execute("UPDATE jobs SET parameters = ? WHERE id = ?", (json.dumps({"parts": parts}), job_b))
        a, _ = generator.export_format_for_job(job_a, "step")
        b, _ = generator.export_format_for_job(job_b, "step")
        la = cq.importers.importStep(str(a)).val().BoundingBox().xlen
        lb = cq.importers.importStep(str(b)).val().BoundingBox().xlen
        assert lb - la == pytest.approx(60.0, abs=0.5)

    def test_an_old_job_with_no_stored_list_is_still_rebuilt(self, generator):
        import db
        job_id, _ = _make_assembly_job(generator)
        with db.get_conn() as conn:
            conn.execute("UPDATE jobs SET parameters = ? WHERE id = ?", (json.dumps({}), job_id))
        path, _ = generator.export_format_for_job(job_id, "step")
        assert path.exists()

    def test_an_old_job_is_rebuilt_from_the_example_it_was_built_from(self, generator):
        import cadquery as cq
        import db
        job_id, _ = _make_assembly_job(generator, "assemble a motor mount and a gear together")
        with db.get_conn() as conn:
            conn.execute("UPDATE jobs SET parameters = ? WHERE id = ?", (json.dumps({}), job_id))
        path, _ = generator.export_format_for_job(job_id, "step")
        text = path.read_text(errors="ignore")
        assert "base_plate" in text and "bracket" in text and "motor_mount" not in text   # the old example, not the text

    def test_a_damaged_stored_list_gives_a_clear_error(self, generator):
        import db
        from exceptions import AssemblyError
        job_id, _ = _make_assembly_job(generator)
        with db.get_conn() as conn:
            conn.execute("UPDATE jobs SET parameters = ? WHERE id = ?",
                         (json.dumps({"parts": [{"part_type": "flat_plate", "position": [1, 2]}]}), job_id))
        with pytest.raises(AssemblyError) as e:
            generator.export_format_for_job(job_id, "step")
        assert "cannot be rebuilt" in e.value.message

    def test_single_parts_still_export_as_before(self, generator):
        r = generator.generate_from_text("shaft 10mm diameter, 50mm long", use_deepseek=False,
                                         formats=["stl"], user_id="tester")
        path, part_type = generator.export_format_for_job(r["job_id"], "step")
        assert part_type == "shaft" and path.exists()

    def test_a_failed_job_still_cannot_be_exported(self, generator):
        import db
        from exceptions import GenerationError
        job_id = db.record_job(description="x", user_id="tester", success=False, error="boom", state="failed")
        with pytest.raises(GenerationError):
            generator.export_format_for_job(job_id, "step")


class TestSplitDescription:
    def test_two_parts_joined_by_with(self):
        assert asm.split_assembly_description("base plate with l bracket") == [("base plate", 1), ("l bracket", 1)]

    def test_details_stay_with_the_part_they_describe(self):
        out = asm.split_assembly_description("an assembly of a base plate 100mm x 80mm with 4 holes and an l bracket 40mm wide")
        assert out == [("base plate 100mm x 80mm 4 holes", 1), ("l bracket 40mm wide", 1)]

    def test_counts_in_digits_and_words(self):
        assert asm.split_assembly_description("assemble two l brackets and a shaft 10mm diameter together") == \
            [("l brackets", 2), ("shaft 10mm diameter", 1)]
        assert asm.split_assembly_description("3 flat plates, hex standoff") == [("flat plates", 3), ("hex standoff", 1)]

    def test_a_size_is_not_mistaken_for_a_count(self):
        assert asm.split_assembly_description("2 x 4 plate and gear") == [("2 x 4 plate", 1), ("gear", 1)]

    def test_a_plain_bracket_means_the_l_bracket(self):
        assert asm.split_assembly_description("plate and bracket") == [("plate", 1), ("l bracket", 1)]
        assert asm.split_assembly_description("plate and t bracket") == [("plate", 1), ("t bracket", 1)]

    def test_civil_parts_are_recognised(self):
        assert [t for t, _ in asm.split_assembly_description("road segment and box culvert together")] == ["road segment", "box culvert"]

    @pytest.mark.parametrize("text", ["", "assemble multiple parts into one unit", "four holes and a fillet", "together"])
    def test_a_description_with_no_parts_gives_nothing(self, text):
        assert asm.split_assembly_description(text) == []

    def test_part_names(self):
        assert asm._part_name("base plate 100mm x 80mm", "flat_plate") == "base_plate"
        assert asm._part_name("l bracket 40mm wide", "l_bracket") == "l_bracket"
        assert asm._part_name("100 x 80", "flat_plate") == "flat_plate"

    def test_the_legacy_example_is_the_old_fixed_assembly(self):
        ex = asm.legacy_assembly_example()
        assert [p["name"] for p in ex] == ["base_plate", "bracket"]
        assert ex[1]["position"] == (0, 0, 5.0) and ex[0]["parameters"]["length_mm"] == 100.0
        assert len(asm.normalize_assembly_spec(ex)) == 2


@needs_cq
@pytest.mark.requires_cadquery
class TestParseAssemblyDescription:
    def test_the_description_is_read(self):
        spec = asm.parse_assembly_description("a base plate 120mm long 60mm wide with an l bracket")
        assert [p["part_type"] for p in spec] == ["flat_plate", "l_bracket"]
        assert spec[0]["parameters"]["length_mm"] == 120.0 and spec[0]["parameters"]["width_mm"] == 60.0

    def test_different_descriptions_give_different_assemblies(self):
        a = asm.parse_assembly_description("base plate with l bracket")
        b = asm.parse_assembly_description("two l brackets and a shaft 10mm diameter 50mm long")
        assert [p["part_type"] for p in a] != [p["part_type"] for p in b] and len(b) == 3

    def test_parts_sit_in_a_row_without_overlapping(self):
        import cadquery as cq
        spec = asm.parse_assembly_description("two flat plates and a gear")
        boxes = []
        for p in spec:
            from cad_templates import TEMPLATES
            bb = TEMPLATES[p["part_type"]](p["parameters"]).val().moved(cq.Location(cq.Vector(*p["position"]))).BoundingBox()
            boxes.append((bb.xmin, bb.xmax, bb.zmin))
        boxes.sort()
        for (a0, a1, _), (b0, b1, _) in zip(boxes, boxes[1:]):
            assert b0 >= a1 + asm.ASSEMBLY_PART_GAP_MM - 0.01
        assert all(abs(z) < 0.01 for _, _, z in boxes)

    def test_repeated_parts_get_distinct_names(self):
        names = [p["name"] for p in asm.parse_assembly_description("three l brackets and a flat plate")]
        assert len(names) == len(set(names)) == 4

    def test_the_result_is_stable_and_survives_storage(self):
        a = asm.parse_assembly_description("plate and bracket")
        b = asm.parse_assembly_description("plate and bracket")
        assert a == b
        assert asm.normalize_assembly_spec(json.loads(json.dumps(a))) == asm.normalize_assembly_spec(a)

    @pytest.mark.parametrize("text", ["assemble multiple parts into one unit", "just a plate", "holes and fillets"])
    def test_fewer_than_two_parts_is_an_error_with_a_hint(self, text):
        with pytest.raises(ValueError) as e:
            asm.parse_assembly_description(text)
        assert "at least two parts" in str(e.value)

    def test_the_generated_values_pass_through_the_validator(self):
        spec = asm.parse_assembly_description("flat plate 1mm thick and l bracket")
        assert spec[0]["parameters"]["thickness_mm"] >= 1.0     # corrected by validate_parameters if it was too thin
