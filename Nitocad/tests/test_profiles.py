"""2D file readers (profiles.py): DXF, SVG, KiCad boards, Gerber and drill files. Pure Python.

Expected areas and positions are worked out by hand, not read back from the code."""
from __future__ import annotations

import io
import math
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import profiles as P  # noqa: E402


def net_area(shape):
    def area(o):
        return math.pi * o["r"] ** 2 if o["type"] == "circle" else abs(P.signed_area([tuple(p) for p in o["points"]]))
    return area(shape["outer"]) - sum(area(h) for h in shape["holes"])


def dxf(body: str, units: int = 4) -> str:
    return f"0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n{units}\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n{body}0\nENDSEC\n0\nEOF\n"


RECT = "0\nLWPOLYLINE\n90\n4\n70\n1\n10\n0\n20\n0\n10\n100\n20\n0\n10\n100\n20\n60\n10\n0\n20\n60\n"
HOLE = "0\nCIRCLE\n10\n50\n20\n30\n40\n4\n"


class TestGeometry:
    def test_arc_points_end_on_the_circle_and_hit_both_ends(self):
        pts = P.arc_points(0, 0, 10, 0, math.pi / 2)
        assert pts[0] == pytest.approx((10, 0)) and pts[-1] == pytest.approx((0, 10), abs=1e-9)
        assert all(math.hypot(x, y) == pytest.approx(10) for x, y in pts)

    def test_chord_error_stays_under_the_tolerance(self):
        pts = P.arc_points(0, 0, 50, 0, math.pi)
        mid = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
        assert 50 - math.hypot(*mid) <= P.CHORD_TOL_MM + 1e-9

    def test_bulge_of_one_is_a_semicircle(self):
        pts = P.bulge_arc((0, 0), (20, 0), 1.0)
        assert all(math.hypot(x - 10, y) == pytest.approx(10, abs=1e-6) for x, y in pts)
        assert min(p[1] for p in pts) < -9.9 or max(p[1] for p in pts) > 9.9       # it bulges to one side

    def test_three_point_arc_passes_through_its_middle_point(self):
        pts = P.three_point_arc((10, 0), (0, 10), (-10, 0))
        assert all(math.hypot(x, y) == pytest.approx(10, abs=1e-6) for x, y in pts)
        assert max(y for _, y in pts) > 9.9 and min(y for _, y in pts) > -1e-9       # went over the top, not under

    def test_point_in_polygon(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert P.point_in_polygon((5, 5), sq) and not P.point_in_polygon((15, 5), sq)

    def test_paths_that_meet_are_joined_in_any_direction(self):
        loops, open_left = P.chain_paths([[(0, 0), (10, 0)], [(10, 10), (10, 0)], [(10, 10), (0, 10)], [(0, 0), (0, 10)]])
        assert len(loops) == 1 and open_left == 0 and abs(P.signed_area(loops[0])) == pytest.approx(100)

    def test_a_gap_leaves_the_path_open(self):
        loops, open_left = P.chain_paths([[(0, 0), (10, 0)], [(10, 0), (10, 10)], [(10, 10), (0, 10.5)]])
        assert loops == [] and open_left == 1

    def test_nesting_alternates_island_and_hole(self):
        loops = [[(0, 0), (100, 0), (100, 100), (0, 100)], [(10, 10), (90, 10), (90, 90), (10, 90)], [(30, 30), (60, 30), (60, 60), (30, 60)]]
        prof = P.build_profile(loops, [], source="t")
        assert len(prof.shapes) == 2 and sorted(len(s["holes"]) for s in prof.shapes) == [0, 1]

    def test_a_drawing_with_no_closed_shape_is_refused(self):
        with pytest.raises(P.ProfileError):
            P.build_profile([], [], source="t")


class TestDxf:
    def test_rectangle_with_a_round_hole(self):
        prof = P.parse_dxf(dxf(RECT + HOLE))
        assert len(prof.shapes) == 1 and net_area(prof.shapes[0]) == pytest.approx(100 * 60 - math.pi * 16)
        assert prof.stats()["round_holes"] == [{"diameter_mm": 8.0, "count": 1}]
        assert prof.bbox() == pytest.approx((0, 0, 100, 60))

    def test_inch_drawings_are_converted_to_millimetres(self):
        assert P.parse_dxf(dxf(RECT, units=1)).bbox() == pytest.approx((0, 0, 2540, 1524))

    def test_no_units_is_read_as_millimetres_with_a_note(self):
        prof = P.parse_dxf(dxf(RECT, units=0))
        assert prof.bbox() == pytest.approx((0, 0, 100, 60)) and any("no units" in w for w in prof.warnings)

    def test_lines_and_an_arc_are_chained_into_one_shape(self):
        body = ("0\nLINE\n10\n0\n20\n0\n11\n40\n21\n0\n0\nARC\n10\n40\n20\n20\n40\n20\n50\n-90\n51\n90\n"
                "0\nLINE\n10\n40\n20\n40\n11\n0\n21\n40\n0\nLINE\n10\n0\n20\n40\n11\n0\n21\n0\n")
        prof = P.parse_dxf(dxf(body))
        assert net_area(prof.shapes[0]) == pytest.approx(40 * 40 + math.pi * 20 ** 2 / 2, rel=0.005)

    def test_bulge_arcs_in_a_polyline(self):
        body = "0\nLWPOLYLINE\n70\n1\n90\n2\n10\n0\n20\n0\n42\n1\n10\n20\n20\n0\n"
        assert net_area(P.parse_dxf(dxf(body)).shapes[0]) == pytest.approx(math.pi * 100 / 2, rel=0.005)

    def test_skipped_entities_are_reported_not_silently_lost(self):
        prof = P.parse_dxf(dxf(RECT + "0\nSPLINE\n8\n0\n0\nINSERT\n2\nBLK\n"))
        assert any("spline" in w for w in prof.warnings) and any("insert" in w for w in prof.warnings)

    @pytest.mark.parametrize("text", ["AutoCAD Binary DXF\r\n\x1a", "not a dxf at all", dxf("0\nLINE\n10\n0\n20\n0\n11\n1\n21\n1\n"), dxf("")])
    def test_unusable_files_are_refused_with_a_message(self, text):
        with pytest.raises(P.ProfileError):
            P.parse_dxf(text)


class TestSvg:
    def test_size_comes_from_width_and_view_box(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60"><rect x="0" y="0" width="100" height="60"/><circle cx="50" cy="30" r="4"/></svg>'
        prof = P.parse_svg(svg)
        assert net_area(prof.shapes[0]) == pytest.approx(6000 - math.pi * 16) and prof.stats()["round_holes"][0]["diameter_mm"] == 8.0

    def test_scaled_view_box(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="50mm" viewBox="0 0 100 100"><rect x="0" y="0" width="100" height="100"/></svg>'
        assert P.parse_svg(svg).stats()["width_mm"] == pytest.approx(50)

    def test_transforms_are_applied(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="100mm" viewBox="0 0 100 100"><g transform="translate(10,10) scale(2)"><rect x="0" y="0" width="10" height="10"/></g></svg>'
        assert P.parse_svg(svg).stats()["width_mm"] == pytest.approx(20)

    def test_path_with_two_subpaths_makes_a_ring(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="40mm" viewBox="0 0 40 40"><path d="M0 0 H40 V40 H0 Z M10 10 h20 v20 h-20 Z"/></svg>'
        assert net_area(P.parse_svg(svg).shapes[0]) == pytest.approx(1600 - 400)

    def test_path_arcs_make_a_circle(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" viewBox="0 0 20 20"><path d="M0,10 A10,10 0 1,1 20,10 A10,10 0 1,1 0,10 Z"/></svg>'
        assert net_area(P.parse_svg(svg).shapes[0]) == pytest.approx(math.pi * 100, rel=0.01)

    def test_curves_stay_inside_their_control_box(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="20mm" viewBox="0 0 20 20"><path d="M0 0 C 0 20, 20 20, 20 0 Z"/></svg>'
        x0, y0, x1, y1 = P.parse_svg(svg).bbox()
        assert (x1 - x0) == pytest.approx(20, abs=0.01) and 0 < (y1 - y0) < 20

    def test_no_size_is_noted(self):
        prof = P.parse_svg('<svg xmlns="http://www.w3.org/2000/svg"><rect x="0" y="0" width="10" height="10"/></svg>')
        assert any("no width and height" in w for w in prof.warnings)

    @pytest.mark.parametrize("text", ['<!DOCTYPE svg [<!ENTITY a "b">]><svg/>', "<html/>", "not xml", "<svg xmlns='http://www.w3.org/2000/svg'/>"])
    def test_hostile_or_empty_files_are_refused(self, text):
        with pytest.raises(P.ProfileError):
            P.parse_svg(text)


KICAD = '''(kicad_pcb (version 20221018) (generator pcbnew)
 (general (thickness 1.6))
 (gr_line (start 100 100) (end 150 100) (layer "Edge.Cuts"))
 (gr_line (start 150 100) (end 150 140) (layer "Edge.Cuts"))
 (gr_line (start 150 140) (end 100 140) (layer "Edge.Cuts"))
 (gr_line (start 100 140) (end 100 100) (layer "Edge.Cuts"))
 (via (at 110 110) (size 0.8) (drill 0.4))
 (footprint "MountingHole" (layer "F.Cu") (at 140 130 90)
   (pad "" np_thru_hole circle (at 5 0) (size 3.2 3.2) (drill 3.2)))
)'''


class TestKicad:
    def test_outline_thickness_and_holes(self):
        prof = P.parse_kicad_pcb(KICAD)
        assert prof.bbox() == pytest.approx((100, -140, 150, -100)) and prof.suggested_thickness_mm == 1.6
        holes = sorted((round(h["cx"], 3), round(h["cy"], 3), h["r"]) for h in prof.shapes[0]["holes"])
        assert holes == [(110.0, -110.0, 0.2), (140.0, -125.0, 1.6)]     # rotated footprint: pad moves up the board

    def test_holes_outside_the_board_are_ignored(self):
        text = KICAD.replace("(via (at 110 110)", "(via (at 500 500)")
        assert len(P.parse_kicad_pcb(text).shapes[0]["holes"]) == 1

    def test_circle_board(self):
        text = '(kicad_pcb (gr_circle (center 50 50) (end 70 50) (layer "Edge.Cuts")))'
        s = P.parse_kicad_pcb(text).shapes[0]["outer"]
        assert s["type"] == "circle" and s["r"] == pytest.approx(20)

    def test_default_thickness_is_noted(self):
        text = KICAD.replace("(general (thickness 1.6))", "")
        assert any("not set" in w for w in P.parse_kicad_pcb(text).warnings)

    def test_open_outline_is_reported(self):
        text = '(kicad_pcb (gr_line (start 0 0) (end 10 0) (layer "Edge.Cuts")) (gr_rect (start 20 20) (end 30 30) (layer "Edge.Cuts")))'
        prof = P.parse_kicad_pcb(text)
        assert len(prof.shapes) == 1 and any("open edge" in w for w in prof.warnings)

    @pytest.mark.parametrize("text", ["(foo)", "(kicad_pcb", "plain text", "(kicad_pcb (general (thickness 1.6)))"])
    def test_unusable_files_are_refused(self, text):
        with pytest.raises(P.ProfileError):
            P.parse_kicad_pcb(text)


GERBER = "%FSLAX24Y24*%\n%MOMM*%\n%TF.FileFunction,Profile,NP*%\n%ADD10C,0.1*%\nG01*\nD10*\nX0Y0D02*\nX500000Y0D01*\nX500000Y400000D01*\nX0Y400000D01*\nX0Y0D01*\nM02*\n"
DRILL = "M48\nMETRIC\nT1C3.200\n%\nT1\nX10.0Y10.0\nX40.0Y30.0\nM30\n"


class TestGerber:
    def test_outline_loop_and_extents(self):
        loops, ext, is_outline, _ = P.parse_gerber(GERBER, "board.gko")
        assert is_outline and ext == pytest.approx((0, 0, 50, 40)) and abs(P.signed_area(loops[0])) == pytest.approx(2000)

    def test_a_clockwise_or_counter_clockwise_corner_arc(self):
        g = "%FSLAX24Y24*%\n%MOMM*%\nG01*\nX0Y0D02*\nX100000Y0D01*\nG75*\nG03*\nX200000Y100000I0J100000D01*\nG01*\nX200000Y200000D01*\nX0Y200000D01*\nX0Y0D01*\nM02*\n"
        loops, *_ = P.parse_gerber(g, "Edge_Cuts.gbr")
        assert abs(P.signed_area(loops[0])) == pytest.approx(400 - (100 - math.pi * 25), rel=0.01)

    def test_inches_and_trailing_zero_formats(self):
        g = "%FSTAX24Y24*%\n%MOIN*%\nG01*\nX0Y0D02*\nX1Y0D01*\nX1Y1D01*\nX0Y1D01*\nX0Y0D01*\n"
        loops, ext, *_ = P.parse_gerber(g, "outline.gbr")
        assert ext[2] == pytest.approx(25.4 * 1.0, rel=1e-9) or ext[2] == pytest.approx(25.4 * 10.0)

    def test_drill_hits(self):
        assert P.parse_excellon(DRILL) == [(10.0, 10.0, 3.2), (40.0, 30.0, 3.2)]

    def test_drill_without_decimals_is_refused_clearly(self):
        with pytest.raises(P.ProfileError):
            P.parse_excellon("M48\nMETRIC\nT1C0.8\n%\nT1\nX1000Y2000\n")

    def test_a_zip_of_layers_makes_a_board_with_holes(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("board.gko", GERBER)
            z.writestr("board-PTH.drl", DRILL)
            z.writestr("board.gtl", "%FSLAX24Y24*%\n%MOMM*%\nX0Y0D02*\nX100000Y0D01*\n")
        prof = P.parse_profile_file("gerber_zip", buf.getvalue())
        assert prof.bbox() == pytest.approx((0, 0, 50, 40)) and len(prof.shapes[0]["holes"]) == 2 and prof.suggested_thickness_mm == 1.6

    def test_without_an_outline_layer_a_rectangle_around_everything_is_used(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("top.gtl", "%FSLAX24Y24*%\n%MOMM*%\nX0Y0D02*\nX300000Y200000D01*\n")
        prof = P.parse_profile_file("gerber_zip", buf.getvalue())
        assert prof.bbox() == pytest.approx((0, 0, 30, 20)) and any("rectangle" in w for w in prof.warnings)

    def test_a_drill_file_alone_has_no_outline(self):
        with pytest.raises(P.ProfileError) as e:
            P.parse_profile_file("drill", DRILL.encode())
        assert "outline" in str(e.value)


class TestZipSafety:
    def test_too_many_files(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for i in range(101):
                z.writestr(f"f{i}.gbr", "x")
        with pytest.raises(P.ProfileError):
            P.read_zip(buf.getvalue())

    def test_path_traversal_names_are_dropped(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../evil.gbr", "x")
            z.writestr("ok.gbr", "y")
        assert list(P.read_zip(buf.getvalue())) == ["ok.gbr"]

    def test_a_damaged_zip(self):
        with pytest.raises(P.ProfileError):
            P.read_zip(b"PK\x03\x04 not really")

    def test_unpacked_size_is_capped(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("big.gbr", b"0" * (2 * 1024 * 1024))
        with pytest.raises(P.ProfileError):
            P.read_zip(buf.getvalue(), max_each=1024 * 1024)
