"""
Tests for civil/ (survey, terrain, alignments, earthworks, hydraulics, loads,
wall stability, criteria). Pure Python plus numpy, no CAD kernel and no server.

Where possible the expected value is worked out independently: an exact property
of the shape (a half-full circular pipe carries half the full flow), a hand
calculation, or a round trip (solve for depth, then recompute the flow).
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from civil import criteria as cr
from civil import earthworks as ew
from civil import hydraulics as hy
from civil import loads, survey, wall_stability
from civil.alignment import HorizontalAlignment, VerticalAlignment, stations_for
from civil.errors import CivilInputError
from civil.terrain import Terrain


# --------------------------------------------------------------------- survey
class TestSurvey:
    def test_header_names_in_any_order(self):
        r = survey.parse_points("P,N,E,Z\n1,20,10,5\n2,20,30,6\n3,40,10,7\n")
        p = r.points[0]
        assert (p.x, p.y, p.z, p.id) == (10, 20, 5, "1")

    def test_no_header_three_and_four_columns(self):
        assert survey.parse_points("0 0 1\n10 0 2\n0 10 3").points[1].x == 10
        r = survey.parse_points("A,0,0,1\nB,10,0,2\nC,0,10,3")
        assert r.points[2].id == "C" and r.points[2].y == 10

    def test_feet_convert_to_metres(self):
        r = survey.parse_points("0,0,0\n100,0,10\n0,100,20", units="ft")
        assert r.points[1].x == pytest.approx(30.48) and r.points[2].z == pytest.approx(6.096)

    def test_bad_lines_are_skipped_with_a_warning(self):
        r = survey.parse_points("x,y,z\n0,0,1\n10,0,2\nnope,1,2\n0,10,3\n")
        assert len(r.points) == 3 and any("Skipped 1" in w for w in r.warnings)

    def test_duplicates_dropped_and_conflicts_reported(self):
        r = survey.parse_points("0,0,1\n0,0,9\n10,0,2\n0,10,3\n")
        assert len(r.points) == 3 and any("different height" in w for w in r.warnings)

    @pytest.mark.parametrize("text", ["", "x,y\n1,2\n", "0,0,1\n1,1,2\n", "# only a comment"])
    def test_unusable_files_raise(self, text):
        with pytest.raises(CivilInputError):
            survey.parse_points(text)

    def test_point_limit(self):
        text = "\n".join(f"{i},{i % 7},{i % 5}" for i in range(50))
        with pytest.raises(CivilInputError):
            survey.parse_points(text, max_points=10)

    def test_height_outlier_flagged(self):
        rows = [f"{x},{y},{50 + 0.01 * x}" for x in range(0, 100, 10) for y in (0, 10)]
        rows.append("55,5,5000")
        r = survey.parse_points("\n".join(rows))
        assert any("far from the typical height" in w for w in r.warnings)


# -------------------------------------------------------------------- terrain
def _plane(n=120, seed=3):
    rnd = random.Random(seed)
    pts = [(rnd.uniform(0, 100), rnd.uniform(0, 100)) for _ in range(n)] + [(0, 0), (100, 0), (0, 100), (100, 100)]
    return [(x, y, 0.1 * x + 0.2 * y + 5) for x, y in pts]


class TestTerrain:
    def test_planar_ground_is_reproduced_exactly(self):
        t = Terrain(_plane())
        rnd = random.Random(9)
        for _ in range(200):
            x, y = rnd.uniform(1, 99), rnd.uniform(1, 99)
            assert t.z_at(x, y) == pytest.approx(0.1 * x + 0.2 * y + 5, abs=1e-9)

    def test_outside_the_surface_is_none(self):
        t = Terrain(_plane())
        assert t.z_at(-10, 50) is None and t.z_at(50, 150) is None

    def test_hull_is_fully_covered(self):
        assert Terrain(_plane()).stats()["plan_area_m2"] == pytest.approx(10000.0, rel=1e-9)

    def test_delaunay_empty_circle_property(self):
        t = Terrain(_plane(60))
        pp = t.points[:, :2]
        for tri in t.triangles:
            a, b, c = pp[tri]
            d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
            ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
            uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
            r2 = (a[0] - ux) ** 2 + (a[1] - uy) ** 2
            inside = ((pp[:, 0] - ux) ** 2 + (pp[:, 1] - uy) ** 2 < r2 * (1 - 1e-9)).sum()
            assert inside == 0

    def test_collinear_points_are_rejected(self):
        with pytest.raises(CivilInputError):
            Terrain([(0, 0, 1), (1, 1, 2), (2, 2, 3), (3, 3, 4)])

    def test_profile_and_stl(self):
        t = Terrain(_plane())
        prof = t.profile([(10, 10), (90, 10)], 10)
        assert prof[0]["distance"] == 0 and prof[-1]["distance"] == pytest.approx(80)
        assert prof[3]["z"] == pytest.approx(0.1 * prof[3]["x"] + 0.2 * 10 + 5)
        stl = t.to_ascii_stl()
        assert stl.startswith("solid") and stl.count("facet normal") == len(t.triangles)

    def test_long_edges_can_be_dropped(self):
        pts = [(0, 0, 0), (10, 0, 0), (0, 10, 0), (10, 10, 0), (200, 5, 0), (200, 0, 0)]
        full = Terrain(pts)
        trimmed = Terrain(pts, max_edge_m=30)
        assert len(trimmed.triangles) < len(full.triangles)


# ------------------------------------------------------------------ alignment
class TestHorizontal:
    def test_right_angle_curve_length_and_geometry(self):
        h = HorizontalAlignment([(0, 0), (100, 0), (100, 100)], [0, 30, 0])
        assert h.length == pytest.approx(70 + 30 * math.pi / 2 + 70)
        c = h.curves[0]
        assert c["tangent"] == pytest.approx(30) and c["direction"] == "left"
        assert c["tc_station"] == pytest.approx(70)

    def test_points_on_the_arc_lie_on_the_circle(self):
        h = HorizontalAlignment([(0, 0), (100, 0), (100, 100)], [0, 30, 0])
        for s in (75, 90, 105, 116):
            p = h.point_at(s)
            assert math.hypot(p["x"] - 70, p["y"] - 30) == pytest.approx(30, abs=1e-9)

    def test_bearing_turns_from_east_to_north_and_right_turn(self):
        h = HorizontalAlignment([(0, 0), (100, 0), (100, 100)], [0, 30, 0])
        assert h.point_at(10)["bearing_deg"] == pytest.approx(90)
        assert h.point_at(h.length)["bearing_deg"] == pytest.approx(0, abs=1e-6)
        r = HorizontalAlignment([(0, 0), (100, 0), (100, -100)], [0, 30, 0])
        assert r.curves[0]["direction"] == "right"
        assert r.point_at(r.length)["bearing_deg"] == pytest.approx(180)

    def test_offset_left_of_travel(self):
        h = HorizontalAlignment([(0, 0), (100, 0)])
        assert h.offset_point(50, 3) == pytest.approx((50, 3))

    def test_overlapping_curves_and_bad_input_raise(self):
        with pytest.raises(CivilInputError):
            HorizontalAlignment([(0, 0), (50, 0), (50, 50)], [0, 80, 0])
        with pytest.raises(CivilInputError):
            HorizontalAlignment([(0, 0), (0, 0)])
        with pytest.raises(CivilInputError):
            HorizontalAlignment([(0, 0), (10, 0)], [0, -1])

    def test_station_outside_range_raises(self):
        h = HorizontalAlignment([(0, 0), (100, 0)])
        with pytest.raises(CivilInputError):
            h.point_at(101)


class TestVertical:
    def test_crest_curve_values(self):
        v = VerticalAlignment([(0, 100, 0), (500, 110, 100), (1000, 100, 0)])
        assert v.elevation(500) == pytest.approx(109.5)        # |A| L / 800 below the PVI
        assert v.elevation(0) == 100 and v.elevation(250) == pytest.approx(105)
        c = v.curves[0]
        assert c["k_value"] == pytest.approx(25) and c["type"] == "crest"
        assert c["high_low_point_station"] == pytest.approx(500)

    def test_grade_is_continuous_across_the_curve(self):
        v = VerticalAlignment([(0, 100, 0), (500, 110, 100), (1000, 100, 0)])
        assert v.grade_at(449.99) == pytest.approx(0.02, abs=1e-4)
        assert v.grade_at(500) == pytest.approx(0.0, abs=1e-9)
        assert v.grade_at(550.01) == pytest.approx(-0.02, abs=1e-4)

    @pytest.mark.parametrize("pvis", [
        [(0, 100, 0), (100, 110, 0), (50, 100, 0)],                    # stations go backwards
        [(0, 100, 10), (100, 110, 0)],                                 # curve on the first PVI
        [(0, 100, 0), (100, 110, 300), (200, 100, 0)],                 # curve runs past the ends
    ])
    def test_bad_input_raises(self, pvis):
        with pytest.raises(CivilInputError):
            VerticalAlignment(pvis)

    def test_stations_include_key_points(self):
        s = stations_for(100, 30, [45.5])
        assert s == [0, 30, 45.5, 60, 90, 100]


# ----------------------------------------------------------------- earthworks
def _flat_site(z=100.0):
    return Terrain([(x, y, z) for x in range(-100, 401, 25) for y in range(-100, 101, 25)])


class TestEarthworks:
    H = HorizontalAlignment([(0, 0), (300, 0)])

    def _tpl(self, **kw):
        base = dict(crossfall_pct=0, shoulder_crossfall_pct=0, pavement_thickness_m=0.0)
        base.update(kw)
        return ew.RoadTemplate(**base)

    def test_fill_volume_matches_hand_calculation(self):
        # 10 m wide formation, 1 m of fill, 2H:1V slopes: area = (10 + 14)/2 x 1 = 12 m2.
        v = VerticalAlignment([(0, 101, 0), (300, 101, 0)])
        q = ew.compute_earthworks(_flat_site(), self.H, v, self._tpl(), station_interval_m=50)["quantities"]
        assert q["fill_m3"] == pytest.approx(12 * 300, rel=1e-6) and q["cut_m3"] == pytest.approx(0, abs=1e-6)

    def test_cut_volume_matches_hand_calculation(self):
        # 1 m of cut with 1.5H:1V slopes: area = (10 + 13)/2 x 1 = 11.5 m2.
        v = VerticalAlignment([(0, 99, 0), (300, 99, 0)])
        q = ew.compute_earthworks(_flat_site(), self.H, v, self._tpl(cut_slope=1.5), station_interval_m=50)["quantities"]
        assert q["cut_m3"] == pytest.approx(11.5 * 300, rel=1e-6) and q["fill_m3"] == pytest.approx(0, abs=1e-6)

    def test_ground_on_grade_gives_no_earthwork(self):
        v = VerticalAlignment([(0, 100, 0), (300, 100, 0)])
        q = ew.compute_earthworks(_flat_site(), self.H, v, self._tpl(), station_interval_m=50)["quantities"]
        assert q["cut_m3"] == pytest.approx(0, abs=1e-6) and q["fill_m3"] == pytest.approx(0, abs=1e-6)

    def test_fill_factor_and_net(self):
        v = VerticalAlignment([(0, 101, 0), (300, 101, 0)])
        q = ew.compute_earthworks(_flat_site(), self.H, v, self._tpl(), station_interval_m=50, fill_factor=1.25)["quantities"]
        assert q["fill_m3_with_factor"] == pytest.approx(q["fill_m3"] * 1.25)
        assert q["net_m3"] == pytest.approx(-q["fill_m3"] * 1.25)

    def test_pavement_quantities(self):
        v = VerticalAlignment([(0, 100.5, 0), (300, 100.5, 0)])
        tpl = ew.RoadTemplate(pavement_thickness_m=0.3, has_kerbs=True,
                              pavement_layers=(("Asphalt", 0.1), ("Base", 0.2)))
        q = ew.compute_earthworks(_flat_site(), self.H, v, tpl, station_interval_m=100)["quantities"]
        assert q["carriageway_area_m2"] == pytest.approx(7 * 300)
        assert q["kerb_length_m"] == pytest.approx(600)
        vols = {l["name"]: l["volume_m3"] for l in q["pavement_layers"]}
        assert vols["Asphalt"] == pytest.approx(0.1 * 10 * 300) and vols["Base"] == pytest.approx(0.2 * 10 * 300)

    def test_stations_outside_the_survey_are_reported(self):
        h = HorizontalAlignment([(0, 0), (600, 0)])
        v = VerticalAlignment([(0, 101, 0), (600, 101, 0)])
        r = ew.compute_earthworks(_flat_site(), h, v, self._tpl(), station_interval_m=100)
        assert any("outside the surveyed surface" in w for w in r["warnings"])
        assert r["stations"][-1]["status"] == "outside_survey"

    def test_layers_must_add_up(self):
        with pytest.raises(CivilInputError):
            ew.RoadTemplate(pavement_thickness_m=0.3, pavement_layers=(("A", 0.1),)).validate()

    def test_vertical_alignment_must_cover_the_road(self):
        v = VerticalAlignment([(0, 100, 0), (100, 100, 0)])
        with pytest.raises(CivilInputError):
            ew.compute_earthworks(_flat_site(), self.H, v, self._tpl())

    def test_sloping_ground_gives_cut_and_fill_and_a_mass_diagram(self):
        t = Terrain([(x, y, 100 + 0.02 * x) for x in range(-100, 401, 25) for y in range(-100, 101, 25)])
        v = VerticalAlignment([(0, 100, 0), (300, 106, 0)])
        r = ew.compute_earthworks(t, self.H, v, self._tpl(), station_interval_m=25)
        assert r["quantities"]["cut_m3"] > 0 or r["quantities"]["fill_m3"] > 0
        ords = [s["mass_ordinate_m3"] for s in r["stations"]]
        assert len(ords) == len(r["stations"]) and ords[0] == 0


# ------------------------------------------------------------------ hydraulics
class TestHydrology:
    def test_rational_method(self):
        assert hy.rational_flow(0.7, 100, 5) == pytest.approx(0.7 * 100 * 5 / 360)

    def test_kirpich(self):
        assert hy.kirpich_tc_min(1000, 0.01) == pytest.approx(0.0195 * 1000 ** 0.77 * 0.01 ** -0.385)

    def test_idf(self):
        assert hy.idf_intensity_mm_h(1000, 10, 0.8, 20) == pytest.approx(1000 / 30 ** 0.8)

    @pytest.mark.parametrize("fn,args", [
        (hy.rational_flow, (1.5, 100, 5)), (hy.rational_flow, (0.5, -1, 5)), (hy.kirpich_tc_min, (0, 0.01)),
    ])
    def test_bad_inputs_raise(self, fn, args):
        with pytest.raises(CivilInputError):
            fn(*args)


class TestPipes:
    def test_full_capacity_formula(self):
        r = hy.pipe_flow(1.0, 0.005, 0.013)
        assert r["q_full_m3s"] == pytest.approx((1 / 0.013) * math.pi / 4 * 0.25 ** (2 / 3) * 0.005 ** 0.5)

    def test_half_full_pipe_carries_half_the_full_flow(self):
        # Exact property of a circular section: at half depth, A and R are half and full ratios.
        full = hy.pipe_flow(0.9, 0.004, 0.013)["q_full_m3s"]
        r = hy.pipe_flow(0.9, 0.004, 0.013, full / 2)
        assert r["fill_ratio"] == pytest.approx(0.5, abs=1e-6)

    def test_peak_free_surface_flow_is_about_1_076_times_full(self):
        r = hy.pipe_flow(1.0, 0.005, 0.013)
        assert r["q_max_free_surface_m3s"] / r["q_full_m3s"] == pytest.approx(1.0757, abs=2e-3)

    def test_round_trip_depth_to_flow(self):
        r = hy.pipe_flow(0.6, 0.01, 0.013, 0.15)
        assert hy._q_at(0.6, r["depth_m"], 0.01, 0.013) == pytest.approx(0.15, rel=1e-6)

    def test_too_much_flow_is_reported_not_solved(self):
        assert hy.pipe_flow(0.3, 0.001, 0.013, 5)["status"] == "exceeds_capacity"

    def test_selection_picks_smallest_that_fits(self):
        r = hy.select_pipe(0.5, 0.005, 0.013, 0.8)
        assert r["selected_diameter_mm"] == 750
        smaller = hy.pipe_flow(0.6, 0.005, 0.013, 0.5)
        assert smaller["status"] != "ok" or smaller["fill_ratio"] > 0.8


class TestChannels:
    def test_round_trip_and_regime(self):
        r = hy.trapezoid_normal_depth(5.0, 3.0, 2.0, 0.001, 0.025)
        y = r["normal_depth_m"]
        a = (3 + 2 * y) * y
        p = 3 + 2 * y * math.sqrt(5)
        assert hy.manning_q(a, a / p, 0.001, 0.025) == pytest.approx(5.0, rel=1e-6)
        assert r["regime"] == "subcritical" and r["critical_depth_m"] < y

    def test_critical_depth_satisfies_the_critical_flow_condition(self):
        r = hy.trapezoid_normal_depth(5.0, 3.0, 2.0, 0.001, 0.025)
        yc = r["critical_depth_m"]
        a, t = (3 + 2 * yc) * yc, 3 + 4 * yc
        assert a ** 3 / t == pytest.approx(25.0 / hy.G, rel=1e-6)

    def test_steep_channel_is_supercritical(self):
        assert hy.trapezoid_normal_depth(5.0, 3.0, 2.0, 0.05, 0.015)["regime"] == "supercritical"


class TestCulvert:
    def test_headwater_rises_with_flow(self):
        last = 0
        for q in (0.5, 1, 2, 3, 4, 6, 8):
            hw = hy.box_culvert_check(q, 2, 1.5, 10, 0.01)["inlet"]["headwater_m"]
            assert hw > last
            last = hw

    def test_inlet_curve_is_continuous_at_the_transition(self):
        D, B = 1.2, 1.5
        q_t = 1.5 * B * (1.2 * D) ** 1.5
        below = hy.box_culvert_check(q_t * 0.999, B, D, 10, 0.01)["inlet"]["headwater_m"]
        above = hy.box_culvert_check(q_t * 1.001, B, D, 10, 0.01)["inlet"]["headwater_m"]
        assert above - below < 0.01

    def test_small_flow_is_unsubmerged_large_flow_submerged(self):
        assert hy.box_culvert_check(2.0, 1.5, 1.2, 12, 0.01)["inlet"]["regime"].startswith("unsubmerged")
        assert hy.box_culvert_check(8.0, 1.5, 1.2, 12, 0.01, barrels=2)["inlet"]["regime"].startswith("submerged")

    def test_barrel_over_capacity_is_flagged_and_barrels_share_flow(self):
        r = hy.box_culvert_check(40.0, 1.0, 1.0, 10, 0.001)
        assert r["barrel"]["status"] == "exceeds_barrel_capacity"
        two = hy.box_culvert_check(6.0, 1.5, 1.2, 12, 0.01, barrels=2)
        assert two["inputs"]["flow_per_barrel_m3s"] == pytest.approx(3.0)


# ----------------------------------------------------------- loads and walls
class TestLoads:
    def test_rankine_coefficients(self):
        assert loads.rankine_ka(30) == pytest.approx(1 / 3)
        assert loads.rankine_kp(30) == pytest.approx(3.0)
        assert loads.rankine_ka(0) == pytest.approx(1.0)

    def test_polygon_centroid(self):
        a, cx, cy = loads.polygon_area_centroid([(0, 0), (4, 0), (4, 2), (0, 2)])
        assert (a, cx, cy) == (8, 2, 1)
        a, cx, cy = loads.polygon_area_centroid([(0, 0), (3, 0), (0, 3)])
        assert (a, cx, cy) == pytest.approx((4.5, 1, 1))

    def test_culvert_loads(self):
        c = loads.culvert_loads(1.5, 1.2, 0.2, 0.25, 1, 1.0, 18, 30, 10)
        assert c["roof_pressure_kpa"] == pytest.approx(28.0)
        concrete_area = 1.9 * 1.7 - 1.5 * 1.2
        assert c["self_weight_kn_per_m"] == pytest.approx(concrete_area * 24)
        assert c["lateral_pressure_at_roof_level_kpa"] == pytest.approx(28.0 / 3)
        assert c["lateral_pressure_at_floor_level_kpa"] == pytest.approx((28.0 + 18 * 1.7) / 3)


WALL = dict(stem_height_m=5.4, stem_thickness_base_m=0.5, stem_thickness_top_m=0.3,
            base_width_m=3.6, base_thickness_m=0.6, toe_length_m=0.8)


class TestWall:
    def test_active_thrust_matches_hand_value(self):
        r = wall_stability.check_wall(**WALL)
        assert r["earth_pressure"]["active_thrust_soil_kn_per_m"] == pytest.approx(0.5 * (1 / 3) * 18 * 6.0 ** 2)

    def test_weights_match_areas(self):
        r = wall_stability.check_wall(**WALL)
        by = {l["name"]: l["force_kn_per_m"] for l in r["loads"]}
        assert by["Base slab"] == pytest.approx(3.6 * 0.6 * 24)
        assert by["Stem"] == pytest.approx((0.5 + 0.3) / 2 * 5.4 * 24)

    def test_base_pressure_resultant_balances_the_vertical_load(self):
        r = wall_stability.check_wall(**WALL)
        assert r["contact"] == "full"
        force = (r["q_max_kpa"] + r["q_min_kpa"]) / 2 * 3.6
        assert force == pytest.approx(r["sum_vertical_kn_per_m"])
        x_from_toe = 3.6 * (2 * r["q_min_kpa"] + r["q_max_kpa"]) / (3 * (r["q_max_kpa"] + r["q_min_kpa"]))
        assert x_from_toe == pytest.approx(r["resultant_from_toe_m"])

    def test_surcharge_lowers_both_factors_of_safety(self):
        a = wall_stability.check_wall(**WALL)
        b = wall_stability.check_wall(**WALL, surcharge_kpa=10)
        assert b["factor_of_safety_overturning"] < a["factor_of_safety_overturning"]
        assert b["factor_of_safety_sliding"] < a["factor_of_safety_sliding"]

    def test_passive_resistance_only_helps_when_switched_on(self):
        a = wall_stability.check_wall(**WALL)
        b = wall_stability.check_wall(**WALL, include_passive=True, embedment_m=1.0)
        assert b["factor_of_safety_sliding"] > a["factor_of_safety_sliding"]
        c = wall_stability.check_wall(**WALL, embedment_m=1.0)
        assert c["factor_of_safety_sliding"] == pytest.approx(a["factor_of_safety_sliding"])

    def test_short_heel_gives_partial_contact(self):
        r = wall_stability.check_wall(stem_height_m=6, stem_thickness_base_m=0.6, stem_thickness_top_m=0.4,
                                      base_width_m=1.6, base_thickness_m=0.5, toe_length_m=0.3, surcharge_kpa=20)
        assert r["contact"] == "partial" and r["q_min_kpa"] == 0 and not r["resultant_within_middle_third"]

    @pytest.mark.parametrize("bad", [
        dict(stem_thickness_top_m=0.9), dict(base_width_m=1.2), dict(stem_height_m=-1),
    ])
    def test_bad_geometry_raises(self, bad):
        with pytest.raises(CivilInputError):
            wall_stability.check_wall(**{**WALL, **bad})


# -------------------------------------------------------------------- criteria
class TestCriteria:
    def test_speed_based_minimum_radius(self):
        assert cr.min_radius_from_speed(cr.generic_starter()) == pytest.approx(60 ** 2 / (127 * 0.21))

    def test_road_checks_pass_and_fail(self):
        h = HorizontalAlignment([(0, 0), (100, 0), (100, 100)], [0, 80, 0])
        v = VerticalAlignment([(0, 100, 0), (500, 110, 100), (1000, 100, 0)])
        f = {x["check"]: x["status"] for x in cr.check_road(cr.generic_starter(), lane_width_m=3.5, crossfall_pct=2.5,
                                                              horizontal=h, vertical=v)}
        assert f["maximum grade"] == "pass" and f["curve radius at PI 2"] == "fail"
        f2 = {x["check"]: x["status"] for x in cr.check_road(cr.generic_starter(), lane_width_m=5.0, crossfall_pct=0.5)}
        assert f2["lane width maximum"] == "fail" and f2["crossfall minimum"] == "fail"

    def test_wall_checks_follow_the_numbers(self):
        r = wall_stability.check_wall(**WALL)
        status = {x["check"]: x["status"] for x in cr.check_wall(cr.generic_starter(), r)}
        assert status["overturning"] == "pass" and status["sliding"] == "fail"   # FS 1.14 < 1.5
        relaxed = cr.criteria_from_dict({"wall_min_fs_sliding": 1.0})
        assert {x["check"]: x["status"] for x in cr.check_wall(relaxed, r)}["sliding"] == "pass"

    def test_overrides_are_validated(self):
        assert cr.criteria_from_dict({"max_grade_pct": 6}).max_grade_pct == 6
        assert cr.criteria_from_dict({"max_grade_pct": None}).max_grade_pct is None
        for bad in ({"nope": 1}, {"max_grade_pct": "x"}, {"wall_require_middle_third": 1}, {"max_grade_pct": float("nan")}):
            with pytest.raises(CivilInputError):
                cr.criteria_from_dict(bad)

    def test_summary_never_claims_compliance(self):
        s = cr.summarize([], cr.generic_starter())
        assert "not a statement of compliance" in s["statement"]
        assert "not a design standard" in s["criteria_name"]
