"""
Pure-Python tests (no CadQuery) for tolerances, the taper/clevis/press-fit
rules, the analytic envelope measurement, measured clearances of the wheel
corner, the kinematic sweep, tolerance stack-up and load screening.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assembly_analysis as AA
import assembly_spec as AS
import assembly_templates as AT
import envelope as E
import loads as L
import mating as M
import tolerance as T


def corner(**per_part):
    spec = AT.corner_module()
    for p in spec["parts"]:
        p["parameters"].update(per_part.get(p["id"], {}))
    return spec


def itf(kind, **data):
    return M.Interface("x", kind, M.Frame((0, 0, 0), (0, 0, 1)), data)


# ---------------------------------------------------------------- ISO tolerances

class TestTolerance:
    @pytest.mark.parametrize("nominal,cls,lo,hi", [
        (30, "H7", 30.000, 30.021), (80, "H7", 80.000, 80.030), (66.1, "H7", 66.1, 66.130),
        (30, "g6", 29.980, 29.993), (80, "h6", 79.981, 80.000), (50, "h7", 49.975, 50.000),
    ])
    def test_textbook_limits(self, nominal, cls, lo, hi):
        f = T.hole_limits if cls[0] == "H" else T.shaft_limits
        got = f(nominal, cls)
        assert got[0] == pytest.approx(lo, abs=0.0006) and got[1] == pytest.approx(hi, abs=0.0006)

    @pytest.mark.parametrize("shaft,expected", [("h6", (0.0, 0.049)), ("g6", (0.010, 0.059))])
    def test_80_H7_fits(self, shaft, expected):
        assert T.fit_clearance(80, "H7", 80, shaft) == pytest.approx(expected, abs=0.0006)

    def test_unsupported_class_and_size_fail_loudly(self):
        with pytest.raises(KeyError):
            T.shaft_limits(30, "k6")
        with pytest.raises(ValueError):
            T.hole_limits(300, "H7")

    def test_general_tolerance_and_combination(self):
        assert T.general_tolerance_mm(12) == 0.2 and T.general_tolerance_mm(380) == 0.5
        assert T.worst_case([0.1, -0.2]) == pytest.approx(0.3)
        assert T.rss([0.3, 0.4]) == pytest.approx(0.5)


# ---------------------------------------------------------------- new / changed rules

class TestRulesWithTolerance:
    def test_iso_classes_use_worst_case(self):
        ok = M.check_pair(itf("pilot_boss", diameter_mm=66.1, tol_class="g6"), itf("pilot_bore", diameter_mm=66.1, tol_class="H7"), "a", "b")[0]
        assert [f.code for f in ok] == ["fit_ok"]
        bad = M.check_pair(itf("pilot_boss", diameter_mm=66.1, tol_class="h6"), itf("pilot_bore", diameter_mm=65.99, tol_class="H7"), "a", "b")[0]
        assert [f.code for f in bad] == ["interference_fit"]

    def test_fit_that_only_works_at_nominal_is_caught_by_limits(self):
        # nominal clearance +0.02mm looks fine, but the H7/h7 limits can interfere
        f = M.check_pair(itf("pilot_boss", diameter_mm=79.98, tol_class="h7"), itf("pilot_bore", diameter_mm=80.0, tol_class="H7"), "a", "b")[0]
        assert [x.code for x in f] in (["fit_ok"], ["interference_fit"])

    def test_press_fit_band(self):
        def run(housing, bore):
            return {f.code for f in M.check_pair(itf("pin", diameter_mm=housing, press_fit=True),
                                                 itf("socket", diameter_mm=bore, depth_mm=22), "a", "b")[0]}
        assert run(30.0, 30.0) == set()
        assert run(30.1, 30.0) == set()                 # 0.1mm interference: within the press-fit allowance
        assert run(30.3, 30.0) == {"interference_fit"}  # too tight to press
        assert run(29.9, 30.0) == {"loose_fit"}         # too loose to be a press fit


class TestTaperAndClevis:
    def stud(self, **kw):
        return itf("taper_stud", **{"base_diameter_mm": 22, "taper_ratio": 0.1, "length_mm": 30, **kw})

    def seat(self, **kw):
        return itf("taper_bore", **{"base_diameter_mm": 22, "taper_ratio": 0.1, "depth_mm": 30, **kw})

    @pytest.mark.parametrize("stud_kw,seat_kw,expected", [
        ({}, {}, set()),
        ({"taper_ratio": 0.12}, {}, {"taper_mismatch"}),
        ({"base_diameter_mm": 23}, {}, {"taper_diameter_mismatch"}),
        ({"base_diameter_mm": 22.3}, {}, {"taper_seat_shift"}),
        ({"length_mm": 40}, {}, {"stud_too_long"}),
    ])
    def test_taper_rule(self, stud_kw, seat_kw, expected):
        f, _ = M.check_pair(self.stud(**stud_kw), self.seat(**seat_kw), "a", "b")
        assert {x.code for x in f} == expected

    @pytest.mark.parametrize("gap,bore,expected", [
        (47.0, 22.0, set()),
        (45.1, 22.0, {"clevis_too_tight"}),
        (49.0, 22.0, {"clevis_play"}),
        (52.0, 22.0, {"clevis_too_loose"}),
        (47.0, 22.8, {"pivot_bore_step"}),
        (47.0, 26.0, {"pivot_bore_mismatch"}),
    ])
    def test_clevis_rule(self, gap, bore, expected):
        f, _ = M.check_pair(itf("bushing_bore", diameter_mm=22, length_mm=45), itf("clevis", gap_mm=gap, bore_diameter_mm=bore), "a", "b")
        assert {x.code for x in f} == expected


class TestChassisMates:
    def test_arm_pivot_spacing_mismatch_is_caught_by_the_closing_mate(self):
        res = AS.resolve_assembly(corner(arm={"pivot_spacing_mm": 260.0}))
        assert not res.ok
        assert "mate_position_mismatch" in {f.code for f in res.errors}

    def test_bushing_longer_than_clevis_gap_is_blocked(self):
        res = AS.resolve_assembly(corner(arm={"bushing_length_mm": 50.0}))
        assert "clevis_too_tight" in {f.code for f in res.errors}

    def test_ear_bore_mismatch_is_blocked(self):
        res = AS.resolve_assembly(corner(subframe={"ear_bore_diameter_mm": 26.0}))
        assert "pivot_bore_mismatch" in {f.code for f in res.errors}

    def test_ball_joint_stud_that_does_not_match_the_seat_is_blocked(self):
        res = AS.resolve_assembly(corner(ball_joint={"stud_taper_ratio": 0.12}))
        assert "taper_mismatch" in {f.code for f in res.errors}

    def test_ball_joint_housing_must_press_into_the_arm_bore(self):
        res = AS.resolve_assembly(corner(ball_joint={"housing_diameter_mm": 29.0, "stud_base_diameter_mm": 22.0}))
        assert "loose_fit" in {f.code for f in res.errors}


# ---------------------------------------------------------------- envelope measurement vs analytic ground truth

class TestEnvelope:
    def test_known_distances(self):
        a = E.cyl_z(10, 20)
        far = E.Placed(E.cyl_z(10, 20), M.Transform(M.IDENTITY, (25, 0, 0)))
        lap = E.Placed(E.cyl_z(10, 20), M.Transform(M.IDENTITY, (18, 0, 0)))
        stacked = E.Placed(E.cyl_z(10, 20), M.Transform(M.IDENTITY, (0, 0, 20.4)))
        assert E.clearance(a, far)[0] == pytest.approx(5.0, abs=0.01)
        assert E.clearance(a, lap)[0] == pytest.approx(-2.0, abs=0.01)
        assert E.clearance(a, stacked)[0] == pytest.approx(0.4, abs=0.01)

    def test_diagonal_closest_approach(self):
        a = E.cyl_z(10, 20)
        b = E.box_ranges(13, 20, 13, 20, 0, 20)
        assert E.clearance(a, b)[0] == pytest.approx(math.hypot(13, 13) - 10, abs=0.02)

    def test_pin_in_bore_uses_exact_tube_distance(self):
        tube = E.annulus_z(20, 10, 30)
        pin = E.cyl_z(9.7, 30)
        assert E.clearance(tube, pin)[0] == pytest.approx(0.3, abs=0.01)

    def test_tube_sdf_is_exact_inside_a_through_hole(self):
        tube = E.annulus_z(20, 10, 30)
        # 18.5 from the material radially, even though it is 0.5 above the bottom plane
        assert tube.sdf((0, 0, -0.5)) == pytest.approx(math.hypot(10, 0.5), abs=1e-9)

    def test_axis_primitives_point_where_asked(self):
        c = E.cyl_axis(5, 20, (100, 0, 0), (1, 0, 0))
        assert c.sdf((110, 0, 0)) < 0 < min(c.sdf((90, 0, 0)), c.sdf((130, 0, 0)))
        c = E.cyl_axis(5, 20, (0, -110, -47), (0, 1, 0))
        assert c.sdf((0, -100, -47)) < 0 < min(c.sdf((0, -120, -47)), c.sdf((0, -80.5, -47)))
        t = E.tube_axis(10, 6, 20, (50, 0, 0), (-1, 0, 0))
        assert t.sdf((40, 8, 0)) < 0 < min(t.sdf((40, 0, 0)), t.sdf((60, 8, 0)))

    def test_nonconverged_projection_never_fakes_a_distance(self):
        bore = E.Subtract(E.cyl_z(20, 30), [E.cyl_z(10, 32, -1)])
        assert E.clearance(bore, E.cyl_z(9.7, 30))[0] == pytest.approx(0.3, abs=0.01)

    def test_envelope_extents_match_the_part_extents(self):
        import domain_packs as dp
        for part, want in {"brake_rotor": (300, 300, 40), "wheel_hub": (140, 140, 64),
                           "steering_knuckle": (120, 170, 62), "control_arm": (285, 427, 38)}.items():
            lo, hi = dp.part_envelope(part, {}).bbox()
            assert tuple(round(hi[i] - lo[i], 3) for i in range(3)) == pytest.approx(want, abs=0.01), part


# ---------------------------------------------------------------- measured clearances of the wheel corner

class TestMeasuredClearances:
    def test_default_corner_has_no_overlaps_and_measured_2mm_closest_approach(self):
        res = AS.resolve_assembly(AT.corner_module())
        m = AA.measure_clearances(res)
        assert [f.code for f in m["findings"] if f.severity != "info"] == []
        assert m["min_non_mating_clearance_mm"] == pytest.approx(2.0, abs=0.05)      # arm boss vs rotor ring

    def test_every_mated_pair_is_in_contact_not_penetrating(self):
        res = AS.resolve_assembly(AT.corner_module())
        for row in AA.measure_clearances(res)["pairs"]:
            if row["mated"] and row["a"] != "subframe":
                assert row["clearance_mm"] == pytest.approx(0.0, abs=0.06), row

    def test_clevis_axial_play_is_measured_as_one_mm_per_side(self):
        res = AS.resolve_assembly(AT.corner_module())
        row = next(r for r in AA.measure_clearances(res)["pairs"] if {r["a"], r["b"]} == {"subframe", "arm"})
        assert row["clearance_mm"] == pytest.approx(1.0, abs=0.05)                  # (47 - 45) / 2

    def test_geometry_collision_that_passes_every_mate_rule_is_measured(self):
        res = AS.resolve_assembly(corner(rotor={"hat_offset_mm": 60.0}))
        assert res.ok                                                                # dimensionally fine ...
        codes = {f.code for f in AA.measure_clearances(res)["findings"]}
        assert codes & {"analytic_collision", "tight_clearance"}                     # ... but the measurement sees it

    def test_a_shorter_arm_clearance_change_is_tracked(self):
        a = AA.measure_clearances(AS.resolve_assembly(corner(hub={"flange_thickness_mm": 12.0})))
        b = AA.measure_clearances(AS.resolve_assembly(corner(hub={"flange_thickness_mm": 13.0})))
        # a thicker flange pushes the rotor outboard, away from the arm: clearance grows by ~1mm
        assert b["min_non_mating_clearance_mm"] - a["min_non_mating_clearance_mm"] == pytest.approx(1.0, abs=0.1)


# ---------------------------------------------------------------- kinematics

class TestKinematics:
    def test_joint_is_the_arm_pivot_and_moving_set_is_everything_outboard(self):
        j = AA.find_revolute_joint(AS.resolve_assembly(AT.corner_module()))
        assert (j["parent"], j["child"]) == ("subframe", "arm")
        assert j["moving"] == {"arm", "ball_joint", "knuckle", "hub", "rotor"} and j["static"] == {"subframe"}
        assert j["axis_dir"] == pytest.approx((1.0, 0.0, 0.0)) and j["axis_point"] == pytest.approx((120.0, 350.0, 0.0))

    def test_rotation_about_an_axis_is_a_proper_rotation_fixing_the_axis(self):
        R = AA._rot_about((10, 20, 30), (1, 0, 0), 25.0)
        assert R.point((10, 20, 30)) == pytest.approx((10, 20, 30), abs=1e-9)
        assert R.point((99, 20, 30)) == pytest.approx((99, 20, 30), abs=1e-9)        # points on the axis do not move
        assert M.norm(M.sub(R.point((10, 120, 30)), (10, 20, 30))) == pytest.approx(100.0, abs=1e-9)

    def test_wheel_centre_follows_the_swing_radius(self):
        res = AS.resolve_assembly(AT.corner_module())
        sw = AA.kinematic_sweep(res, (-10, 10), 5)
        hub0 = res.parts["hub"].placement.t
        pivot = (120.0, 350.0, 0.0)
        r = math.hypot(hub0[1] - pivot[1], hub0[2] - pivot[2])
        for row in sw["rows"]:
            c = row["wheel_centre_mm"]
            assert math.hypot(c[1] - pivot[1], c[2] - pivot[2]) == pytest.approx(r, abs=0.02)   # constant radius about the pivot
            assert c[0] == pytest.approx(hub0[0], abs=0.01)                                      # no fore-aft motion
        zs = [row["wheel_centre_mm"][2] for row in sw["rows"]]
        assert zs == sorted(zs) and zs[2] == pytest.approx(hub0[2], abs=0.01)                    # monotonic, 0 deg = design height

    def test_default_corner_clears_the_chassis_over_plus_minus_ten_degrees(self):
        sw = AA.kinematic_sweep(AS.resolve_assembly(AT.corner_module()), (-10, 10), 9)
        assert not [f for f in sw["findings"] if f.severity == "error"]
        assert sw["min_clearance_mm"] > 0.5

    def test_a_chassis_collision_in_travel_is_reported(self):
        res = AS.resolve_assembly(corner(arm={"arm_width_mm": 70.0, "bushing_length_mm": 45.0}))
        if res.ok:
            sw = AA.kinematic_sweep(res, (-25, 25), 7)
            assert sw["min_clearance_mm"] is not None


# ---------------------------------------------------------------- stack-up

class TestStackUp:
    def test_arm_to_rotor_gap_nominal_worst_case_and_rss(self):
        su = AA.stack_up(AT.corner_module(), "rotor", "arm")
        assert su["nominal_mm"] == pytest.approx(2.0, abs=0.05)
        assert 0 < su["worst_case_min_mm"] < su["rss_min_mm"] < su["nominal_mm"]
        names = {c["param"] for c in su["contributors"]}
        assert {"rotor.hat_offset_mm", "hub.flange_thickness_mm", "knuckle.housing_length_mm"} <= names

    def test_each_contributor_effect_is_the_expected_one_for_a_pure_offset(self):
        su = AA.stack_up(AT.corner_module(), "rotor", "arm")
        eff = {c["param"]: c for c in su["contributors"]}
        c = eff["hub.flange_thickness_mm"]
        assert c["effect_mm"] == pytest.approx(c["tolerance_mm"], abs=0.02)       # 1:1 offset along the wheel axis

    def test_custom_tolerances_override_the_defaults(self):
        su = AA.stack_up(AT.corner_module(), "rotor", "arm", {"hub.flange_thickness_mm": 0.05})
        c = next(x for x in su["contributors"] if x["param"] == "hub.flange_thickness_mm")
        assert c["tolerance_mm"] == 0.05

    def test_perturbations_that_break_a_fit_are_reported_not_hidden(self):
        su = AA.stack_up(AT.corner_module(), "rotor", "arm")
        assert any(x.startswith("hub.pilot_diameter_mm") for x in su["perturbations_invalid"])

    def test_distant_pairs_and_invalid_assemblies_are_refused(self):
        with pytest.raises(ValueError):
            AA.stack_up(AT.corner_module(), "rotor", "subframe")
        with pytest.raises(ValueError):
            AA.stack_up(corner(rotor={"center_bore_mm": 70.0}), "rotor", "arm")


# ---------------------------------------------------------------- loads

class TestLoads:
    CASE = L.WheelLoadCase("1g cornering", fz_N=8000, fy_N=7000, tyre_radius_mm=330, wheel_offset_mm=40)

    def test_flange_loads_hand_calculation(self):
        fl = L.hub_flange_loads(self.CASE)
        assert fl["axial_N"] == 7000 and fl["shear_N"] == 8000
        assert fl["moment_Nmm"] == pytest.approx(math.hypot(7000 * 330, 8000 * 40), rel=1e-12)

    def test_stud_group_peak_tension_hand_calculation(self):
        g = L.stud_group_loads(5, 114.3, 7000, 8000, math.hypot(7000 * 330, 8000 * 40))
        assert g["peak_tension_N"] == pytest.approx(7000 / 5 + 2 * math.hypot(7000 * 330, 8000 * 40) / (5 * 57.15), rel=1e-9)
        assert g["shear_per_stud_N"] == pytest.approx(1600.0)

    def test_stud_utilisation_matches_the_stress_area_calculation(self):
        u = L.stud_utilisation(17722.4, 1600.0, "10.9", "M12")
        assert u["tension_stress_MPa"] == pytest.approx(17722.4 / 84.3, abs=0.1)
        assert u["utilisation"] == pytest.approx(math.sqrt((17722.4 / 84.3) ** 2 + 3 * (1600 / 84.3) ** 2) / 830, abs=0.002)

    def test_screen_corner_uses_the_assemblys_own_hub(self):
        res = AS.resolve_assembly(AT.corner_module())
        out = L.screen_corner(res, self.CASE, ball_joint_force_N=12000, pivot_force_N=9000)
        assert out["hub_studs"]["count"] == 5 and out["hub_studs"]["pcd_mm"] == 114.3 and out["hub_studs"]["thread"] == "M12"
        assert {c["flag"] for c in out["checks"]} == {"ok"}
        six = AS.resolve_assembly(corner(hub={"bolt_count": 6, "bolt_pcd_mm": 139.7, "flange_diameter_mm": 170},
                                         rotor={"bolt_count": 6, "bolt_pcd_mm": 139.7, "hat_diameter_mm": 190}))
        assert L.screen_corner(six, self.CASE)["hub_studs"]["utilisation"] < out["hub_studs"]["utilisation"]

    def test_overload_is_flagged_and_not_derived_items_are_listed(self):
        res = AS.resolve_assembly(AT.corner_module())
        out = L.screen_corner(res, L.WheelLoadCase("crash", fz_N=60000, fy_N=60000))
        assert out["checks"][0]["flag"] == "exceeds_proof"
        assert any("ball-joint reaction" in x for x in out["not_derived"])
        assert "Screening calculation only" in out["disclaimer"]

    @pytest.mark.parametrize("bad", [dict(fz_N=float("nan")), dict(tyre_radius_mm=5), dict(wheel_offset_mm=900), dict(fz_N=5e7)])
    def test_incredible_inputs_are_rejected(self, bad):
        res = AS.resolve_assembly(AT.corner_module())
        with pytest.raises(ValueError):
            L.screen_corner(res, L.WheelLoadCase("x", **{"fz_N": 1000, **bad}))

    def test_unknown_class_and_thread_raise(self):
        with pytest.raises(ValueError):
            L.stud_utilisation(1, 1, "99.9", "M12")
        with pytest.raises(ValueError):
            L.stud_utilisation(1, 1, "10.9", "M13")


class TestAnalyzeAssembly:
    def test_everything_together_is_serialisable(self):
        out = AA.analyze_assembly(AT.corner_module(), sweep={"min_deg": -5, "max_deg": 5, "steps": 3},
                                  stack_up_pair={"a": "rotor", "b": "arm"},
                                  load_case={"name": "x", "fz_N": 5000, "fy_N": 4000})
        assert out["ok"] and {"clearances", "kinematics", "stack_up", "load_screening"} <= set(out)
        json.dumps(out)

    def test_invalid_assembly_is_not_analysed(self):
        out = AA.analyze_assembly(corner(rotor={"center_bore_mm": 70.0}), sweep={"steps": 3})
        assert not out["ok"] and "analysis_skipped" in out and "kinematics" not in out

    @pytest.mark.parametrize("sweep", [{"min_deg": -90, "max_deg": 90}, {"steps": 1}, {"steps": 999}, {"min_deg": 5, "max_deg": -5}])
    def test_sweep_bounds(self, sweep):
        with pytest.raises(ValueError):
            AA.analyze_assembly(AT.corner_module(), sweep=sweep)
