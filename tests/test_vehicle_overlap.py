"""The kernel-free overlap check finds the volume two parts share, and says what it cannot model."""
import json
from pathlib import Path

import pytest

import vehicle_overlap as VO
import vehicle_spec as VS

ROOT = Path(__file__).resolve().parents[1]


def box(name, x, size=100, y=0, z=0):
    return {"name": name, "part_type": "simple_box", "parameters": {"length_mm": size, "width_mm": size, "height_mm": size},
            "position": [x, y, z], "rotation": [0, 0, 0]}


def test_two_boxes_overlapping_by_ten_millimetres_share_exactly_that_volume():
    r = VO.find_overlaps([box("a", 0), box("b", 90)])
    assert r["overlaps"] == [{"a": "a", "b": "b", "volume_mm3": pytest.approx(100000.0)}]


def test_boxes_that_only_touch_do_not_overlap():
    assert VO.find_overlaps([box("a", 0), box("b", 100)])["overlaps"] == []


def test_a_rotated_shaft_is_placed_the_way_the_assembly_builder_places_it():
    shaft = {"name": "s", "part_type": "shaft", "parameters": {"diameter_mm": 20, "length_mm": 200},
             "position": [-200, 0, 0], "rotation": [0, 90, 0]}            # local +Z becomes +X, so it runs x = -200 .. 0
    r = VO.find_overlaps([box("a", 0), shaft])
    assert r["overlaps"][0]["volume_mm3"] == pytest.approx(50 * 3.14159 * 100, rel=0.03)
    up = dict(shaft, rotation=[-90, 0, 0], position=[0, -300, 0])           # local +Z becomes +Y, runs y = -300 .. -100
    assert VO.find_overlaps([box("a", 0), up])["overlaps"] == []


def test_a_hollow_box_is_open_at_the_top_and_empty_inside():
    tub = {"name": "tub", "part_type": "simple_box", "position": [0, 0, 0], "rotation": [0, 0, 0],
           "parameters": {"length_mm": 100, "width_mm": 100, "height_mm": 100, "hollow": True, "wall_thickness_mm": 5}}
    inside = {"name": "pin", "part_type": "shaft", "parameters": {"diameter_mm": 10, "length_mm": 30},
              "position": [0, 0, -20], "rotation": [0, 0, 0]}
    assert VO.find_overlaps([tub, inside])["overlaps"] == []
    on_floor = dict(inside, position=[0, 0, -48])
    assert VO.find_overlaps([tub, on_floor])["overlaps"]                    # sunk into the 5 mm floor


def test_a_meshing_gear_pair_is_not_an_overlap_but_two_gears_on_one_spot_are():
    g = lambda n, x: {"name": n, "part_type": "gear", "position": [x, 0, 0], "rotation": [0, 0, 0],
                      "parameters": {"module": 3, "teeth": 20, "thickness_mm": 10, "bore_diameter_mm": 10}}
    assert VO.find_overlaps([g("a", 0), g("b", 60)])["overlaps"] == []     # centre distance = module x teeth
    assert VO.find_overlaps([g("a", 0), g("b", 30)])["overlaps"]


def test_unknown_shapes_are_listed_not_silently_clean():
    odd = {"name": "rod", "part_type": "connecting_rod", "parameters": {}, "position": [0, 0, 0], "rotation": [0, 0, 0]}
    r = VO.find_overlaps([box("a", 0), odd])
    assert r["unmodelled"] == ["rod"] and r["modelled"] == 1


def test_pairs_that_are_meant_to_touch_can_be_skipped():
    assert VO.find_overlaps([box("a", 0), box("b", 90)], skip_pairs=lambda a, b: True)["overlaps"] == []


def test_a_wrongly_sized_panel_is_caught_in_the_real_body():
    spec = {"name": "t", "components": [{"id": "panels", "module": "car_body", "assembly": "body", "position": [0, 0, 0],
                                         "params": {}}]}
    ex = VS.expand_vehicle(spec)
    assert VO.find_overlaps(ex["parts"])["overlaps"] == []
    roof = next(p for p in ex["parts"] if p["name"] == "panels_roof")
    roof["parameters"]["features"][0]["profile"]["height"] += 60            # a roof wider than the glass beside it
    hits = VO.find_overlaps(ex["parts"])["overlaps"]
    assert {h["a"] for h in hits} | {h["b"] for h in hits} >= {"panels_roof", "panels_side_window_l"}
