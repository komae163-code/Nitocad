"""Exporter checks that need no real geometry: file verification, the STEP re-import
check (with OCCT calls faked), the kill switch, and hole-circle matching.

Runs with or without CadQuery installed. The real-geometry versions live in
test_exporters.py (marked requires_cadquery)."""
import math
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

cq = pytest.importorskip("cadquery", reason="exporters imports cadquery at module level")

import exporters  # noqa: E402
from exceptions import GenerationError  # noqa: E402


def tmp() -> Path:
    return Path(tempfile.mkdtemp())


class FakeShape:
    def __init__(self, bounds=(0, 0, 0, 10, 10, 10), volume=1000.0):
        self.bounds, self._volume, self.wrapped = bounds, volume, object()

    def Volume(self):
        return self._volume


def run_roundtrip(source, imported, path, source_obj=None):
    bounds = {id(source): source.bounds, id(imported): imported.bounds}
    with mock.patch.object(exporters, "_exact_bounds", lambda s: bounds[id(s)]), \
         mock.patch.object(exporters, "_shape_for_roundtrip_check", lambda o: source), \
         mock.patch.object(exporters.cq.importers, "importStep",
                           lambda p: mock.Mock(val=lambda: imported)):
        exporters._validate_step_roundtrip(source_obj or object(), path)


# ---------------------------------------------------------- file checks ---

def test_assert_export_written_rejects_missing_and_empty():
    d = tmp()
    with pytest.raises(GenerationError):
        exporters._assert_export_written(d / "none.step", "step")
    (d / "empty.stl").write_bytes(b"")
    with pytest.raises(GenerationError):
        exporters._assert_export_written(d / "empty.stl", "stl")
    (d / "ok.stl").write_bytes(b"x")
    assert exporters._assert_export_written(d / "ok.stl", "stl") == d / "ok.stl"


def test_prepare_output_path_creates_parent_dirs():
    d = tmp() / "a" / "b" / "c.step"
    assert exporters._prepare_output_path(d) == d and d.parent.is_dir()


def test_stl_rejects_non_positive_tolerance():
    for kw in ({"tolerance": 0}, {"angular_tolerance": -1}):
        with pytest.raises(ValueError):
            exporters.export_stl(mock.Mock(), tmp() / "a.stl", **kw)


# ------------------------------------------------- STEP re-import check ---

def test_roundtrip_passes_when_geometry_matches_and_keeps_file():
    p = tmp() / "ok.step"; p.write_text("STEP")
    run_roundtrip(FakeShape(), FakeShape(), p)
    assert p.exists()


def test_roundtrip_tolerates_tiny_float_noise():
    p = tmp() / "ok.step"; p.write_text("STEP")
    run_roundtrip(FakeShape(), FakeShape((0, 0, 0, 10.00002, 10, 10), 1000.001), p)


def test_roundtrip_bbox_mismatch_raises_and_deletes_the_bad_file():
    p = tmp() / "bad.step"; p.write_text("STEP")
    with pytest.raises(GenerationError, match="bounding-box"):
        run_roundtrip(FakeShape(), FakeShape((0, 0, 0, 25.4, 10, 10)), p)  # a units mix-up
    assert not p.exists()


def test_roundtrip_volume_mismatch_raises():
    p = tmp() / "bad.step"; p.write_text("STEP")
    with pytest.raises(GenerationError, match="volume"):
        run_roundtrip(FakeShape(), FakeShape(volume=500.0), p)  # a body went missing
    assert not p.exists()


def test_roundtrip_import_failure_becomes_generation_error():
    p = tmp() / "bad.step"; p.write_text("STEP")
    with mock.patch.object(exporters.cq.importers, "importStep", side_effect=RuntimeError("corrupt")), \
         mock.patch.object(exporters, "_shape_for_roundtrip_check", lambda o: FakeShape()):
        with pytest.raises(GenerationError, match="round-trip validated"):
            exporters._validate_step_roundtrip(object(), p)
    assert not p.exists()


def test_roundtrip_source_is_every_object_in_a_workplane():
    wp = mock.MagicMock(spec=cq.Workplane)
    wp.toCompound.return_value = "ALL"
    assert exporters._shape_for_roundtrip_check(wp) == "ALL"
    shape = object()
    assert exporters._shape_for_roundtrip_check(shape) is shape


def test_kill_switch_skips_the_roundtrip_but_not_the_file_check():
    out = tmp() / "x.step"

    def fake_export(obj, path, exportType=None, **kw):
        Path(path).write_text("STEP")
    with mock.patch.object(exporters.cq.exporters, "export", fake_export), \
         mock.patch.object(exporters, "_as_workplane", lambda o: o), \
         mock.patch.object(exporters, "_validate_step_roundtrip") as check:
        with mock.patch.dict(os.environ, {"NITOCAD_STEP_ROUNDTRIP": "0"}):
            exporters.export_step(mock.MagicMock(spec=cq.Workplane), out)
            check.assert_not_called()
        with mock.patch.dict(os.environ, {"NITOCAD_STEP_ROUNDTRIP": "1"}):
            exporters.export_step(mock.MagicMock(spec=cq.Workplane), out)
            check.assert_called_once()


def test_export_step_fails_loudly_when_nothing_is_written():
    with mock.patch.object(exporters.cq.exporters, "export", lambda *a, **k: None), \
         mock.patch.object(exporters, "_as_workplane", lambda o: o):
        with pytest.raises(GenerationError, match="did not create"):
            exporters.export_step(mock.MagicMock(spec=cq.Workplane), tmp() / "none.step")


# ------------------------------------------------------ hole matching ---

def circle(cx, cy, r, n=32):
    pts = [(cx + r * math.cos(2 * math.pi * i / (n - 1)), cy + r * math.sin(2 * math.pi * i / (n - 1))) for i in range(n)]
    pts[-1] = pts[0]
    return pts


def view(lines, bounds=(0, 60, 0, 40), holes=()):
    return exporters._View(name="top", label="TOP", visible=lines, hidden=[],
                           u_min=bounds[0], u_max=bounds[1], v_min=bounds[2], v_max=bounds[3],
                           hole_circles=list(holes))


def test_outer_silhouette_circle_is_not_a_hole():
    v = view([circle(5, 5, 5)], bounds=(0, 10, 0, 10))   # top view of a solid cylinder
    assert exporters._detect_circles(v) == []


def test_internal_circle_is_a_candidate_but_needs_topology_proof():
    v = view([circle(20, 20, 3)])
    assert len(exporters._detect_circles(v)) == 1
    assert exporters._validated_hole_circles(v) == []                        # no proven holes
    assert len(exporters._validated_hole_circles(view([circle(20, 20, 3)], holes=[(20, 20, 3)]))) == 1


def test_mismatched_radius_or_position_is_not_validated():
    assert exporters._validated_hole_circles(view([circle(20, 20, 3)], holes=[(20, 20, 4.5)])) == []
    assert exporters._validated_hole_circles(view([circle(20, 20, 3)], holes=[(30, 20, 3)])) == []


def test_unclosed_arc_is_never_a_hole():
    arc = circle(20, 20, 3)[:-6]
    assert exporters._detect_circles(view([arc])) == []


def test_topology_detection_never_raises():
    assert isinstance(exporters._detect_topology_holes(object(), "top"), list)
