"""A building as the Studio sees it: flat parameters in, checked building and drawing set out."""
import subprocess
import tempfile
from pathlib import Path

import pytest

from building.drawing_set import building_sheets, write_building_pdf
from building.flat import resolve_flat
from validator import validate_parameters

TWO_STOREY = {"floors": 2, "bedrooms": 3, "bathrooms": 2, "dining_room": 1}


class TestFlatParameters:
    def test_a_short_description_lays_out_and_fills_in_the_footprint(self):
        r = resolve_flat(TWO_STOREY)
        assert r["ok"] and r["corrections"]["width_mm"][0] == "auto"
        assert r["spec"]["width_mm"] >= r["spec"]["depth_mm"]
        assert len(r["model"]["levels"]) == 2

    def test_a_stated_footprint_is_respected_not_overwritten(self):
        r = resolve_flat({**TWO_STOREY, "width_mm": 14000, "depth_mm": 10000})
        assert r["ok"] and r["corrections"] == {} and r["model"]["geometry"]["W"] == 14000

    def test_a_footprint_that_is_too_small_is_refused_with_a_suggestion(self):
        r = resolve_flat({**TWO_STOREY, "width_mm": 9000, "depth_mm": 7000})
        assert not r["ok"] and "smallest footprint that fits" in r["errors"][-1]

    def test_a_floor_with_fewer_rooms_does_not_stop_the_footprint_search(self):
        # Two floors share one footprint. The upper floor (master, two bedrooms, two bathrooms) sets its
        # size, so the three ground-floor rooms come out larger than requested. That is a note, not a failure.
        r = resolve_flat({"floors": 2, "bedrooms": 3, "bathrooms": 2})
        assert r["ok"] and r["corrections"]["width_mm"][0] == "auto"
        assert any("larger than requested" in w and "floor with the most rooms" in w for w in r["warnings"])

    def test_a_program_that_cannot_fit_names_the_room_that_blocks_it(self):
        r = resolve_flat({"floors": 1, "bedrooms": 8, "bathrooms": 6, "toilets": 4, "study": 1, "store": 1,
                          "dining_room": 1, "master_bedroom": 1})
        assert not r["ok"]
        msg = r["errors"][0]
        assert "No footprint up to" in msg and "would be" in msg and "minimum" in msg
        assert "Remove a room or enlarge the footprint" not in msg

    def test_room_sizes_are_treated_as_minimums_when_choosing_the_footprint(self):
        from building.intake import search_footprint

        found = search_footprint(resolve_flat({"floors": 2, "bedrooms": 3, "bathrooms": 2})["spec"] | {})
        assert found["footprint"] is not None and found["under"] == [] and found["blockers"] == []

    @pytest.mark.parametrize("bad", [{"floors": 9}, {"floors": 0}, {"floors": 1.5}, {"bedrooms": -1}, {"roof_type": "dome"},
                                     {"study": 3}, {"floors": True}])
    def test_bad_values_are_refused(self, bad):
        assert not resolve_flat({**TWO_STOREY, **bad})["ok"]

    def test_defaults_alone_make_a_valid_small_house(self):
        assert resolve_flat({})["ok"]

    def test_an_edit_from_the_chat_changes_the_building(self):
        a = resolve_flat(TWO_STOREY)["model"]
        b = resolve_flat({**TWO_STOREY, "bedrooms": 4, "width_mm": 15000, "depth_mm": 10000})
        assert b["ok"] and sum(len(lv["rooms"]) for lv in b["model"]["levels"]) > sum(len(lv["rooms"]) for lv in a["levels"])

    def test_flat_parameters_are_plain_scalars_so_the_studio_can_show_and_edit_them(self):
        r = resolve_flat(TWO_STOREY)
        assert all(isinstance(v, (int, float, str, bool)) for v in {**TWO_STOREY, "width_mm": r["spec"]["width_mm"]}.values())


class TestValidatorHook:
    def test_validate_parameters_returns_the_footprint_as_a_correction(self):
        corrected, result = validate_parameters("building", TWO_STOREY)
        assert result.is_valid and corrected["width_mm"] > 0
        assert result.corrections["width_mm"]["old"] == "auto"      # the Studio shows "width_mm: auto -> 12500"

    def test_invalid_buildings_are_rejected_before_any_geometry_is_built(self):
        _, result = validate_parameters("building", {**TWO_STOREY, "width_mm": 9000, "depth_mm": 7000})
        assert not result.is_valid and result.errors


class TestDrawingSet:
    def test_eight_sheets_for_a_two_storey_house(self):
        model = resolve_flat(TWO_STOREY)["model"]
        titles = [s.title for s in building_sheets(model)]
        assert len(titles) == 8 and titles[0] == "GROUND FLOOR PLAN" and "SECTION A-A" in titles

    def test_the_pdf_has_one_page_per_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "set.pdf"
            write_building_pdf(p, TWO_STOREY)
            assert p.read_bytes().startswith(b"%PDF")
            out = subprocess.run(["qpdf", "--show-npages", str(p)], capture_output=True, text=True)
            if out.returncode == 0:
                assert out.stdout.strip() == "8"


def test_the_building_template_is_registered_and_builds_one_compound():
    cq = pytest.importorskip("cadquery")
    from cad_templates import TEMPLATES
    assert "building" in TEMPLATES and "asme_flange" in TEMPLATES and "flange" in TEMPLATES   # their flange is still there
    wp = TEMPLATES["building"]({"floors": 1, "bedrooms": 2, "bathrooms": 1})
    solids = wp.solids().vals()
    assert len(solids) >= 5 and all(s.isValid() and s.Volume() > 0 for s in solids)
    bb = wp.val().BoundingBox()
    assert bb.zmin == pytest.approx(-200.0, abs=1.0) and bb.xlen > 9000
