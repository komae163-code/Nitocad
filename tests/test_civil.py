"""
Tests for the civil elements: road_segment, box_culvert, retaining_wall.

Three layers, so most of this runs without the CAD kernel:

  1. Profile maths (cad_templates/_civil_profiles.py) - plain numbers.
  2. Validator rules and the regex parser - no CadQuery needed (the parser tests
     need pydantic, which requirements.txt already pins).
  3. Real geometry, exports and the PDF drawing scale - marked
     `requires_cadquery`, skipped when CadQuery/OCP is not installed.

The geometry tests check volumes against the profile areas, so they catch a
wrongly built or wrongly placed solid, not just "something came out".
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str, relpath: str):
    """Load a module by file path so the profile maths can be tested without
    importing the cad_templates package (which imports CadQuery)."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


prof = _load("civil_profiles_under_test", "cad_templates/_civil_profiles.py")


# --------------------------------------------------------------- profile maths

class TestRoadProfile:
    def test_default_levels(self):
        h = prof.road_heights(3500, 2, 1500, 300, 2.5, 4.0)
        assert h["half_carriageway"] == 3500
        assert h["half_width"] == 5000
        assert h["z_crown"] == 300
        assert h["z_carriageway_edge"] == pytest.approx(212.5)
        assert h["z_shoulder_edge"] == pytest.approx(152.5)

    def test_outline_is_closed_counter_clockwise_and_symmetric(self):
        pts = prof.road_profile(3500, 2, 1500, 300, 2.5, 4.0)
        assert len(pts) == 7
        assert prof.polygon_area(pts) > 0
        assert sorted(p[0] for p in pts) == sorted(-p[0] for p in pts)

    def test_crown_is_the_highest_point(self):
        pts = prof.road_profile(3500, 2, 1500, 300, 2.5, 4.0)
        assert max(p[1] for p in pts) == 300
        assert (0.0, 300) in pts

    def test_zero_shoulder_drops_duplicate_points(self):
        pts = prof.road_profile(3500, 2, 0, 300, 2.5, 4.0)
        assert len(pts) == 5
        assert len(set(pts)) == len(pts)

    def test_area_matches_trapezoid_sum(self):
        # Hand calculation for the default road: bottom 10000 wide.
        pts = prof.road_profile(3500, 2, 1500, 300, 2.5, 4.0)
        per_side = (152.5 + 212.5) / 2 * 1500 + (212.5 + 300) / 2 * 3500
        assert prof.polygon_area(pts) == pytest.approx(2 * per_side)


class TestKerbRects:
    def test_overlap_goes_below_the_surface_edge(self):
        left, right = prof.kerb_rects(5000, 152.5, 150, 150, 20)
        assert min(p[1] for p in right) == pytest.approx(132.5)
        assert max(p[1] for p in right) == pytest.approx(302.5)
        assert max(p[0] for p in right) == 5000 and min(p[0] for p in right) == 4850
        assert min(p[0] for p in left) == -5000


class TestCulvert:
    def test_outer_size_single_and_twin(self):
        assert prof.culvert_dims(1500, 1200, 200, 250, 1) == {"outer_width": 1900, "outer_height": 1700}
        assert prof.culvert_dims(1500, 1200, 200, 250, 2)["outer_width"] == 3600

    def test_cell_centres_are_symmetric(self):
        assert prof.culvert_cell_centres(1500, 200, 1) == [0.0]
        assert prof.culvert_cell_centres(1500, 200, 2) == [-850.0, 850.0]
        three = prof.culvert_cell_centres(1500, 200, 3)
        assert three[1] == 0.0 and three[0] == -three[2]


class TestRetainingWallProfile:
    def test_outline_and_height(self):
        pts = prof.retaining_wall_profile(3000, 400, 250, 2200, 400, 600)
        assert prof.polygon_area(pts) > 0
        assert max(p[1] for p in pts) == 3400
        assert max(p[0] for p in pts) == 2200

    def test_stem_is_thicker_at_the_base(self):
        pts = prof.retaining_wall_profile(3000, 400, 250, 2200, 400, 600)
        assert (1000, 400) in pts and (850, 3400) in pts

    def test_area_matches_slab_plus_stem(self):
        pts = prof.retaining_wall_profile(3000, 400, 250, 2200, 400, 600)
        slab = 2200 * 400
        stem = (400 + 250) / 2 * 3000
        assert prof.polygon_area(pts) == pytest.approx(slab + stem)


# ------------------------------------------------------------------ validator

validator = _load("validator_under_test", "validator.py")


class TestCivilValidator:
    @pytest.mark.parametrize("ptype", ["road_segment", "box_culvert", "retaining_wall"])
    def test_defaults_are_valid_and_carry_the_notice(self, ptype):
        params, result = validator.validate_parameters(ptype, {})
        assert result.is_valid
        assert result.corrections == {}
        assert any("Not checked against any design code" in w for w in result.warnings)

    def test_road_thin_pavement_is_thickened_not_the_falls_changed(self):
        params, result = validator.validate_parameters(
            "road_segment", {"pavement_thickness_mm": 120})
        assert params["pavement_thickness_mm"] == pytest.approx(
            (2 * 3500 / 2) * 2.5 / 100 + 1500 * 4.0 / 100 + 50)

    def test_road_edge_height_never_below_50mm_after_corrections(self):
        for params_in in ({"pavement_thickness_mm": 100, "lane_count": 6, "lane_width_mm": 4000},
                          {"pavement_thickness_mm": 150, "crossfall_percent": 8,
                           "shoulder_width_mm": 5000, "shoulder_crossfall_percent": 10}):
            p, _ = validator.validate_parameters("road_segment", params_in)
            lanes = p.get("lane_count", 2)
            lane_w = p.get("lane_width_mm", 3500)
            drop = (lanes * lane_w / 2) * p.get("crossfall_percent", 2.5) / 100 \
                + p.get("shoulder_width_mm", 1500) * p.get("shoulder_crossfall_percent", 4.0) / 100
            assert p.get("pavement_thickness_mm", 300) - drop >= 50 - 1e-6

    def test_road_lane_count_is_made_a_whole_number(self):
        p, r = validator.validate_parameters("road_segment", {"lane_count": 3.5})
        assert p["lane_count"] == 4 and "lane_count" in r.corrections

    def test_road_length_is_capped(self):
        p, _ = validator.validate_parameters("road_segment", {"length_mm": 5_000_000})
        assert p["length_mm"] == validator.CIVIL_MAX_LENGTH_MM

    def test_culvert_barrel_count_and_wall_limits(self):
        p, r = validator.validate_parameters("box_culvert", {"barrel_count": 9, "wall_thickness_mm": 20})
        assert p["barrel_count"] == 4 and p["wall_thickness_mm"] == 100

    def test_retaining_wall_top_not_thicker_than_base(self):
        p, _ = validator.validate_parameters("retaining_wall", {"stem_thickness_top_mm": 600})
        assert p["stem_thickness_top_mm"] == 400

    def test_retaining_wall_base_gets_a_heel(self):
        p, _ = validator.validate_parameters("retaining_wall", {"base_width_mm": 800})
        assert p["base_width_mm"] == 600 + 400 + 300

    def test_garbage_numbers_fall_back_to_defaults_without_raising(self):
        _, r = validator.validate_parameters("road_segment", {"length_mm": "long"})
        assert r.is_valid


# --------------------------------------------------------------------- parser

class TestCivilParser:
    @pytest.fixture(scope="class")
    def sp(self):
        pytest.importorskip("pydantic")
        import smart_parser
        return smart_parser

    def _parse(self, sp, text):
        ptype = sp.infer_part_type(text, [], [])
        params = sp.infer_missing_dimensions(ptype, [], [], text, [])
        return ptype, params

    def test_road_in_metres(self, sp):
        t, p = self._parse(sp, "Road segment 50 m long with two 3.5 m lanes, 1.5 m shoulders and kerbs")
        assert t == "road_segment"
        assert p["length_mm"] == 50000 and p["lane_width_mm"] == 3500
        assert p["shoulder_width_mm"] == 1500 and p["lane_count"] == 2 and p["has_kerbs"] is True

    def test_british_spelling_metres(self, sp):
        t, p = self._parse(sp, "a 2 lane highway 100 metres long, 3.65m lane width, 2% crossfall")
        assert t == "road_segment"
        assert p["length_mm"] == 100000 and p["lane_width_mm"] == 3650 and p["crossfall_percent"] == 2.0

    def test_box_culvert_beats_the_generic_box_keyword(self, sp):
        t, p = self._parse(sp, "box culvert 1.5m x 1.2m, 8 m long, twin cell, wall thickness 250mm")
        assert t == "box_culvert"
        assert p["clear_width_mm"] == 1500 and p["clear_height_mm"] == 1200
        assert p["length_mm"] == 8000 and p["barrel_count"] == 2 and p["wall_thickness_mm"] == 250

    def test_culvert_size_in_mm(self, sp):
        _, p = self._parse(sp, "box culvert 1500 x 1200 mm")
        assert p["clear_width_mm"] == 1500 and p["clear_height_mm"] == 1200

    def test_retaining_wall(self, sp):
        t, p = self._parse(sp, "retaining wall 3.5 m high 12 m long base 2.4 m")
        assert t == "retaining_wall"
        assert p["stem_height_mm"] == 3500 and p["length_mm"] == 12000 and p["base_width_mm"] == 2400

    @pytest.mark.parametrize("text,expected", [
        ("broad plate 100mm x 50mm", "flat_plate"),          # 'broad' must not match 'road'
        ("L-bracket 50mm wide with a channel", "l_bracket"),
        ("box 100mm enclosure", "simple_box"),
        ("steel I-beam 200mm long", "structural_beam"),
    ])
    def test_mechanical_parts_are_not_hijacked(self, sp, text, expected):
        assert sp.infer_part_type(text, [], []) == expected


# ------------------------------------------------------------- real geometry

cadquery = None
try:
    import cadquery  # noqa: F401
except Exception:  # noqa: BLE001
    pass

needs_cq = pytest.mark.skipif(cadquery is None, reason="requires a real CadQuery/OCCT install")


def _solid_ok(wp) -> bool:
    solids = wp.solids().vals()
    return len(solids) == 1 and solids[0].isValid()


@pytest.mark.requires_cadquery
@needs_cq
class TestCivilGeometry:
    @pytest.fixture(scope="class")
    def templates(self):
        from cad_templates import TEMPLATES
        return TEMPLATES

    def test_all_three_are_registered(self, templates):
        for name in ("road_segment", "box_culvert", "retaining_wall"):
            assert name in templates

    def test_road_defaults_volume_and_bbox(self, templates):
        wp = templates["road_segment"]({})
        assert _solid_ok(wp)
        expected = prof.polygon_area(prof.road_profile(3500, 2, 1500, 300, 2.5, 4.0)) * 20000
        assert wp.val().Volume() == pytest.approx(expected, rel=1e-4)
        bb = wp.val().BoundingBox()
        assert (bb.xmin, bb.xmax) == (pytest.approx(0, abs=1e-6), pytest.approx(20000, abs=1e-6))
        assert bb.ymax - bb.ymin == pytest.approx(10000, abs=1e-6)
        assert bb.zmin == pytest.approx(0, abs=1e-6) and bb.zmax == pytest.approx(300, abs=1e-6)

    def test_road_with_kerbs_is_one_valid_solid_and_adds_volume(self, templates):
        plain = templates["road_segment"]({})
        kerbed = templates["road_segment"]({"has_kerbs": True})
        assert _solid_ok(kerbed)
        # Each kerb adds at least width x height x length above the surface.
        assert kerbed.val().Volume() > plain.val().Volume() + 2 * 150 * 150 * 20000 * 0.99
        assert kerbed.val().BoundingBox().zmax > plain.val().BoundingBox().zmax - 1e-6

    def test_road_without_shoulders(self, templates):
        wp = templates["road_segment"]({"shoulder_width_mm": 0, "lane_count": 1})
        assert _solid_ok(wp)

    @pytest.mark.parametrize("barrels", [1, 2, 3, 4])
    def test_culvert_volume_and_openings(self, templates, barrels):
        wp = templates["box_culvert"]({"barrel_count": barrels})
        assert _solid_ok(wp)
        d = prof.culvert_dims(1500, 1200, 200, 250, barrels)
        expected = (d["outer_width"] * d["outer_height"] - barrels * 1500 * 1200) * 6000
        assert wp.val().Volume() == pytest.approx(expected, rel=1e-4)
        bb = wp.val().BoundingBox()
        assert bb.ymax - bb.ymin == pytest.approx(d["outer_width"], abs=1e-6)
        assert bb.zmax - bb.zmin == pytest.approx(d["outer_height"], abs=1e-6)

    def test_retaining_wall_volume_and_bbox(self, templates):
        wp = templates["retaining_wall"]({})
        assert _solid_ok(wp)
        area = prof.polygon_area(prof.retaining_wall_profile(3000, 400, 250, 2200, 400, 600))
        assert wp.val().Volume() == pytest.approx(area * 10000, rel=1e-4)
        bb = wp.val().BoundingBox()
        assert bb.ymax - bb.ymin == pytest.approx(2200, abs=1e-6)
        assert bb.zmax == pytest.approx(3400, abs=1e-6)

    @pytest.mark.parametrize("ptype", ["road_segment", "box_culvert", "retaining_wall"])
    def test_validated_defaults_build(self, templates, ptype):
        params, _ = validator.validate_parameters(ptype, {})
        assert _solid_ok(templates[ptype](params))


@pytest.mark.requires_cadquery
@needs_cq
class TestCivilExports:
    @pytest.fixture(scope="class")
    def exporters_mod(self):
        pytest.importorskip("ezdxf")
        pytest.importorskip("reportlab")
        import exporters
        return exporters

    def test_scale_is_1_for_ordinary_parts_and_grows_for_big_ones(self, exporters_mod):
        import cadquery as cq
        small = cq.Workplane("XY").box(100, 80, 10)
        big = cq.Workplane("XY").box(20000, 10000, 300)
        assert exporters_mod.pdf_scale_denominator(small) == 1
        n = exporters_mod.pdf_scale_denominator(big)
        assert n in exporters_mod._PDF_STANDARD_SCALES
        assert (20000 + 10000 + 300) / n <= exporters_mod._PDF_TARGET_EXTENT_MM

    def test_road_exports_in_all_five_formats_with_a_usable_pdf_page(self, exporters_mod, tmp_path):
        from cad_templates import TEMPLATES
        road = TEMPLATES["road_segment"]({})
        out = exporters_mod.export_all(road, tmp_path, "road", part_type="road_segment")
        assert set(out) == {"step", "stl", "iges", "dxf", "pdf"}
        for p in out.values():
            assert p.exists() and p.stat().st_size > 0
        head = out["pdf"].read_bytes()
        assert head.startswith(b"%PDF")
        m = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)", head)
        assert m, "could not read the page size"
        assert float(m.group(1)) < 14400 and float(m.group(2)) < 14400   # PDF viewers' limit

    @pytest.mark.parametrize("factor", [0.5, 0.01])
    def test_scaling_a_shape_scales_its_volume_by_the_cube(self, exporters_mod, factor):
        import cadquery as cq
        box = cq.Workplane("XY").box(100, 80, 10).val()
        scaled = exporters_mod._scale_shape(box, factor)
        assert scaled.Volume() == pytest.approx(box.Volume() * factor ** 3, rel=1e-6)
        assert scaled.BoundingBox().xlen == pytest.approx(100 * factor, rel=1e-6)

    def test_pdf_of_a_normal_part_is_unchanged_at_1_to_1(self, exporters_mod, tmp_path):
        import cadquery as cq
        plate = cq.Workplane("XY").box(100, 80, 10)
        out = exporters_mod.export_all(plate, tmp_path, "plate", formats=["pdf"], part_type="flat_plate")
        assert out["pdf"].read_bytes().startswith(b"%PDF")
