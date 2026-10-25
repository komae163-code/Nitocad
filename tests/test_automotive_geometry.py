"""
Real CadQuery/OCCT geometry tests for the automotive pack. Marked
`requires_cadquery` per the existing convention (see test_cad_templates.py).

STATUS: written against the CadQuery 2.3.x API and checked for API-shape and
parameter-space robustness with a stand-in, but NOT executed against real OCCT
in the environment this was authored in (no network access to install
cadquery's native dependencies). Run this file on a machine with the project's
real dependencies before relying on any of the geometry. Expected bounding
boxes below are derived from the default parameters in each part's SPEC.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

cq = pytest.importorskip("cadquery", reason="requires a real CadQuery/OCCT install")

from cad_generator import CADGenerator
from cad_templates import TEMPLATES

pytestmark = pytest.mark.requires_cadquery

PARTS = ["brake_rotor", "wheel_hub", "piston", "flywheel", "control_arm", "steering_knuckle", "ball_joint",
         "subframe", "battery_bracket"]


def _valid(wp) -> bool:
    solids = wp.solids().vals()
    return bool(solids) and all(s.isValid() for s in solids)


def _single_solid(wp) -> bool:
    return len(wp.solids().vals()) == 1


def _bbox(part, params=None):
    return TEMPLATES[part](params or {}).val().BoundingBox()


def _volume(part, params=None):
    return TEMPLATES[part](params or {}).val().Volume()


@pytest.mark.parametrize("part", PARTS)
def test_registered_in_core_template_table(part):
    assert part in TEMPLATES


@pytest.mark.parametrize("part", PARTS)
def test_default_geometry_is_one_valid_solid(part):
    wp = TEMPLATES[part]({})
    assert _valid(wp)
    assert _single_solid(wp), f"{part} defaults produced a disconnected body"


class TestBrakeRotor:
    def test_extents(self):
        b = _bbox("brake_rotor")
        assert b.xlen == pytest.approx(300.0, abs=0.5)
        assert b.zlen == pytest.approx(40.0, abs=0.5)      # hat_offset_mm > thickness_mm

    def test_vented_is_lighter_than_solid_of_same_envelope(self):
        assert _volume("brake_rotor", {"vented": True}) < _volume("brake_rotor", {"vented": False})

    def test_vented_is_a_single_connected_solid(self):
        assert _single_solid(TEMPLATES["brake_rotor"]({"vented": True, "vane_count": 48}))

    def test_nothing_protrudes_past_the_rim(self):
        b = _bbox("brake_rotor", {"vane_count": 72, "vane_thickness_mm": 8})
        assert b.xlen == pytest.approx(300.0, abs=0.5)

    def test_bore_and_studs_are_through_the_mounting_plate(self):
        wp = TEMPLATES["brake_rotor"]({})
        probe = cq.Workplane("XY").workplane(offset=33.0).circle(1.0).extrude(4.0)   # on-axis, inside mounting plate
        assert len(probe.cut(wp).solids().vals()) > 0        # bore leaves it intact => nothing there


class TestWheelHub:
    def test_extents(self):
        b = _bbox("wheel_hub")
        assert b.xlen == pytest.approx(140.0, abs=0.5)
        assert b.zlen == pytest.approx(64.0, abs=0.5)        # barrel 40 + flange 12 + pilot 12

    def test_lobed_flange_uses_less_material_but_stays_connected(self):
        round_v = _volume("wheel_hub", {"lobed": False})
        lobed = TEMPLATES["wheel_hub"]({"lobed": True})
        assert _valid(lobed) and _single_solid(lobed)
        assert lobed.val().Volume() < round_v


class TestPiston:
    def test_extents(self):
        b = _bbox("piston")
        assert b.xlen == pytest.approx(86.0, abs=0.5)
        assert b.zlen == pytest.approx(62.0, abs=0.5)

    def test_ring_grooves_remove_material(self):
        assert _volume("piston", {"ring_groove_count": 3}) < _volume("piston", {"ring_groove_count": 0})

    def test_dish_removes_material(self):
        assert _volume("piston", {"crown_style": "dish"}) < _volume("piston", {"crown_style": "flat"})

    def test_underside_is_hollow_but_crown_is_closed(self):
        wp = TEMPLATES["piston"]({})
        under = cq.Workplane("XY").workplane(offset=2.0).circle(5.0).extrude(5.0)        # in the skirt cavity
        crown = cq.Workplane("XY").workplane(offset=60.0).circle(5.0).extrude(1.0)       # inside the crown
        assert len(under.cut(wp).solids().vals()) > 0           # cavity: probe survives
        assert len(crown.cut(wp).solids().vals()) == 0          # solid: probe fully consumed

    def test_pin_bore_runs_through_both_skirt_walls(self):
        wp = TEMPLATES["piston"]({})
        z_pin = 62.0 - 32.0
        for x in (-40.0, 40.0):
            probe = cq.Workplane("XY").workplane(offset=z_pin).center(x, 0).circle(1.0).extrude(0.5)
            assert len(probe.cut(wp).solids().vals()) > 0       # bore: no material at the pin axis, skirt wall


class TestFlywheel:
    def test_extents(self):
        b = _bbox("flywheel")
        assert b.xlen == pytest.approx(300.0, abs=0.5)
        assert b.zlen == pytest.approx(37.0, abs=0.5)        # hub boss 37 (25 + 12) from crank face to clutch face

    def test_ring_gear_teeth_remove_material_and_stay_valid(self):
        plain = _volume("flywheel", {"ring_gear_teeth": 0})
        toothed = TEMPLATES["flywheel"]({"ring_gear_teeth": 100})
        assert _valid(toothed)
        assert toothed.val().Volume() < plain

    def test_lightening_holes_remove_material(self):
        assert _volume("flywheel", {"lightening_hole_count": 6}) < _volume("flywheel", {"lightening_hole_count": 0})


class TestControlArm:
    def test_extents(self):
        b = _bbox("control_arm")
        assert b.xlen == pytest.approx(285.0, abs=1.0)       # 2 * (120 + 22.5)
        assert b.ylen == pytest.approx(427.0, abs=1.0)       # sleeve radius 19 below the pivot + 380 + ball-joint boss radius 28
        assert b.zlen == pytest.approx(38.0, abs=1.0)        # sleeve OD

    def test_cross_brace_adds_material_and_stays_one_piece(self):
        braced = TEMPLATES["control_arm"]({"cross_brace": True})
        assert _valid(braced) and _single_solid(braced)
        assert braced.val().Volume() > _volume("control_arm", {"cross_brace": False})

    def test_legs_fuse_to_sleeves_and_boss(self):
        assert _single_solid(TEMPLATES["control_arm"]({}))


class TestSteeringKnuckle:
    def test_extents(self):
        b = _bbox("steering_knuckle")
        assert b.xlen == pytest.approx(120.0, abs=0.5)       # housing diameter
        assert b.ylen == pytest.approx(170.0, abs=0.5)       # +60 housing top .. -110 leg bottom face (the 110mm drop)
        assert b.zlen == pytest.approx(62.0, abs=0.5)        # leg inboard face (-62) .. housing outboard face (0)

    def test_bearing_bore_is_open_and_leg_is_fused_to_the_housing(self):
        wp = TEMPLATES["steering_knuckle"]({})
        assert _single_solid(wp)
        probe = cq.Workplane("XY").workplane(offset=-30.0).circle(10.0).extrude(5.0)   # inside the bore
        assert len(probe.cut(wp).solids().vals()) > 0


class TestBallJoint:
    def test_extents(self):
        b = _bbox("ball_joint")
        assert b.xlen == pytest.approx(42.0, abs=0.5)        # flange diameter (30 + 12)
        assert b.zlen == pytest.approx(62.0, abs=0.5)        # housing 24 + flange 8 + stud 30

    def test_stud_is_a_true_cone_narrower_at_the_tip(self):
        wp = TEMPLATES["ball_joint"]({})
        base = cq.Workplane("XY").workplane(offset=9.0).circle(10.0).extrude(0.5)     # inside the base of the stud (r=11 there)
        tip = cq.Workplane("XY").workplane(offset=37.0).circle(10.0).extrude(0.5)     # r<=9.5 at the tip: this probe pokes out
        assert len(base.cut(wp).solids().vals()) == 0
        assert len(tip.cut(wp).solids().vals()) > 0


class TestSubframe:
    def test_extents_and_connectivity(self):
        b = _bbox("subframe")
        assert b.xlen == pytest.approx(700.0, abs=0.5)
        assert b.ylen == pytest.approx(760.0, abs=0.5)       # 2 * (350 + 30)
        assert b.zlen == pytest.approx(110.0, abs=0.5)       # ears -35 .. rail top 75
        assert _single_solid(TEMPLATES["subframe"]({}))

    def test_clevis_ear_bores_run_along_x_on_the_pivot_axis(self):
        wp = TEMPLATES["subframe"]({})
        for x in (120.0 + 23.5 + 4.0, 120.0 - 23.5 - 4.0):                             # through each ear of the left-front clevis
            probe = cq.Workplane("XY").workplane(offset=-1.0).center(x, 350.0).circle(1.0).extrude(2.0)
            assert len(probe.cut(wp).solids().vals()) > 0                              # the bore leaves the probe intact


class TestBatteryBracket:
    def test_extents(self):
        b = _bbox("battery_bracket")
        assert b.xlen == pytest.approx(60.0, abs=0.5)
        assert b.ylen == pytest.approx(120.0, abs=0.5)
        assert b.zlen == pytest.approx(80.0, abs=0.5)

    def test_sharp_bend_variant_is_valid(self):
        assert _valid(TEMPLATES["battery_bracket"]({"bend_radius_mm": 0, "gusset_count": 0}))

    def test_gussets_add_material_and_fuse(self):
        with_g = TEMPLATES["battery_bracket"]({"gusset_count": 3})
        assert _single_solid(with_g)
        assert with_g.val().Volume() > _volume("battery_bracket", {"gusset_count": 0})

    def test_slotted_holes_remove_more_material_than_round(self):
        assert _volume("battery_bracket", {"slotted": True}) < _volume("battery_bracket", {"slotted": False})

    def test_inside_bend_has_the_requested_radius_not_a_sharp_corner(self):
        # A radiused bend adds material at the inside corner and removes it at the outside corner;
        # the two cancel only approximately, so the volumes must differ if the arc is really in the profile.
        assert _volume("battery_bracket", {"bend_radius_mm": 12, "gusset_count": 0}) != pytest.approx(
            _volume("battery_bracket", {"bend_radius_mm": 0, "gusset_count": 0}), rel=1e-6)


class TestEndToEnd:
    @pytest.mark.parametrize("description,part", [
        ("300mm vented brake rotor, 28mm thick, 5x114.3, 66.1mm center bore", "brake_rotor"),
        ("wheel hub with 5x114.3 stud pattern, 140mm flange diameter, lobed", "wheel_hub"),
        ("86mm dished piston with 2 rings and 22mm wrist pin", "piston"),
        ("flywheel 300mm diameter, 25mm thick, 132 teeth", "flywheel"),
        ("lower control arm 380mm long, 10mm thick, 30mm wide, with cross brace", "control_arm"),
        ("EV battery enclosure bracket, 120mm base length, 80mm tall, 60mm wide, 6mm thick, 13mm holes, 3 gussets, slotted", "battery_bracket"),
    ])
    def test_text_to_exported_step_and_stl(self, tmp_path, description, part):
        gen = CADGenerator(output_dir=str(tmp_path))
        result = gen.generate_from_text(description, user_id="automotive_test")
        assert result["success"], result.get("error")
        assert result["part_type"] == part
        assert Path(result["step_file"]).stat().st_size > 1000
        assert Path(result["stl_file"]).stat().st_size > 1000
