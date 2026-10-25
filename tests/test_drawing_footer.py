"""Drawing notes, ISO 2768 table, hole call-outs, parts list layout, and a real PDF written
from synthetic views (reportlab only - no CAD kernel, so it runs anywhere reportlab does)."""
import math
import re
import tempfile
from pathlib import Path
from unittest import mock

import pytest

import drawing_footer as df

# ------------------------------------------------------------- ISO 2768-1 ---

def test_iso2768_values_match_the_standard_at_the_range_edges():
    cases = {  # nominal: (f, m, c, v)
        1: (0.05, 0.1, 0.2, None), 3: (0.05, 0.1, 0.2, None), 3.01: (0.05, 0.1, 0.3, 0.5),
        6: (0.05, 0.1, 0.3, 0.5), 10: (0.1, 0.2, 0.5, 1.0), 30: (0.1, 0.2, 0.5, 1.0),
        30.5: (0.15, 0.3, 0.8, 1.5), 100: (0.15, 0.3, 0.8, 1.5), 120: (0.15, 0.3, 0.8, 1.5),
        250: (0.2, 0.5, 1.2, 2.5), 400: (0.2, 0.5, 1.2, 2.5), 800: (0.3, 0.8, 2.0, 4.0),
        1500: (0.5, 1.2, 3.0, 6.0), 3000: (None, 2.0, 4.0, 8.0),
    }
    for nominal, expected in cases.items():
        assert tuple(df.iso2768_tolerance(nominal, c) for c in "fmcv") == expected, nominal


def test_iso2768_out_of_range_and_bad_input():
    for bad in (0.5, 0.1, 0, -4, float("nan"), float("inf"), 4001):
        assert df.iso2768_tolerance(bad, "m") is None
    with pytest.raises(ValueError):
        df.iso2768_tolerance(10, "x")


def test_tolerance_rows_skip_ranges_a_class_does_not_cover():
    assert len(df.tolerance_rows("m")) == 8 and len(df.tolerance_rows("f")) == 7
    assert df.tolerance_rows("m")[2] == ("6 to 30", "\u00b10.2")


# ------------------------------------------------------------ hole labels ---

def test_numbers_are_formatted_without_noise():
    assert [df.fmt_number(v) for v in (5.0, 5.25, 5.1, 0.0, 12.3456, 100)] == ["5", "5.25", "5.1", "0", "12.35", "100"]


def test_hole_label_has_counts_and_never_claims_thru():
    assert df.hole_label(5.0, 4) == "4X \u00d85" and df.hole_label(3.2, 1) == "\u00d83.2"
    assert "THRU" not in df.hole_label(5, 9)


def test_hole_groups_count_by_diameter():
    circles = [(10, 10, 2.5), (50, 10, 2.5), (10, 40, 2.5), (50, 40, 2.52), (30, 25, 6)]
    groups = df.hole_groups(circles)
    assert groups == [(10, 10, 2.5, 4), (30, 25, 6, 1)]
    assert df.hole_groups([]) == []


def test_center_marks_cross_at_the_centre_and_overshoot_the_rim():
    (a, b), (c, d) = df.center_mark(10, 20, 3)
    assert a == (5, 20) and b == (15, 20) and c == (10, 15) and d == (10, 25)


# ------------------------------------------------------------- the layout ---

BOM = {"rows": [
    {"item": 1, "names": ["plate"], "quantity": 1, "part_type": "flat_plate", "material": "6061 aluminum", "total_mass_g": 108.0},
    {"item": 2, "names": ["post_1", "post_2"], "quantity": 2, "part_type": "spacer", "material": None, "total_mass_g": None}],
    "part_count": 3, "total_mass_g": 108.0, "mass_is_complete": False}


def texts(prims):
    return [p[5] for p in prims if p[0] == "text"]


def test_footer_without_a_bom_has_notes_and_tolerances_only():
    f = df.build_footer(None, "m")
    assert f.table_title is None and not any("MASS" in n for n in f.notes)
    prims, w, h = df.layout_footer(f)
    t = texts(prims)
    assert "NOTES" in t and "GENERAL TOLERANCES ISO 2768-m" in t and "PARTS LIST" not in t
    assert any("THIRD ANGLE" in x for x in t) and any("\u00b10.2" == x for x in t)


def test_footer_with_a_bom_lists_every_row_and_flags_partial_mass():
    prims, w, h = df.layout_footer(df.build_footer(BOM, "c"))
    t = texts(prims)
    assert "PARTS LIST" in t and "plate (flat_plate)" in t and "post_1 +1 (spacer)" in t
    assert "108" in t and "-" in t
    assert any(x.startswith("TOTAL 3 PARTS, 108 g (PARTIAL)") for x in t)
    assert "GENERAL TOLERANCES ISO 2768-c" in t


def test_every_primitive_sits_inside_the_footer_and_text_fits_its_block():
    for bom in (None, BOM):
        prims, w, h = df.layout_footer(df.build_footer(bom))
        for p in prims:
            xs = [p[1], p[3]] if p[0] == "line" else [p[1]]
            ys = [p[2], p[4]] if p[0] == "line" else [p[2]]
            assert all(-1e-6 <= x <= w + 1e-6 for x in xs) and all(-1e-6 <= y <= h + 1e-6 for y in ys), p
        # no two text items share a position
        pos = [(round(p[1], 3), round(p[2], 3)) for p in prims if p[0] == "text"]
        assert len(pos) == len(set(pos))


def test_min_width_pads_the_footer():
    f = df.build_footer(None)
    _, w, _ = df.layout_footer(f)
    assert df.layout_footer(f, min_width=w + 100)[1] == w + 100


def test_long_names_are_clipped_in_the_parts_list():
    long_bom = {**BOM, "rows": [{**BOM["rows"][0], "names": ["x" * 200]}]}
    cells = df.build_footer(long_bom).table_rows[0]
    assert len(cells[2]) <= 46 and len(cells[3]) <= 22


# --------------------------------------------- a real PDF from synthetic views ---

def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]


def ring(cx, cy, r, n=32):
    pts = [(cx + r * math.cos(2 * math.pi * i / (n - 1)), cy + r * math.sin(2 * math.pi * i / (n - 1))) for i in range(n)]
    pts[-1] = pts[0]
    return pts


def synthetic_views():
    import exporters

    def view(name, label, w, h, holes):
        lines = [rect(0, 0, w, h)] + [ring(cx, cy, r) for cx, cy, r in holes]
        return exporters._View(name=name, label=label, visible=lines, hidden=[], u_min=0, u_max=w, v_min=0, v_max=h, hole_circles=list(holes))

    holes = [(10, 10, 2.5), (50, 10, 2.5), (10, 30, 2.5), (50, 30, 2.5), (30, 20, 6)]
    return [view("front", "FRONT VIEW", 60, 5, []), view("top", "TOP VIEW", 60, 40, holes), view("side", "RIGHT SIDE VIEW", 40, 5, [])]


def make_pdf(bom=None):
    pytest.importorskip("reportlab")
    import reportlab.rl_config as cfg
    import exporters

    cfg.pageCompression = 0                       # so the text can be found in the file
    out = Path(tempfile.mkdtemp()) / "d.pdf"
    with mock.patch.object(exporters, "_build_three_views", lambda wp: synthetic_views()), \
         mock.patch.object(exporters, "_as_workplane", lambda o: o):
        exporters.export_pdf(object(), out, part_name="plate", material="6061 aluminum", part_type="flat_plate", bom=bom)
    return out.read_bytes()


def test_pdf_has_hole_counts_notes_and_the_tolerance_table():
    data = make_pdf()
    assert data.startswith(b"%PDF")
    # PDF escapes the diameter sign as octal \330. Four 5 mm holes are one "4X" call-out; the lone 12 mm hole is not.
    assert rb"(4X \3305)" in data and rb"(\33012)" in data
    assert b"(NOTES)" in data and b"ISO 2768-m" in data and b"THIRD ANGLE" in data


def test_pdf_with_a_bom_has_the_parts_list():
    data = make_pdf(BOM)
    assert b"(PARTS LIST)" in data and b"plate \\(flat_plate\\)" in data and b"PARTIAL" in data


def test_pdf_page_is_taller_with_the_footer_than_the_drawing_alone():
    sizes = {}
    for key, bom in (("plain", None), ("bom", BOM)):
        m = re.search(rb"/MediaBox \[ ?0 ?0 ?([\d.]+) ([\d.]+) ?\]", make_pdf(bom))
        sizes[key] = float(m.group(2))
    assert sizes["bom"] > sizes["plain"] - 1e-6 and sizes["plain"] > 150


# ------------------------------------------------------ leader direction ---

def test_leader_prefers_45_degrees_when_clear():
    assert df.leader_angle(50, 50, 3, [(50, 50, 3)], (0, 100, 0, 100)) == 45.0


def test_leader_avoids_a_neighbouring_hole():
    # a large hole sits up and to the right, right where a 45 degree label would go
    angle = df.leader_angle(10, 10, 2.5, [(10, 10, 2.5), (22, 22, 6)], (0, 60, 0, 40))
    assert angle != 45.0
    lx, ly = 10 + 14.5 * math.cos(math.radians(angle)), 10 + 14.5 * math.sin(math.radians(angle))
    assert math.hypot(lx - 22, ly - 22) - 6 > 5


def test_leader_stays_inside_the_view():
    angle = df.leader_angle(58, 38, 2, [(58, 38, 2)], (0, 60, 0, 40))     # hole in the top-right corner
    lx, ly = 58 + 14 * math.cos(math.radians(angle)), 38 + 14 * math.sin(math.radians(angle))
    assert lx <= 62 and ly <= 42 and angle in (135.0, 225.0, 180.0, 270.0)


def test_footer_scale_note_follows_the_drawing_scale():
    assert any("SCALE 1:50." in n for n in df.build_footer(None, "m", "1:50").notes)
    assert any("SCALE 1:1." in n for n in df.build_footer(None).notes)
