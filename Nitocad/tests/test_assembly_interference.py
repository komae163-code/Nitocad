"""Needs the CadQuery kernel (skipped where it is not installed)."""
import pytest

cq = pytest.importorskip("cadquery")

from assembly import check_interference  # noqa: E402


def _box(x, y, z, size=10.0):
    return cq.Workplane("XY").box(size, size, size).translate((x, y, z)).val()


def test_overlapping_boxes_are_reported():
    overlaps, failures = check_interference([("a", _box(0, 0, 0)), ("b", _box(5, 0, 0))])
    assert failures == []
    assert len(overlaps) == 1 and overlaps[0]["volume_mm3"] == pytest.approx(500.0, rel=0.01)


def test_touching_faces_are_not_interference():
    overlaps, _ = check_interference([("a", _box(0, 0, 0)), ("b", _box(10, 0, 0))])
    assert overlaps == []


def test_distant_parts_skip_the_boolean():
    overlaps, failures = check_interference([("a", _box(0, 0, 0)), ("b", _box(500, 0, 0))])
    assert overlaps == [] and failures == []


def test_exact_shaft_in_bore_fit_is_clean():
    shaft = cq.Workplane("XY").circle(5).extrude(40).val()
    bearing = cq.Workplane("XY").circle(11).circle(5).extrude(10).translate((0, 0, 15)).val()
    overlaps, _ = check_interference([("shaft", shaft), ("bearing", bearing)])
    assert overlaps == []
