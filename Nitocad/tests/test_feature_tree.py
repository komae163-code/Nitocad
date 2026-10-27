"""Feature-tree DSL: schema, bounds, geometry pre-checks, airfoil generator. Pure Python."""
import math

import pytest

from assembly_planner import default_catalog, plan_assembly, planner_system_prompt, validate_spec
from feature_tree import (
    MAX_FEATURES, MAX_PATTERN, airfoil_points, profile_bbox, validate_feature_tree,
)


def rect(w=100, h=60, **kw):
    return {"type": "rect", "width": w, "height": h, **kw}


def plate(**over):
    f = {"id": "plate", "op": "extrude", "plane": "XY", "profile": rect(), "distance": 8}
    f.update(over)
    return f


def hole(**over):
    f = {"id": "hole", "op": "extrude", "plane": "XY", "mode": "cut",
         "profile": {"type": "circle", "diameter": 6, "center": [-40, -20]}, "distance": 8}
    f.update(over)
    return f


def check(features):
    return validate_feature_tree({"features": features})


class TestBasics:
    def test_valid_plate_with_pattern_normalizes(self):
        feats, errors = check([plate(), hole(), {"id": "holes", "op": "pattern", "feature": "hole",
                                                 "kind": "linear", "count": 2, "spacing": [80, 0, 0]}])
        assert errors == []
        assert feats[0]["mode"] == "new" and feats[1]["mode"] == "cut"
        assert feats[0]["offset"] == 0.0 and feats[0]["symmetric"] is False and feats[0]["reverse"] is False

    def test_idempotent_normalization(self):
        feats, errors = check([plate(profile=rect(holes=[{"type": "circle", "diameter": 10}])), hole()])
        assert errors == []
        again, errors2 = validate_feature_tree({"features": feats})
        assert errors2 == [] and again == feats

    @pytest.mark.parametrize("spec", [None, {}, {"features": []}, {"features": "x"}, []])
    def test_rejects_empty(self, spec):
        assert validate_feature_tree(spec)[1]

    def test_accepts_bare_list(self):
        assert validate_feature_tree([plate()])[1] == []

    def test_first_feature_rules(self):
        assert any("first feature" in e for e in check([plate(mode="add")])[1])
        assert any("only the first" in e for e in check([plate(), plate(id="b", mode="new")])[1])
        assert check([{"id": "p", "op": "pattern", "feature": "x", "kind": "linear", "count": 2, "spacing": [1, 0, 0]}])[1]

    def test_ids_snake_case_and_unique(self):
        assert any("snake_case" in e for e in check([plate(id="Bad Id")])[1])
        assert any("used twice" in e for e in check([plate(), plate()])[1])

    def test_unknown_keys_and_ops_rejected(self):
        assert any("unknown key" in e for e in check([plate(depth=5)])[1])
        assert any("not supported" in e for e in check([plate(), {"id": "f", "op": "fillet"}])[1])
        assert any("unknown profile key" in e for e in check([plate(profile=rect(radius=2))])[1])

    @pytest.mark.parametrize("bad", [0, -1, "8", None, float("nan"), float("inf"), 99999, True])
    def test_bad_distance(self, bad):
        assert check([plate(distance=bad)])[1]

    def test_limits(self):
        many = [plate()] + [plate(id=f"f{i}", mode="add") for i in range(MAX_FEATURES)]
        assert any("Too many features" in e for e in check(many)[1])
        big = {"id": "p", "op": "pattern", "feature": "hole", "kind": "linear", "count": MAX_PATTERN + 1, "spacing": [1, 0, 0]}
        assert check([plate(), hole(), big])[1]

    def test_reverse_and_symmetric_exclusive(self):
        assert any("cannot both" in e for e in check([plate(symmetric=True, reverse=True)])[1])


class TestProfiles:
    def test_polygon_checks(self):
        ok = {"type": "polygon", "points": [[0, 0], [50, 0], [50, 30], [0, 30]]}
        assert check([plate(profile=ok)])[1] == []
        bow = {"type": "polygon", "points": [[0, 0], [50, 50], [50, 0], [0, 50]]}
        assert any("self-intersecting" in e for e in check([plate(profile=bow)])[1])
        flat = {"type": "polygon", "points": [[0, 0], [10, 0], [20, 0]]}
        assert any("zero area" in e for e in check([plate(profile=flat)])[1])
        dup = {"type": "polygon", "points": [[0, 0], [0, 0], [10, 0], [10, 10]]}
        assert any("duplicate" in e for e in check([plate(profile=dup)])[1])
        assert check([plate(profile={"type": "polygon", "points": [[0, 0], [1, 1]]})])[1]

    def test_holes_must_be_strictly_inside(self):
        inside = rect(holes=[{"type": "circle", "diameter": 10}])
        assert check([plate(profile=inside)])[1] == []
        outside = rect(holes=[{"type": "circle", "diameter": 10, "center": [48, 0]}])
        assert any("strictly inside" in e for e in check([plate(profile=outside)])[1])
        nested = rect(holes=[{"type": "circle", "diameter": 10, "holes": [{"type": "circle", "diameter": 2}]}])
        assert check([plate(profile=nested)])[1]

    def test_slot_and_regular_polygon(self):
        assert check([plate(profile={"type": "slot", "length": 40, "width": 10, "angle": 30})])[1] == []
        assert check([plate(profile={"type": "slot", "length": 10, "width": 10})])[1]
        assert check([plate(profile={"type": "regular_polygon", "sides": 6, "diameter": 20})])[1] == []
        assert check([plate(profile={"type": "regular_polygon", "sides": 2, "diameter": 20})])[1]

    def test_airfoil_validation(self):
        assert check([plate(profile={"type": "airfoil", "naca": "2412", "chord": 1000})])[1] == []
        assert check([plate(profile={"type": "airfoil", "naca": "24", "chord": 1000})])[1]
        assert check([plate(profile={"type": "airfoil", "naca": "2400", "chord": 1000})])[1]


class TestOperations:
    def test_revolve_must_stay_on_one_side_of_the_axis(self):
        good = {"id": "r", "op": "revolve", "plane": "XY", "axis": "y",
                "profile": {"type": "rect", "width": 10, "height": 40, "center": [10, 20]}}
        assert check([good])[1] == []
        bad = dict(good, profile={"type": "rect", "width": 10, "height": 40, "center": [3, 20]})
        assert any("crosses the revolve axis" in e for e in check([bad])[1])
        assert check([dict(good, angle=0)])[1] and check([dict(good, axis="z")])[1]

    def test_loft_rules(self):
        def sec(off, w):
            return {"plane": "XY", "offset": off, "profile": rect(w, w)}
        ok = {"id": "l", "op": "loft", "sections": [sec(0, 50), sec(30, 20)]}
        assert check([ok])[1] == []
        assert check([dict(ok, sections=[sec(0, 50)])])[1]
        assert any("strictly increasing" in e for e in check([dict(ok, sections=[sec(10, 50), sec(10, 20)])])[1])
        mixed = [sec(0, 50), {"plane": "XY", "offset": 30, "profile": {"type": "circle", "diameter": 20}}]
        assert any("same profile type" in e for e in check([dict(ok, sections=mixed)])[1])
        planes = [sec(0, 50), dict(sec(30, 20), plane="XZ")]
        assert any("same plane" in e for e in check([dict(ok, sections=planes)])[1])
        holey = [dict(sec(0, 50), profile=rect(50, 50, holes=[{"type": "circle", "diameter": 5}])), sec(30, 20)]
        assert check([dict(ok, sections=holey)])[1]

    def test_pattern_and_mirror_references(self):
        base = [plate(), hole()]
        lin = {"id": "p", "op": "pattern", "feature": "hole", "kind": "linear", "count": 3, "spacing": [10, 0, 0]}
        assert check(base + [lin])[1] == []
        assert check(base + [dict(lin, feature="nope")])[1]
        assert any("first (\"new\")" in e for e in check(base + [dict(lin, feature="plate")])[1])
        assert check(base + [dict(lin, spacing=[0, 0, 0])])[1]
        circ = {"id": "c", "op": "pattern", "feature": "hole", "kind": "circular", "count": 4, "axis": "Z"}
        assert check(base + [circ])[1] == []
        assert check(base + [dict(circ, axis="Q")])[1]
        mir = {"id": "m", "op": "mirror", "feature": "hole", "plane": "YZ"}
        assert check(base + [mir])[1] == []
        assert check(base + [dict(mir, plane="AB")])[1]
        # cannot reference a pattern/mirror (only base ops)
        assert check(base + [lin, {"id": "pp", "op": "pattern", "feature": "p", "kind": "linear",
                                   "count": 2, "spacing": [1, 0, 0]}])[1]


class TestAirfoil:
    def test_naca_0012_is_symmetric_with_12_percent_thickness(self):
        pts = airfoil_points("0012", 1000.0)
        assert len(pts) == 80
        ys = [p[1] for p in pts]
        assert max(ys) == pytest.approx(-min(ys), rel=1e-9)
        assert (max(ys) - min(ys)) / 1000.0 == pytest.approx(0.12, abs=0.002)

    def test_naca_2412_has_camber_and_correct_extent(self):
        pts = airfoil_points("2412", 1000.0)
        xs = [p[0] for p in pts]
        assert min(xs) == pytest.approx(0.0, abs=1e-6) and max(xs) == pytest.approx(1000.0, abs=1.0)
        upper = max(p[1] for p in pts)
        lower = -min(p[1] for p in pts)
        assert upper > lower  # positive camber
        assert (upper + lower) / 1000.0 == pytest.approx(0.12, abs=0.01)

    def test_outline_is_closed_and_non_self_intersecting(self):
        from feature_tree import _polygon_problems
        pts = [list(p) for p in airfoil_points("4415", 500.0)]
        assert _polygon_problems(pts) is None
        assert math.dist(pts[0], pts[-1]) < 20  # leading edge neighbors, no duplicate point

    def test_bbox_of_airfoil_profile(self):
        prof = validate_feature_tree([plate(profile={"type": "airfoil", "naca": "0012", "chord": 100,
                                                      "center": [10, 5]})])[0][0]["profile"]
        x0, y0, x1, y1 = profile_bbox(prof)
        assert x0 == pytest.approx(10, abs=1e-6) and x1 == pytest.approx(110, abs=0.1)


class TestPlannerIntegration:
    def test_catalog_and_prompt(self):
        assert "feature_tree" in default_catalog()
        assert "airfoil" in planner_system_prompt()

    def test_planner_accepts_a_valid_feature_tree_part(self):
        part = {"name": "wall", "part_type": "feature_tree", "position": [0, 0, 0],
                "parameters": {"features": [plate(id="wall", profile=rect(4000, 2600, holes=[{"type": "rect", "width": 1200, "height": 1000}]),
                                                  distance=200)]}}
        parts, errors = validate_spec({"parts": [part]})
        assert errors == [] and parts[0]["parameters"]["features"][0]["op"] == "extrude"

    def test_planner_reports_feature_errors_with_context(self):
        part = {"name": "wall", "part_type": "feature_tree",
                "parameters": {"features": [plate(distance=-1)]}}
        _, errors = validate_spec({"parts": [part]})
        assert any("part 'wall'" in e and "distance" in e for e in errors)
        part["parameters"] = {"features": [plate()], "extra": 1}
        assert validate_spec({"parts": [part]})[1]

    def test_retry_loop_feeds_back_feature_errors(self):
        seen = []

        def llm(system, user):
            seen.append(user)
            bad = [plate(distance=-1)] if len(seen) == 1 else [plate()]
            return {"parts": [{"name": "p", "part_type": "feature_tree", "parameters": {"features": bad}}]}

        assert plan_assembly("a plate", call_llm=llm) and "distance" in seen[1]


class TestSweepPaths:
    PIPE = {"id": "pipe", "op": "sweep",
            "profile": {"type": "circle", "diameter": 50, "holes": [{"type": "circle", "diameter": 44}]},
            "path": [[0, 0, 0], [0, 0, 300], [200, 0, 300], [200, 0, 600]], "bend_radius": 100}

    def test_two_quarter_bends_have_exact_geometry(self):
        from feature_tree import expand_path, path_length
        segs, errs = expand_path(self.PIPE["path"], 100)
        assert errs == [] and [s[0] for s in segs] == ["line", "arc", "arc", "line"]
        a, mid, b = segs[1][1], segs[1][2], segs[1][3]
        assert a == pytest.approx((0, 0, 200)) and b == pytest.approx((100, 0, 300))
        assert mid == pytest.approx((100 - 100 * math.sqrt(0.5), 0, 200 + 100 * math.sqrt(0.5)))
        assert path_length(segs) == pytest.approx(400 + math.pi * 100)  # 2 lines of 200 + two quarter arcs of r=100

    def test_sharp_corners_and_collinear_points(self):
        from feature_tree import expand_path
        segs, errs = expand_path([[0, 0, 0], [0, 0, 100], [100, 0, 100]], 0)
        assert errs == [] and [s[0] for s in segs] == ["line", "line"]
        segs, errs = expand_path([[0, 0, 0], [0, 0, 50], [0, 0, 100]], 30)
        assert errs == [] and [s[0] for s in segs] == ["line", "line"]  # collinear: no arc

    def test_bad_paths(self):
        from feature_tree import expand_path
        assert any("hairpin" in e for e in expand_path([[0, 0, 0], [0, 0, 100], [0, 0.1, 0]], 0)[1])
        assert any("duplicates" in e for e in expand_path([[0, 0, 0], [0, 0, 0], [0, 0, 5]], 0)[1])
        # two 90-degree bends of r=100 need 100 + 100 mm of the middle leg: 200 fits exactly, 150 does not
        assert expand_path([[0, 0, 0], [0, 0, 300], [200, 0, 300], [200, 0, 600]], 100)[1] == []
        err = expand_path([[0, 0, 0], [0, 0, 300], [150, 0, 300], [150, 0, 600]], 100)[1]
        assert any("does not fit" in e for e in err)

    def test_profile_frame_orientation(self):
        from feature_tree import profile_frame
        x0, y0 = profile_frame((0, 0, 1))  # same frame as the XY plane
        assert x0 == pytest.approx((1, 0, 0)) and y0 == pytest.approx((0, 1, 0))
        x, y = profile_frame((1, 0, 0))
        assert x == pytest.approx((0, 0, 1)) and y == pytest.approx((0, -1, 0))

    def test_segment_distance(self):
        from feature_tree import _seg_seg_dist
        assert _seg_seg_dist((0, 0, 0), (10, 0, 0), (0, 5, 0), (10, 5, 0)) == pytest.approx(5)
        assert _seg_seg_dist((0, 0, 0), (10, 0, 0), (5, -3, 4), (5, 3, 4)) == pytest.approx(4)  # skew lines
        assert _seg_seg_dist((0, 0, 0), (1, 0, 0), (4, 0, 0), (9, 0, 0)) == pytest.approx(3)

    def test_valid_pipe_normalizes_idempotently(self):
        feats, errors = check([self.PIPE])
        assert errors == [] and feats[0]["bend_radius"] == 100.0 and feats[0]["mode"] == "new"
        again, errors2 = validate_feature_tree({"features": feats})
        assert errors2 == [] and again == feats

    def test_bend_radius_must_exceed_profile_radius(self):
        assert any("must exceed the profile" in e for e in check([dict(self.PIPE, bend_radius=25)])[1])
        assert any("must exceed the profile" in e for e in check([dict(self.PIPE, bend_radius=10)])[1])

    def test_radius_that_does_not_fit_is_reported_by_validation(self):
        short = dict(self.PIPE, path=[[0, 0, 0], [0, 0, 300], [150, 0, 300], [150, 0, 600]])
        assert any("does not fit" in e for e in check([short])[1])

    def test_path_that_folds_back_close_to_itself_is_rejected(self):
        u = [[0, 0, 0], [0, 0, 100], [30, 0, 100], [30, 0, 0]]
        fat = dict(self.PIPE, path=u, bend_radius=0, profile={"type": "circle", "diameter": 50})
        assert any("intersect itself" in e for e in check([fat])[1])
        slim = dict(self.PIPE, path=u, bend_radius=0, profile={"type": "circle", "diameter": 20})
        assert check([slim])[1] == []

    @pytest.mark.parametrize("bad", [None, [], [[0, 0, 0]], [[0, 0]], [[0, 0, 0], [0, 0, "x"]], [[0, 0, 0]] * 101,
                                     [[0, 0, 0], [0, 0, float("inf")]]])
    def test_bad_path_values(self, bad):
        assert check([dict(self.PIPE, path=bad)])[1]

    def test_unknown_key_and_negative_radius(self):
        assert check([dict(self.PIPE, plane="XY")])[1]
        assert check([dict(self.PIPE, bend_radius=-5)])[1]

    def test_sweep_can_be_patterned_and_cut(self):
        base = {"id": "block", "op": "extrude", "plane": "XY", "profile": rect(100, 100), "distance": 20}
        groove = {"id": "groove", "op": "sweep", "mode": "cut", "profile": {"type": "circle", "diameter": 6},
                  "path": [[-40, -40, 10], [-40, 40, 10]]}
        rep = {"id": "grooves", "op": "pattern", "feature": "groove", "kind": "linear", "count": 3, "spacing": [30, 0, 0]}
        assert check([base, groove, rep])[1] == []

    def test_planner_accepts_a_sweep_part(self):
        part = {"name": "pipe", "part_type": "feature_tree", "parameters": {"features": [self.PIPE]}}
        parts, errors = validate_spec({"parts": [part]})
        assert errors == [] and parts[0]["parameters"]["features"][0]["op"] == "sweep"
        assert "sweep" in planner_system_prompt()
