"""Aerospace pack tests. Parser/validator/airfoil-math tests need no CAD kernel;
the TestTemplates class needs cadquery (same as tests/test_cad_templates.py)."""
import pytest
from smart_parser import infer_part_type
from validator import validate_parameters
from cad_templates.aerospace import naca4_points, parse_naca4
from aerospace_pack import GEOMETRY_ONLY_NOTICE, wing_metrics


class TestAirfoilMath:
    def test_parse(self):
        assert parse_naca4("2412") == (0.02, 0.4, 0.12)

    def test_bad_code(self):
        with pytest.raises(ValueError):
            parse_naca4("24")

    def test_outline_is_closed_polygon_without_duplicates(self):
        pts = naca4_points("2412", 200)
        assert len(pts) == len(set(pts))
        assert min(x for x, _ in pts) == pytest.approx(0.0, abs=1e-9)
        assert max(x for x, _ in pts) == pytest.approx(200.0, abs=0.1)

    def test_symmetric_section_is_symmetric(self):
        pts = naca4_points("0012", 100)
        ys = [y for _, y in pts]
        assert max(ys) == pytest.approx(-min(ys), rel=1e-6)


class TestParser:
    @pytest.mark.parametrize("text,expected", [
        ("NACA 2412 wing, 600mm span", "airfoil_wing"),
        ("wing rib NACA 4412 chord 250", "wing_rib"),
        ("fuselage frame 300mm diameter", "fuselage_frame"),
        ("bulkhead 200mm diameter", "fuselage_frame"),
        ("wing spar I-beam 600mm", "wing_spar"),
        ("attachment lug 30mm wide 10mm pin hole", "attachment_lug"),
    ])
    def test_detects(self, text, expected):
        assert infer_part_type(text, [], []) == expected

    @pytest.mark.parametrize("text", ["wing nut M6", "swing arm bracket", "ribbon cable clip"])
    def test_no_false_positives(self, text):
        assert infer_part_type(text, [], []) not in ("airfoil_wing", "wing_rib", "fuselage_frame")


class TestValidator:
    def test_every_aero_part_carries_the_geometry_only_notice(self):
        for pt in ("airfoil_wing", "wing_rib", "fuselage_frame"):
            _, r = validate_parameters(pt, {})
            assert GEOMETRY_ONLY_NOTICE in r.warnings

    def test_bad_naca_rejected(self):
        _, r = validate_parameters("airfoil_wing", {"naca": "abc"})
        assert not r.is_valid

    def test_frame_ring_wider_than_radius_rejected(self):
        _, r = validate_parameters("fuselage_frame", {"outer_diameter_mm": 100, "ring_width_mm": 60})
        assert not r.is_valid

    def test_wing_metrics(self):
        m = wing_metrics({"root_chord_mm": 200, "tip_chord_mm": 100, "span_mm": 500})
        assert m["planform_area_mm2"] == 75000.0 and m["taper_ratio"] == 0.5


class TestTemplates:
    @pytest.fixture(autouse=True)
    def _cq(self):
        pytest.importorskip("cadquery")

    @pytest.mark.parametrize("name,params", [
        ("airfoil_wing", {}),
        ("airfoil_wing", {"sweep_deg": 20, "both_halves": True}),
        ("wing_rib", {}),
        ("wing_rib", {"naca": "0012", "lightening_hole_count": 0}),
        ("fuselage_frame", {}),
        ("fuselage_frame", {"flange_height_mm": 0, "lightening_hole_count": 0}),
        ("wing_spar", {}),
        ("attachment_lug", {}),
        ("attachment_lug", {"width_mm": 40, "hole_diameter_mm": 16}),
    ])
    def test_builds_a_positive_volume_solid(self, name, params):
        from cad_templates import TEMPLATES
        solid = TEMPLATES[name](params).val()
        assert solid.isValid()
        assert solid.Volume() > 0


class TestVehicleParts:
    @pytest.mark.parametrize("text,expected", [
        ("twin-engine jet aircraft with 35 m wingspan", "jet_aircraft"),
        ("turbofan engine with 2.4 m fan diameter", "turbofan_engine"),
        ("fuselage 30 m long", "fuselage"),
        ("vertical stabilizer", "tail_surface"),
    ])
    def test_detects(self, text, expected):
        assert infer_part_type(text, [], []) == expected

    @pytest.mark.parametrize("text", ["water jet cutter", "wing nut M6"])
    def test_no_false_positives(self, text):
        assert infer_part_type(text, [], []) not in ("jet_aircraft", "turbofan_engine", "fuselage", "tail_surface", "airfoil_wing")

    @pytest.mark.parametrize("name", ["fuselage", "tail_surface", "turbofan_engine", "jet_aircraft"])
    def test_validators_carry_notice(self, name):
        _, r = validate_parameters(name, {})
        assert GEOMETRY_ONLY_NOTICE in r.warnings


class TestVehicleTemplates:
    @pytest.fixture(autouse=True)
    def _cq(self):
        pytest.importorskip("cadquery")

    @pytest.mark.parametrize("name,params", [
        ("fuselage", {}),
        ("tail_surface", {"orientation": "vertical"}),
        ("tail_surface", {}),
        ("turbofan_engine", {"fan_blades": 0}),
        ("turbofan_engine", {}),
        ("jet_aircraft", {"engines": 0}),
        ("jet_aircraft", {"engines": 2, "engine_fan_blades": 0}),
    ])
    def test_builds_a_positive_volume_solid(self, name, params):
        from cad_templates import TEMPLATES
        shape = TEMPLATES[name](params).val()
        assert shape.Volume() > 0


class TestEngineStages:
    @pytest.fixture(autouse=True)
    def _cq(self):
        pytest.importorskip("cadquery")

    @pytest.mark.parametrize("params", [
        {"compressor_stages": 3, "turbine_stages": 2, "blades_per_row": 12, "fan_blades": 8},
        {"compressor_stages": 1, "turbine_stages": 0, "blades_per_row": 8, "fan_blades": 0},
    ])
    def test_stage_geometry_builds(self, params):
        from cad_templates import TEMPLATES
        assert TEMPLATES["turbofan_engine"](params).val().Volume() > 0
