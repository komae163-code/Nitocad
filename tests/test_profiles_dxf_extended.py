"""DXF entities the first reader skipped: splines, ellipses, blocks (INSERT) and mirrored extrusions.

Expected areas are worked out by hand from the geometry, not read back from the code. Pure Python."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import profiles as P  # noqa: E402


def dxf(body: str, blocks: str = "", units: int = 4) -> str:
    head = f"0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n{units}\n0\nENDSEC\n"
    blk = f"0\nSECTION\n2\nBLOCKS\n{blocks}0\nENDSEC\n" if blocks else ""
    return head + blk + f"0\nSECTION\n2\nENTITIES\n{body}0\nENDSEC\n0\nEOF\n"


def area(o):
    return math.pi * o["r"] ** 2 if o["type"] == "circle" else abs(P.signed_area([tuple(p) for p in o["points"]]))


def net(shape):
    return area(shape["outer"]) - sum(area(h) for h in shape["holes"])


def rect_entity(x, y, w, h, layer=0):
    return (f"0\nLWPOLYLINE\n8\n{layer}\n90\n4\n70\n1\n10\n{x}\n20\n{y}\n10\n{x + w}\n20\n{y}\n"
            f"10\n{x + w}\n20\n{y + h}\n10\n{x}\n20\n{y + h}\n")


def spline(deg, knots, ctrl, weights=None, flags=0):
    s = f"0\nSPLINE\n70\n{flags}\n71\n{deg}\n"
    s += "".join(f"40\n{k}\n" for k in knots)
    if weights:
        s += "".join(f"41\n{w}\n" for w in weights)
    s += "".join(f"10\n{x}\n20\n{y}\n30\n0\n" for x, y in ctrl)
    return s


R2 = math.sqrt(2) / 2
# a full circle of radius 10 as a rational quadratic NURBS (nine control points, the standard form)
NURBS_CIRCLE = spline(2, [0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 4],
                      [(10, 0), (10, 10), (0, 10), (-10, 10), (-10, 0), (-10, -10), (0, -10), (10, -10), (10, 0)],
                      [1, R2, 1, R2, 1, R2, 1, R2, 1])


class TestSpline:
    def test_a_rational_nurbs_circle_has_the_area_of_a_circle(self):
        prof = P.parse_dxf(dxf(NURBS_CIRCLE))
        assert len(prof.shapes) == 1 and net(prof.shapes[0]) == pytest.approx(math.pi * 100, rel=0.002)

    def test_points_lie_on_the_circle(self):
        pts = P.parse_dxf(dxf(NURBS_CIRCLE)).shapes[0]["outer"]["points"]
        assert all(math.hypot(x, y) == pytest.approx(10, abs=0.01) for x, y in pts)

    def test_a_spline_closed_with_straight_lines_makes_a_plate(self):
        # a 40 x 10 rectangle with a cubic Bezier bump on top, control heights above y = 10 of 0, 20, 20, 0 at x = 0, 10, 30, 40.
        # Bump area = integral of h(t) x'(t) dt with h = 60 t (1 - t) and x' = 3 (10 + 20 t (1 - t)) * ... = 180 (10/6 + 20/30) = 420
        bump = spline(3, [0, 0, 0, 0, 1, 1, 1, 1], [(0, 10), (10, 30), (30, 30), (40, 10)])
        body = bump + "0\nLINE\n10\n40\n20\n10\n11\n40\n21\n0\n0\nLINE\n10\n40\n20\n0\n11\n0\n21\n0\n0\nLINE\n10\n0\n20\n0\n11\n0\n21\n10\n"
        prof = P.parse_dxf(dxf(body))
        assert net(prof.shapes[0]) == pytest.approx(40 * 10 + 420, rel=0.002)

    def test_fit_point_spline_passes_through_its_points(self):
        fit = "0\nSPLINE\n70\n1\n" + "".join(f"11\n{x}\n21\n{y}\n" for x, y in [(0, 0), (20, 0), (20, 20), (0, 20)])
        prof = P.parse_dxf(dxf(fit))
        pts = [tuple(p) for p in prof.shapes[0]["outer"]["points"]]
        for corner in [(0, 0), (20, 0), (20, 20), (0, 20)]:
            assert any(math.hypot(x - corner[0], y - corner[1]) < 1e-6 for x, y in pts)

    def test_inconsistent_spline_is_reported_not_crashed(self):
        bad = spline(3, [0, 0, 1, 1], [(0, 0), (1, 1), (2, 0), (3, 1)])
        prof = P.parse_dxf(dxf(rect_entity(0, 0, 10, 10) + bad))
        assert any("spline" in w for w in prof.warnings) and len(prof.shapes) == 1


class TestEllipse:
    def test_full_ellipse_area(self):
        e = "0\nELLIPSE\n10\n0\n20\n0\n11\n30\n21\n0\n40\n0.5\n41\n0\n42\n6.283185307179586\n"
        assert net(P.parse_dxf(dxf(e)).shapes[0]) == pytest.approx(math.pi * 30 * 15, rel=0.002)

    def test_rotated_ellipse_keeps_its_area(self):
        e = "0\nELLIPSE\n10\n5\n20\n5\n11\n0\n21\n20\n40\n0.25\n41\n0\n42\n6.283185307179586\n"
        assert net(P.parse_dxf(dxf(e)).shapes[0]) == pytest.approx(math.pi * 20 * 5, rel=0.002)

    def test_half_ellipse_closed_by_its_diameter(self):
        e = ("0\nELLIPSE\n10\n0\n20\n0\n11\n30\n21\n0\n40\n0.5\n41\n0\n42\n3.141592653589793\n"
             "0\nLINE\n10\n-30\n20\n0\n11\n30\n21\n0\n")
        assert net(P.parse_dxf(dxf(e)).shapes[0]) == pytest.approx(math.pi * 30 * 15 / 2, rel=0.003)


class TestBlocks:
    BLOCK = "0\nBLOCK\n2\nBRK\n10\n0\n20\n0\n" + rect_entity(0, 0, 20, 10) + "0\nENDBLK\n"

    def test_two_inserts_make_two_shapes(self):
        ins = lambda x, y, extra="": f"0\nINSERT\n2\nBRK\n10\n{x}\n20\n{y}\n{extra}"      # noqa: E731
        prof = P.parse_dxf(dxf(ins(0, 0) + ins(100, 0), self.BLOCK))
        assert len(prof.shapes) == 2 and all(net(s) == pytest.approx(200) for s in prof.shapes)

    def test_scale_and_rotation(self):
        ins = "0\nINSERT\n2\nBRK\n10\n50\n20\n50\n41\n2\n42\n3\n50\n90\n"
        prof = P.parse_dxf(dxf(ins, self.BLOCK))
        xs = [p[0] for p in prof.shapes[0]["outer"]["points"]]
        ys = [p[1] for p in prof.shapes[0]["outer"]["points"]]
        # 20 x 10 scaled 2 x 3 is 40 x 30, then turned 90 degrees: 30 wide, 40 tall, corner at the insertion point
        assert net(prof.shapes[0]) == pytest.approx(1200)
        assert (max(xs) - min(xs), max(ys) - min(ys)) == pytest.approx((30, 40))
        assert (min(ys), max(xs)) == pytest.approx((50, 50))

    def test_block_base_point_is_respected(self):
        blk = "0\nBLOCK\n2\nB2\n10\n10\n20\n5\n" + rect_entity(10, 5, 20, 10) + "0\nENDBLK\n"
        prof = P.parse_dxf(dxf("0\nINSERT\n2\nB2\n10\n100\n20\n100\n", blk))
        pts = prof.shapes[0]["outer"]["points"]
        assert (min(p[0] for p in pts), min(p[1] for p in pts)) == pytest.approx((100, 100))

    def test_array_insert(self):
        ins = "0\nINSERT\n2\nBRK\n10\n0\n20\n0\n70\n3\n71\n2\n44\n40\n45\n30\n"
        assert len(P.parse_dxf(dxf(ins, self.BLOCK)).shapes) == 6

    def test_nested_blocks(self):
        outer = "0\nBLOCK\n2\nPAIR\n10\n0\n20\n0\n0\nINSERT\n2\nBRK\n10\n0\n20\n0\n0\nINSERT\n2\nBRK\n10\n0\n20\n20\n0\nENDBLK\n"
        prof = P.parse_dxf(dxf("0\nINSERT\n2\nPAIR\n10\n0\n20\n0\n0\nINSERT\n2\nPAIR\n10\n100\n20\n0\n", self.BLOCK + outer))
        assert len(prof.shapes) == 4

    def test_circle_in_a_uniformly_scaled_block_stays_exact(self):
        blk = "0\nBLOCK\n2\nC\n10\n0\n20\n0\n0\nCIRCLE\n10\n0\n20\n0\n40\n3\n0\nENDBLK\n"
        body = rect_entity(0, 0, 100, 100) + "0\nINSERT\n2\nC\n10\n50\n20\n50\n41\n2\n42\n2\n"
        hole = P.parse_dxf(dxf(body, blk)).shapes[0]["holes"][0]
        assert hole["type"] == "circle" and hole["r"] == pytest.approx(6)

    def test_circle_in_an_unevenly_scaled_block_becomes_an_ellipse_outline(self):
        blk = "0\nBLOCK\n2\nC\n10\n0\n20\n0\n0\nCIRCLE\n10\n0\n20\n0\n40\n3\n0\nENDBLK\n"
        body = rect_entity(0, 0, 100, 100) + "0\nINSERT\n2\nC\n10\n50\n20\n50\n41\n2\n42\n1\n"
        hole = P.parse_dxf(dxf(body, blk)).shapes[0]["holes"][0]
        assert hole["type"] == "polygon" and area(hole) == pytest.approx(math.pi * 6 * 3, rel=0.01)

    def test_unknown_block_is_reported(self):
        prof = P.parse_dxf(dxf(rect_entity(0, 0, 10, 10) + "0\nINSERT\n2\nNOPE\n10\n0\n20\n0\n"))
        assert any("insert" in w for w in prof.warnings)

    def test_a_block_that_inserts_itself_cannot_run_away(self):
        bomb = "0\nBLOCK\n2\nA\n10\n0\n20\n0\n" + rect_entity(0, 0, 1, 1) + "0\nINSERT\n2\nA\n10\n2\n20\n0\n0\nINSERT\n2\nA\n10\n4\n20\n0\n0\nENDBLK\n"
        prof = P.parse_dxf(dxf("0\nINSERT\n2\nA\n10\n0\n20\n0\n", bomb))
        assert len(prof.shapes) <= 2 ** 9                  # bounded by the nesting depth, not infinite

    def test_polyline_with_vertex_records_inside_a_block(self):
        poly = ("0\nPOLYLINE\n70\n1\n0\nVERTEX\n10\n0\n20\n0\n0\nVERTEX\n10\n10\n20\n0\n0\nVERTEX\n10\n10\n20\n10\n0\nVERTEX\n10\n0\n20\n10\n0\nSEQEND\n")
        blk = "0\nBLOCK\n2\nP\n10\n0\n20\n0\n" + poly + "0\nENDBLK\n"
        prof = P.parse_dxf(dxf("0\nINSERT\n2\nP\n10\n5\n20\n5\n", blk))
        assert net(prof.shapes[0]) == pytest.approx(100)


class TestMirroredExtrusion:
    def test_arc_with_downward_extrusion_is_mirrored_in_x(self):
        # a half disc: the upper half circle about (50, 0), radius 10, closed by its diameter. With the extrusion pointing
        # down the whole shape sits about x = -50. Without the mirror the arc and the line would not meet and no shape would form.
        body = ("0\nARC\n10\n50\n20\n0\n40\n10\n50\n0\n51\n180\n230\n-1\n"
                "0\nLINE\n10\n-60\n20\n0\n11\n-40\n21\n0\n")
        prof = P.parse_dxf(dxf(body))
        pts = [tuple(p) for p in prof.shapes[0]["outer"]["points"]]
        assert net(prof.shapes[0]) == pytest.approx(math.pi * 100 / 2, rel=0.005)
        assert max(x for x, _ in pts) <= -40 + 1e-6 and min(x for x, _ in pts) >= -60 - 1e-6

    def test_circle_centre_is_mirrored(self):
        body = rect_entity(-100, -50, 100, 100) + "0\nCIRCLE\n10\n40\n20\n0\n40\n5\n230\n-1\n"
        hole = P.parse_dxf(dxf(body)).shapes[0]["holes"][0]
        assert (hole["cx"], hole["cy"]) == pytest.approx((-40, 0))


class TestUnits:
    def test_units_apply_through_blocks(self):
        blk = "0\nBLOCK\n2\nB\n10\n0\n20\n0\n" + rect_entity(0, 0, 1, 1) + "0\nENDBLK\n"
        prof = P.parse_dxf(dxf("0\nINSERT\n2\nB\n10\n0\n20\n0\n41\n10\n42\n10\n", blk, units=1))      # inches
        assert net(prof.shapes[0]) == pytest.approx((10 * 25.4) ** 2)
