"""Building kernel: spec, layout, 3D parts, schedules, drawings. Pure Python (no CadQuery)."""
import json
from xml.etree import ElementTree as ET

import pytest

from building import design_building, normalize_building_spec, stair_geometry
from building.drawings import plan_svg
from building.model3d import build_parts
from building.schedule import build_schedules, schedule_csv
from feature_tree import validate_feature_tree

HOUSE = {"name": "Two-storey house", "width_mm": 13000, "depth_mm": 10000, "levels": [
    {"rooms": [{"type": "living"}, {"type": "dining"}, {"type": "kitchen"},
               {"type": "bedroom", "name": "Guest bedroom"}, {"type": "toilet"}, {"type": "store"}]},
    {"rooms": [{"type": "master_bedroom"}, {"type": "bedroom", "count": 2}, {"type": "bathroom", "count": 2}]}]}
BUNGALOW = {"name": "Bungalow", "width_mm": 12000, "depth_mm": 9000, "levels": [
    {"rooms": [{"type": "living"}, {"type": "kitchen"}, {"type": "bedroom", "count": 2}, {"type": "bathroom"}]}]}


@pytest.fixture(scope="module")
def house():
    r = design_building(HOUSE)
    assert r["ok"], r["errors"]
    return r["model"]


class TestSpec:
    def test_defaults_are_recorded_as_assumptions(self):
        spec, errors, assumptions = normalize_building_spec(HOUSE)
        assert errors == [] and spec["ext_wall_mm"] == 225 and spec["int_wall_mm"] == 150
        text = " ".join(assumptions)
        assert "225 mm" in text and "Gable roof" in text and "assumed default" in text
        assert [r["name"] for r in spec["levels"][1]["rooms"]][:3] == ["Master bedroom", "Bedroom 1", "Bedroom 2"]

    def test_stated_values_are_not_listed_as_assumptions(self):
        raw = dict(HOUSE, floor_height_mm=3200, ext_wall_mm=230)
        _, errors, assumptions = normalize_building_spec(raw)
        assert errors == [] and not any("Floor-to-floor" in a or "External walls" in a for a in assumptions)

    @pytest.mark.parametrize("change,expect", [
        ({"width_mm": 9000, "depth_mm": 12000}, "longer side"),
        ({"width_mm": 100}, "width"),
        ({"levels": []}, "levels"),
        ({"levels": [{"rooms": [{"type": "bedroom"}]}]}, "living room"),
        ({"levels": [{"rooms": [{"type": "living"}, {"type": "sauna"}]}]}, "type must be"),
        ({"levels": [{"rooms": [{"type": "living", "count": 99}]}]}, "count"),
        ({"floor_height_mm": 9000}, "floor_height_mm"),
        ({"roof": {"type": "dome"}}, "roof.type"),
        ({"levels": [{"rooms": [{"type": "living", "area_m2": -4}]}]}, "area_m2"),
    ])
    def test_rejections(self, change, expect):
        _, errors, _ = normalize_building_spec(dict(HOUSE, **change))
        assert any(expect in e for e in errors), errors

    def test_not_a_dict(self):
        assert normalize_building_spec("a house")[1]


class TestStair:
    def test_riser_and_tread_are_within_limits(self):
        for fh in (2700, 3000, 3200, 3500, 4000):
            s = stair_geometry(fh)
            assert s["riser"] <= 190 and s["tread"] >= 250 and s["treads"] == s["risers"] - 1
            assert s["risers"] * s["riser"] == pytest.approx(fh)

    def test_3000_mm_floor_gives_17_risers(self):
        s = stair_geometry(3000)
        assert s["risers"] == 17 and s["run"] == 4000


class TestLayout:
    def test_example_house_is_laid_out(self, house):
        assert len(house["levels"]) == 2
        assert house["compliance"]["building_code"] == "UNKNOWN"
        assert len(house["levels"][0]["rooms"]) == 7 and len(house["levels"][1]["rooms"]) == 6

    def test_rooms_tile_each_band_without_overlap_and_inside_the_footprint(self, house):
        g = house["geometry"]
        for lv in house["levels"]:
            for band in ("south", "north"):
                rooms = sorted((r for r in lv["rooms"] if r["band"] == band), key=lambda r: r["net"][0])
                assert rooms
                for r in rooms:
                    x0, y0, x1, y1 = r["net"]
                    assert g["e"] <= x0 < x1 <= g["W"] - g["e"] and g["e"] <= y0 < y1 <= g["D"] - g["e"]
                for a, b in zip(rooms, rooms[1:]):
                    assert b["net"][0] - a["net"][2] == pytest.approx(g["i"], abs=0.5)  # exactly one partition between rooms
                assert rooms[-1]["net"][2] == pytest.approx(g["W"] - g["e"], abs=0.5)  # fills to the east wall
                assert rooms[0]["net"][0] == pytest.approx(g["e"], abs=0.5)

    def test_bands_do_not_overlap_the_corridor(self, house):
        g = house["geometry"]
        for lv in house["levels"]:
            for r in lv["rooms"]:
                if r["band"] == "south":
                    assert r["net"][3] <= g["yc0"] - g["i"] / 2 + 0.5
                else:
                    assert r["net"][1] >= g["yc1"] + g["i"] / 2 - 0.5

    def test_every_room_is_reachable_from_the_corridor(self, house):
        for lv in house["levels"]:
            reach = {o["room"] for o in lv["openings"] if o["wall"] in ("cor_s", "cor_n") and o["kind"] in ("door", "opening")}
            assert {r["id"] for r in lv["rooms"]} == reach

    def test_exactly_one_entrance_door_in_the_living_room(self, house):
        doors = [o for lv in house["levels"] for o in lv["openings"] if o["wall"] == "ext_s" and o["kind"] == "door"]
        assert len(doors) == 1
        living = next(r for r in house["levels"][0]["rooms"] if r["type"] == "living")
        assert doors[0]["room"] == living["id"] and doors[0]["width"] == 1000

    def test_every_habitable_room_has_a_window(self, house):
        for lv in house["levels"]:
            for r in lv["rooms"]:
                if r["type"] in ("living", "dining", "kitchen", "bedroom", "master_bedroom"):
                    assert any(o["room"] == r["id"] and o["kind"] == "window" for o in lv["openings"]), r["name"]

    def test_openings_fit_their_walls_and_never_overlap(self, house):
        g = house["geometry"]
        for lv in house["levels"]:
            for w in lv["walls"]:
                length = abs(w["b"][0] - w["a"][0]) + abs(w["b"][1] - w["a"][1])
                ops = sorted(w["openings"], key=lambda o: o["offset"])
                for o in ops:
                    assert o["offset"] >= 100 and o["offset"] + o["width"] <= length - 100
                    assert o["sill"] + o["height"] <= g["h_w"] - 100
                for a, b in zip(ops, ops[1:]):
                    assert b["offset"] - (a["offset"] + a["width"]) >= 100 - 1e-6

    def test_stair_sits_inside_the_hall_with_landings(self, house):
        st = house["levels"][0]["stair"]
        hall = next(r for r in house["levels"][0]["rooms"] if r["type"] == "stair_hall")
        hx0, hy0, hx1, hy1 = hall["net"]
        assert hx0 <= st["x0"] and st["x1"] <= hx1 and hy0 <= st["y0"] and st["y1"] <= hy1
        assert st["x0"] - hx0 >= 900 - 1e-6 and hx1 - st["x1"] >= 1000 - 1e-6
        assert st["y1"] - st["y0"] == 1000 and house["levels"][1]["stair"] is None

    def test_deterministic(self):
        a = json.dumps(design_building(HOUSE), sort_keys=True, default=str)
        b = json.dumps(design_building(HOUSE), sort_keys=True, default=str)
        assert a == b

    def test_single_storey_has_no_stair(self):
        r = design_building(BUNGALOW)
        assert r["ok"], r["errors"]
        assert all(lv["stair"] is None for lv in r["model"]["levels"])
        assert not any(room["type"] == "stair_hall" for lv in r["model"]["levels"] for room in lv["rooms"])

    def test_three_storeys(self):
        raw = dict(HOUSE, levels=HOUSE["levels"] + [{"rooms": [{"type": "bedroom", "count": 2}, {"type": "bathroom", "count": 2}]}])
        r = design_building(raw)
        assert r["ok"], r["errors"]
        assert [bool(lv["stair"]) for lv in r["model"]["levels"]] == [True, True, False]

    def test_oversized_program_is_refused_with_a_reason(self):
        raw = dict(HOUSE, width_mm=9000, depth_mm=7000)
        r = design_building(raw)
        assert not r["ok"] and r["model"] is None
        assert any("minimum" in e or "stair hall" in e or "deep" in e for e in r["errors"]), r["errors"]

    def test_too_shallow_footprint_is_refused(self):
        r = design_building(dict(BUNGALOW, depth_mm=6000))
        assert not r["ok"] and any("deep" in e for e in r["errors"])

    def test_empty_band_is_reported_when_it_cannot_be_balanced(self):
        raw = {"width_mm": 12000, "depth_mm": 9000, "levels": [{"rooms": [{"type": "living"}]}]}
        r = design_building(raw)
        assert not r["ok"] and any("north band" in e for e in r["errors"])

    def test_bands_are_balanced_by_moving_rooms_to_the_lighter_side(self):
        raw = {"width_mm": 12000, "depth_mm": 9000, "levels": [{"rooms": [{"type": "living"}, {"type": "kitchen"}]}]}
        r = design_building(raw)
        assert r["ok"], r["errors"]
        assert {room["band"] for room in r["model"]["levels"][0]["rooms"]} == {"south", "north"}

    def test_a_generous_footprint_warns_that_rooms_grew(self):
        r = design_building(dict(BUNGALOW, width_mm=20000, depth_mm=14000))
        assert r["ok"] and any("larger than requested" in w for w in r["warnings"])


class TestModel3D:
    def test_every_part_passes_the_strict_feature_tree_validator(self, house):
        parts = build_parts(house)
        assert [p["name"] for p in parts] == ["ground_slab", "level_0_walls", "slab_1", "stair_0",
                                              "level_1_walls", "slab_2", "gable_west", "gable_east", "roof"]
        for p in parts:
            feats, errors = validate_feature_tree(p["parameters"]["features"])
            assert errors == [], (p["name"], errors)
            assert len(feats) <= 60 and feats[0]["mode"] == "new"

    def test_wall_extents_match_the_plan(self, house):
        g = house["geometry"]
        walls = {w["id"]: w for w in house["levels"][0]["walls"]}
        feats = {f["id"]: f for f in build_parts(house)[1]["parameters"]["features"]}
        for wid, f in feats.items():
            w = walls[wid]
            t = w["t"]
            if f["plane"] == "XZ":      # extrudes toward -Y from y = -offset
                y_hi = -f["offset"]
                assert (y_hi - f["distance"], y_hi) == pytest.approx((w["a"][1] - t / 2, w["a"][1] + t / 2))
            else:                        # YZ extrudes toward +X from x = offset
                assert (f["offset"], f["offset"] + f["distance"]) == pytest.approx((w["a"][0] - t / 2, w["a"][0] + t / 2))
            zs = [p[1] for p in f["profile"]["points"]]
            assert min(zs) == 0 and max(zs) == g["h_w"]

    def test_doors_are_notches_and_windows_are_holes(self, house):
        feats = {f["id"]: f for f in build_parts(house)[1]["parameters"]["features"]}
        walls = {w["id"]: w for w in house["levels"][0]["walls"]}
        for wid, f in feats.items():
            doors = [o for o in walls[wid]["openings"] if o["kind"] in ("door", "opening")]
            wins = [o for o in walls[wid]["openings"] if o["kind"] == "window"]
            assert len(f["profile"]["points"]) == 4 + 4 * len(doors)
            assert len(f["profile"].get("holes", [])) == len(wins)

    def test_slab_void_matches_the_stair_and_only_where_needed(self, house):
        slabs = {p["name"]: p["parameters"]["features"][0] for p in build_parts(house) if p["name"].startswith(("slab", "ground"))}
        st = house["levels"][0]["stair"]
        hole = slabs["slab_1"]["profile"]["holes"][0]
        assert hole["width"] == pytest.approx(st["x1"] - st["x0"]) and hole["height"] == pytest.approx(st["y1"] - st["y0"])
        assert "holes" not in slabs["slab_2"]["profile"] and "holes" not in slabs["ground_slab"]["profile"]

    def test_stair_reaches_the_next_floor_exactly(self, house):
        flight = next(p for p in build_parts(house) if p["name"] == "stair_0")["parameters"]["features"][0]
        zs = [p[1] for p in flight["profile"]["points"]]
        assert min(zs) == 0 and max(zs) == pytest.approx(house["geometry"]["fh"])

    def test_roof_apex_height(self, house):
        import math
        g = house["geometry"]
        roof = next(p for p in build_parts(house) if p["name"] == "roof")["parameters"]["features"][0]
        zs = [p[1] for p in roof["profile"]["points"]]
        expected = 2 * g["fh"] + (g["D"] / 2) * math.tan(math.radians(25))
        assert max(zs) == pytest.approx(expected)
        assert roof["distance"] == pytest.approx(g["W"] + 2 * 450)

    def test_flat_roof_has_no_roof_part(self):
        raw = dict(BUNGALOW, roof={"type": "flat"})
        parts = build_parts(design_building(raw)["model"])
        assert "roof" not in [p["name"] for p in parts]


class TestSchedulesAndDrawings:
    def test_schedule_counts_match_the_model(self, house):
        s = build_schedules(house)
        total = sum(len(lv["openings"]) for lv in house["levels"])
        assert sum(r["count"] for k in ("doors", "windows", "openings") for r in s[k]) == total
        assert s["quantities"]["doors"] == sum(r["count"] for r in s["doors"])
        marks = [r["mark"] for k in ("doors", "windows", "openings") for r in s[k]]
        assert len(marks) == len(set(marks))
        assert s["doors"][0]["width"] == 1000 and s["doors"][0]["count"] == 1  # the entrance is its own type

    def test_room_schedule_lists_every_room(self, house):
        s = build_schedules(house)
        assert len(s["rooms"]) == sum(len(lv["rooms"]) for lv in house["levels"])
        csv = schedule_csv(s)
        assert csv.startswith("MARK,TYPE") and "LEVEL,ROOM" in csv and "Master bedroom" in csv

    def test_quantities_are_plausible(self, house):
        q = build_schedules(house)["quantities"]
        assert 200 < q["gross_floor_area_m2"] < 260 and q["roof_area_m2"] > 13 * 10
        assert "Approximate" in q["note"]

    def test_plans_are_well_formed_svg_with_every_room_and_door_swing(self, house):
        for k, lv in enumerate(house["levels"]):
            svg = plan_svg(house, k)
            root = ET.fromstring(svg)
            assert root.tag.endswith("svg")
            for r in lv["rooms"]:
                assert r["name"] in svg
            swings = sum(1 for o in lv["openings"] if o["kind"] == "door" and o["swing"])
            assert svg.count("<path d=") == swings
            assert "CONCEPT" in svg and "UNKNOWN" in svg

    def test_upper_plan_shows_the_stair_coming_down(self, house):
        assert "DN" in plan_svg(house, 1) and "UP" in plan_svg(house, 0)
