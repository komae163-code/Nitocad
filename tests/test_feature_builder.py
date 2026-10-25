"""Feature-tree geometry against closed-form volumes. Needs CadQuery (skipped where it is absent)."""
import math

import pytest

cq = pytest.importorskip("cadquery")

from cad_templates import TEMPLATES  # noqa: E402
from feature_builder import FeatureTreeError, build_feature_tree  # noqa: E402


def rect(w, h, **kw):
    return {"type": "rect", "width": w, "height": h, **kw}


def plate(**over):
    f = {"id": "plate", "op": "extrude", "plane": "XY", "profile": rect(100, 60), "distance": 8}
    f.update(over)
    return f


def hole(cx=-40, cy=-20, **over):
    f = {"id": "hole", "op": "extrude", "plane": "XY", "mode": "cut",
         "profile": {"type": "circle", "diameter": 6, "center": [cx, cy]}, "distance": 8}
    f.update(over)
    return f


def vol(body):
    return body.val().Volume()


HOLE = math.pi * 3 ** 2 * 8


def test_registered_as_template():
    assert "feature_tree" in TEMPLATES


def test_plate_with_through_hole():
    body = build_feature_tree([plate(), hole()])
    assert vol(body) == pytest.approx(100 * 60 * 8 - HOLE, rel=1e-4)


def test_linear_pattern_cuts_every_instance():
    body = build_feature_tree([plate(), hole(), {"id": "p", "op": "pattern", "feature": "hole",
                                                 "kind": "linear", "count": 3, "spacing": [40, 0, 0]}])
    assert vol(body) == pytest.approx(100 * 60 * 8 - 3 * HOLE, rel=1e-4)


def test_circular_pattern_bolt_circle():
    flange = {"id": "flange", "op": "extrude", "plane": "XY", "profile": {"type": "circle", "diameter": 120}, "distance": 10}
    bolt = {"id": "bolt", "op": "extrude", "plane": "XY", "mode": "cut", "distance": 10,
            "profile": {"type": "circle", "diameter": 8, "center": [40, 0]}}
    ring = {"id": "ring", "op": "pattern", "feature": "bolt", "kind": "circular", "count": 6, "axis": "Z"}
    body = build_feature_tree([flange, bolt, ring])
    assert vol(body) == pytest.approx(math.pi * 60 ** 2 * 10 - 6 * math.pi * 4 ** 2 * 10, rel=1e-4)


def test_revolve_tube_volume():
    tube = {"id": "tube", "op": "revolve", "plane": "XY", "axis": "y",
            "profile": rect(10, 40, center=[10, 20])}  # x 5..15, y 0..40
    body = build_feature_tree([tube])
    assert vol(body) == pytest.approx(math.pi * (15 ** 2 - 5 ** 2) * 40, rel=1e-4)


def test_ruled_loft_matches_frustum_formula():
    loft = {"id": "frustum", "op": "loft", "ruled": True,
            "sections": [{"plane": "XY", "offset": 0, "profile": rect(50, 50)},
                         {"plane": "XY", "offset": 30, "profile": rect(20, 20)}]}
    assert vol(build_feature_tree([loft])) == pytest.approx(30 / 3 * (2500 + 400 + 1000), rel=1e-4)


def test_wall_with_window_opening():
    wall = {"id": "wall", "op": "extrude", "plane": "XY", "distance": 200,
            "profile": rect(4000, 2600, center=[2000, 1300], holes=[rect(1200, 1000, center=[2000, 1500])])}
    assert vol(build_feature_tree([wall])) == pytest.approx(4000 * 2600 * 200 - 1200 * 1000 * 200, rel=1e-4)


def test_airfoil_wing_is_one_valid_solid_with_the_right_span():
    wing = {"id": "wing", "op": "loft", "sections": [
        {"plane": "XZ", "offset": 0, "profile": {"type": "airfoil", "naca": "2412", "chord": 1600}},
        {"plane": "XZ", "offset": 5000, "profile": {"type": "airfoil", "naca": "2412", "chord": 900, "center": [350, 0]}}]}
    body = build_feature_tree([wing])
    bb = body.val().BoundingBox()
    assert len(body.solids().vals()) == 1 and body.val().isValid()
    assert bb.ylen == pytest.approx(5000, abs=1.0)          # XZ sketches extrude toward -Y
    assert bb.xlen == pytest.approx(1600, abs=2.0)
    # volume between the two root/tip prisms (area ~ 0.0823 * chord^2 for a 12% airfoil)
    assert 0.0823 * 900 ** 2 * 5000 < vol(body) < 0.0823 * 1600 ** 2 * 5000


def test_mirror_and_symmetric_and_reverse():
    base = plate(profile=rect(40, 40, center=[30, 0]))
    bump = {"id": "bump", "op": "extrude", "plane": "XY", "offset": 8, "distance": 4,
            "profile": {"type": "circle", "diameter": 10, "center": [30, 0]}}
    mirror = {"id": "m", "op": "mirror", "feature": "bump", "plane": "YZ"}
    # mirrored bump at x=-30 floats beside the plate (disconnected) -> must be reported
    with pytest.raises(FeatureTreeError) as e:
        build_feature_tree([base, bump, mirror])
    assert "disconnected" in str(e.value)
    sym = build_feature_tree([plate(symmetric=True, distance=10)]).val().BoundingBox()
    assert sym.zmin == pytest.approx(-5, abs=1e-6) and sym.zmax == pytest.approx(5, abs=1e-6)
    top_cut = hole(offset=8, reverse=True)
    assert vol(build_feature_tree([plate(), top_cut])) == pytest.approx(100 * 60 * 8 - HOLE, rel=1e-4)


def test_cut_that_misses_the_body_is_an_error_naming_the_feature():
    with pytest.raises(FeatureTreeError) as e:
        build_feature_tree([plate(), hole(cx=500)])
    assert e.value.feature_id == "hole" and "removed no material" in str(e.value)


def test_schema_errors_surface_as_feature_tree_error():
    with pytest.raises(FeatureTreeError) as e:
        build_feature_tree([plate(distance=-1)])
    assert e.value.feature_id == "(schema)"


# ------------------------------------------------------------------ sweeps
PIPE_PROFILE = {"type": "circle", "diameter": 50, "holes": [{"type": "circle", "diameter": 44}]}
RING_AREA = math.pi * (25 ** 2 - 22 ** 2)


def test_straight_pipe_volume_and_extent():
    pipe = {"id": "pipe", "op": "sweep", "profile": PIPE_PROFILE, "path": [[0, 0, 0], [0, 0, 300]]}
    body = build_feature_tree([pipe])
    bb = body.val().BoundingBox()
    assert vol(body) == pytest.approx(RING_AREA * 300, rel=1e-4)
    assert bb.zmin == pytest.approx(0, abs=1e-6) and bb.zmax == pytest.approx(300, abs=1e-6)
    assert bb.xlen == pytest.approx(50, abs=1e-3)


def test_bent_pipe_volume_is_area_times_centerline_length():
    from feature_tree import expand_path, path_length
    path = [[0, 0, 0], [0, 0, 300], [200, 0, 300], [200, 0, 600]]
    body = build_feature_tree([{"id": "pipe", "op": "sweep", "profile": PIPE_PROFILE, "path": path, "bend_radius": 100}])
    length = path_length(expand_path(path, 100)[0])
    assert len(body.solids().vals()) == 1 and body.val().isValid()
    assert vol(body) == pytest.approx(RING_AREA * length, rel=3e-3)


def test_profile_is_placed_perpendicular_to_a_path_along_x():
    pipe = {"id": "pipe", "op": "sweep", "profile": PIPE_PROFILE, "path": [[10, 5, 5], [310, 5, 5]]}
    bb = build_feature_tree([pipe]).val().BoundingBox()
    assert bb.xmin == pytest.approx(10, abs=1e-3) and bb.xmax == pytest.approx(310, abs=1e-3)
    assert bb.ylen == pytest.approx(50, abs=1e-3) and bb.zlen == pytest.approx(50, abs=1e-3)
    assert (bb.ymin + bb.ymax) / 2 == pytest.approx(5, abs=1e-3)


def test_rectangular_duct_with_sharp_corner_is_one_valid_solid():
    duct = {"id": "duct", "op": "sweep", "profile": rect(20, 10), "path": [[0, 0, 0], [0, 0, 100], [80, 0, 100]]}
    body = build_feature_tree([duct])
    assert len(body.solids().vals()) == 1 and body.val().isValid()


def test_swept_groove_cut_and_pattern_remove_the_expected_volume():
    block = {"id": "block", "op": "extrude", "plane": "XY", "profile": rect(100, 100), "distance": 20}
    groove = {"id": "groove", "op": "sweep", "mode": "cut", "profile": {"type": "circle", "diameter": 6},
              "path": [[-40, -40, 10], [-40, 40, 10]]}
    one = build_feature_tree([block, groove])
    assert vol(one) == pytest.approx(100 * 100 * 20 - math.pi * 9 * 80, rel=1e-3)
    pattern = {"id": "grooves", "op": "pattern", "feature": "groove", "kind": "linear", "count": 3, "spacing": [30, 0, 0]}
    three = build_feature_tree([block, groove, pattern])
    assert vol(three) == pytest.approx(100 * 100 * 20 - 3 * math.pi * 9 * 80, rel=1e-3)


def test_empty_feature_tree_is_rejected_not_defaulted():
    for empty in (None, [], {}, {"features": []}):
        with pytest.raises(FeatureTreeError) as e:
            build_feature_tree(empty)
        assert e.value.feature_id == "(schema)"
    with pytest.raises(FeatureTreeError):
        TEMPLATES["feature_tree"]({})
