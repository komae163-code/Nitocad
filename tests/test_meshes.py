"""STL and OBJ meshes (meshes.py): reading, measuring, editing and writing. numpy only."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import meshes as M  # noqa: E402

V = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], float)
F = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
CUBE = V[np.array(F)]


def signed_volume(t):
    return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6.0)


class TestReadWrite:
    def test_binary_stl_round_trip(self):
        data = M.write_stl(CUBE)
        assert len(data) == 84 + 50 * 12 and np.allclose(M.parse_stl(data), CUBE, atol=1e-6)

    def test_ascii_stl(self):
        txt = "solid x\n" + "".join(" facet normal 0 0 0\n  outer loop\n" + "".join(f"   vertex {p[0]} {p[1]} {p[2]}\n" for p in tri) + "  endloop\n endfacet\n" for tri in CUBE) + "endsolid x\n"
        assert len(M.parse_stl(txt.encode())) == 12

    def test_a_binary_file_that_starts_with_solid_is_still_binary(self):
        data = bytearray(M.write_stl(CUBE))
        data[:5] = b"solid"
        assert len(M.parse_stl(bytes(data))) == 12

    def test_obj_with_quads_is_fan_triangulated(self):
        assert len(M.parse_obj(b"v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1/1 2/2 3/3 4/4\n")) == 2

    def test_obj_negative_indices(self):
        assert len(M.parse_obj(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf -3 -2 -1\n")) == 1

    @pytest.mark.parametrize("data", [b"hello", b"solid x\nvertex 1 2\n", b"", struct.pack("<80sI", b"", 5)])
    def test_bad_stl_files(self, data):
        with pytest.raises(M.MeshError):
            M.parse_stl(data)

    @pytest.mark.parametrize("data", [b"f 1 2 3", b"v 0 0 0\nf 1 2 3\n", b"v a b c\n"])
    def test_bad_obj_files(self, data):
        with pytest.raises(M.MeshError):
            M.parse_obj(data)

    def test_triangle_limit(self):
        big = np.zeros((M.MAX_TRIANGLES + 1, 3, 3))
        with pytest.raises(M.MeshError):
            M._check(big)


class TestStats:
    def test_cube(self):
        s = M.stats(CUBE)
        assert s["triangles"] == 12 and s["size_mm"] == [1, 1, 1] and s["watertight"] and s["volume_mm3"] == pytest.approx(1.0)
        assert s["surface_area_mm2"] == pytest.approx(6.0)

    def test_an_open_mesh_is_not_watertight_and_has_no_volume(self):
        s = M.stats(CUBE[:-2])
        assert not s["watertight"] and s["volume_mm3"] is None


class TestEdits:
    def test_scale_multiplies_volume_by_the_cube(self):
        assert M.stats(M.apply_edits(CUBE, [{"op": "scale", "factor": 10}]))["volume_mm3"] == pytest.approx(1000)

    @pytest.mark.parametrize("axis,expect", [("x", [50, 50, 50]), ("largest", [50, 50, 50])])
    def test_scale_to(self, axis, expect):
        assert M.stats(M.apply_edits(CUBE, [{"op": "scale_to", "axis": axis, "mm": 50}]))["size_mm"] == pytest.approx(expect)

    def test_rotate_then_translate(self):
        lo, hi = M.bbox(M.apply_edits(CUBE, [{"op": "rotate", "axis": "z", "degrees": 90}, {"op": "translate", "x": 5, "y": 0, "z": -3}]))
        assert np.allclose(lo, [4, 0, -3]) and np.allclose(hi, [5, 1, -2])

    def test_ground_and_center(self):
        lo, hi = M.bbox(M.apply_edits(CUBE, [{"op": "translate", "x": 3, "y": 4, "z": -7}, {"op": "ground"}, {"op": "center"}]))
        assert np.allclose(lo, [-0.5, -0.5, 0]) and np.allclose(hi, [0.5, 0.5, 1])

    @pytest.mark.parametrize("plane", ["xy", "yz", "xz"])
    def test_mirror_keeps_faces_pointing_outward(self, plane):
        out = M.apply_edits(CUBE, [{"op": "mirror", "plane": plane}])
        assert signed_volume(out) == pytest.approx(1.0) and M.stats(out)["watertight"]

    def test_edits_do_not_change_the_input(self):
        before = CUBE.copy()
        M.apply_edits(CUBE, [{"op": "scale", "factor": 3}])
        assert np.array_equal(before, CUBE)

    def test_solid_only_edits_are_refused(self):
        with pytest.raises(M.MeshError):
            M.apply_edits(CUBE, [{"op": "hole"}])

    def test_scaling_a_flat_mesh_to_its_missing_dimension_is_refused(self):
        flat = CUBE.copy()
        flat[:, :, 2] = 0
        with pytest.raises(M.MeshError):
            M.apply_edits(flat, [{"op": "scale_to", "axis": "z", "mm": 10}])
