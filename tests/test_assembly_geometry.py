"""
Real CadQuery/OCCT tests for interface-driven assemblies: the solved
placements are applied to real solids and checked for overlap.
Marked `requires_cadquery`.

STATUS: written but NOT executed in the environment this was authored in (no
CadQuery/OCCT available). The expectations below follow from the default
dimensions (see assembly_spec/mating docstrings and test_assembly.py's
hand-derived placements): in the default wheel corner every mating pair touches
face-to-face or line-to-line (zero overlap) and the closest non-mating
approach is 2mm. Run this file on a real install before relying on it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

cq = pytest.importorskip("cadquery", reason="requires a real CadQuery/OCCT install")

import assembly as assembly_mod
import assembly_spec as AS
import assembly_templates as AT
from cad_generator import CADGenerator
from exceptions import GeometryValidationError

pytestmark = pytest.mark.requires_cadquery


def _corner(**per_part):
    spec = AT.corner_module()
    for p in spec["parts"]:
        p["parameters"].update(per_part.get(p["id"], {}))
    return spec


def _built(spec):
    resolved = AS.resolve_assembly(spec)
    assert resolved.ok, [f.message for f in resolved.errors]
    return resolved, assembly_mod.build_parts(resolved)


class TestDefaultCorner:
    def test_no_solids_overlap(self):
        resolved, built = _built(AT.corner_module())
        findings = assembly_mod.check_interference(resolved, built)
        assert [f for f in findings if f.severity == "error"] == [], [f.message for f in findings]

    def test_no_inconclusive_boolean_either(self):
        resolved, built = _built(AT.corner_module())
        assert not [f for f in assembly_mod.check_interference(resolved, built) if f.code == "interference_inconclusive"]

    def test_every_mating_pair_actually_touches(self):
        """The solver says these faces coincide; the real solids must agree:
        growing one part by a hair must overlap its mate."""
        resolved, built = _built(AT.corner_module())
        placed = {pid: assembly_mod._placed_shape(built[pid], resolved.parts[pid].placement) for pid in resolved.order}
        for a, b in (("hub", "rotor"), ("hub", "knuckle"), ("knuckle", "ball_joint"), ("ball_joint", "arm"), ("arm", "subframe")):
            ba, bb = placed[a].BoundingBox(), placed[b].BoundingBox()
            gap = max(bb.xmin - ba.xmax, ba.xmin - bb.xmax, bb.ymin - ba.ymax, ba.ymin - bb.ymax,
                      bb.zmin - ba.zmax, ba.zmin - bb.zmax)
            assert gap <= 1e-3, f"{a}/{b} bounding boxes are {gap:.3f}mm apart"

    def test_hub_axis_is_horizontal_outboard_and_wheel_centre_height_matches_derivation(self):
        resolved, built = _built(AT.corner_module())
        hub = assembly_mod._placed_shape(built["hub"], resolved.parts["hub"].placement).BoundingBox()
        assert hub.zmin + (hub.zmax - hub.zmin) / 2 == pytest.approx(121.0, abs=0.6)    # 11 (boss top) + 110 (drop)
        assert (hub.ymax - hub.ymin) == pytest.approx(64.0, abs=0.5)                    # hub zlen runs along world Y

    def test_assembly_object_has_all_named_parts(self):
        resolved, built = _built(AT.corner_module())
        assy = assembly_mod.assemble_resolved(resolved, built)
        assert sorted(c.name for c in assy.children) == ["arm", "ball_joint", "hub", "knuckle", "rotor", "subframe"]

    @pytest.mark.parametrize("variant", [
        dict(hub={"lobed": True}),
        dict(rotor={"vented": False, "outer_diameter_mm": 330}),
        dict(arm={"arm_length_mm": 450}),
    ])
    def test_valid_variants_stay_collision_free(self, variant):
        resolved, built = _built(_corner(**variant))
        assert not [f for f in assembly_mod.check_interference(resolved, built) if f.severity == "error"]


class TestEnvelopeAgainstOCCT:
    """The analytic envelopes drive clearance measurement, the sweep and the
    stack-up. They are only as good as their agreement with the real solids,
    so compare enclosed volumes."""

    @pytest.mark.parametrize("part,params,step,rel", [
        ("brake_rotor", {"vented": False}, 5.0, 0.06),     # vented rotors are modelled as solid rings
        ("wheel_hub", {}, 3.0, 0.06),
        ("steering_knuckle", {}, 3.0, 0.06),
        ("ball_joint", {}, 1.5, 0.06),
        ("control_arm", {}, 3.0, 0.06),
        ("subframe", {}, 8.0, 0.10),
    ])
    def test_envelope_volume_matches_the_solid(self, part, params, step, rel):
        import domain_packs
        import envelope as E
        from cad_templates import TEMPLATES
        solid = TEMPLATES[part](params).val().Volume()
        est = E.volume(domain_packs.part_envelope(part, params), step)
        assert est == pytest.approx(solid, rel=rel), f"{part}: envelope {est:,.0f} mm3 vs solid {solid:,.0f} mm3"


class TestCollisionsAreCaught:
    def test_interface_valid_but_physically_colliding_assembly_is_detected(self):
        # A deeper rotor hat passes every dimensional mating rule but pushes
        # the rotor ring back into the control-arm ball-joint boss.
        resolved, built = _built(_corner(rotor={"hat_offset_mm": 60.0}))
        errs = [f for f in assembly_mod.check_interference(resolved, built) if f.severity == "error"]
        assert errs and all(f.code == "interference" for f in errs)

    def test_two_parts_placed_on_top_of_each_other_collide(self):
        spec = {"parts": [{"id": "h1", "part_type": "wheel_hub"},
                          {"id": "h2", "part_type": "wheel_hub", "placement": {"position": [0, 0, 0]}}]}
        resolved, built = _built(spec)
        assert any(f.code == "interference" for f in assembly_mod.check_interference(resolved, built))


class TestEndToEnd:
    def test_text_to_step_with_a_report(self, tmp_path):
        gen = CADGenerator(output_dir=str(tmp_path))
        r = gen.generate_from_text("wheel corner module, 5x114.3", user_id="assembly_test", formats=["step", "stl"])
        assert r["success"], r.get("error")
        assert r["parameters"]["parameters"]["assembly"]["parts"]            # resolved spec is what gets stored
        rep = r["assembly"]
        assert rep["ok"] and rep["interference_checked"]
        assert [p["id"] for p in rep["parts"]] == ["subframe", "arm", "ball_joint", "knuckle", "hub", "rotor"]
        assert rep["analytic_clearances"]["min_non_mating_clearance_mm"] == pytest.approx(2.0, abs=0.1)
        assert Path(r["step_file"]).stat().st_size > 5000
        assert not [f for f in rep["findings"] if f["severity"] == "error"]

    def test_mismatched_interfaces_are_blocked_before_any_geometry_is_exported(self, tmp_path):
        gen = CADGenerator(output_dir=str(tmp_path))
        spec = _corner(rotor={"center_bore_mm": 70.0})
        with pytest.raises(GeometryValidationError):
            gen._resolve_and_build_assembly(spec)
        assert not list(tmp_path.iterdir())

    def test_colliding_assembly_is_blocked(self, tmp_path):
        gen = CADGenerator(output_dir=str(tmp_path))
        with pytest.raises(GeometryValidationError):
            gen._resolve_and_build_assembly(_corner(rotor={"hat_offset_mm": 60.0}))
