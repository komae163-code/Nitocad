"""Architecture domain: drawings, furniture, compliance, foundations, intake, package. Pure Python."""
import hashlib
import json
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from building import design_building
from building.compliance import CONCEPT_RULESET, UNASSESSED, evaluate, load_ruleset
from building.furniture import LIB, _Room, furniture_parts, furniture_schedule, place_furniture
from building.intake import confirm, extract_with_llm, intake, parse_description, suggest_footprint
from building.package import generate_package
from building.sheet import STANDARD_SCALES, choose_layout, fit, to_pdf, to_svg
from building.sheets import elevation_sheet, plan_sheet, section_defs, section_sheet
from building.structure import foundation_parts, preliminary_foundation
from feature_tree import validate_feature_tree

HOUSE = {"name": "Two-storey house", "width_mm": 13000, "depth_mm": 10000, "levels": [
    {"rooms": [{"type": "living"}, {"type": "dining"}, {"type": "kitchen"},
               {"type": "bedroom", "name": "Guest bedroom"}, {"type": "toilet"}, {"type": "store"}]},
    {"rooms": [{"type": "master_bedroom"}, {"type": "bedroom", "count": 2}, {"type": "bathroom", "count": 2}]}]}
BUNGALOW = {"name": "Bungalow", "width_mm": 14000, "depth_mm": 9000, "levels": [
    {"rooms": [{"type": "living"}, {"type": "kitchen"}, {"type": "bedroom", "count": 2}, {"type": "bathroom"}]}]}


@pytest.fixture(scope="module")
def house():
    return design_building(HOUSE)["model"]


@pytest.fixture(scope="module")
def bungalow():
    r = design_building(BUNGALOW)
    assert r["ok"], r["errors"]
    return r["model"]


@pytest.fixture(scope="module")
def furn(house):
    return place_furniture(house)


class TestSheets:
    def test_plans_reach_1_to_50_on_the_smallest_paper_that_allows_it(self, house):
        paper, scale = choose_layout(plan_sheet(house, 0))
        assert (paper, scale) == ("A2", 50)
        for side in ("south", "east", "north", "west"):
            assert choose_layout(elevation_sheet(house, side)) == ("A3", 50)
        for sd in section_defs(house):
            assert choose_layout(section_sheet(house, sd)) == ("A3", 50)

    def test_scale_is_always_a_standard_one_and_the_drawing_fits(self, house):
        for sh in (plan_sheet(house, 0), plan_sheet(house, 1), elevation_sheet(house, "south")):
            f = fit(sh, None)
            assert f["scale"] in STANDARD_SCALES and f["fits"]

    def test_every_sheet_is_well_formed_svg_with_scale_and_status(self, house):
        sheets = [plan_sheet(house, 0), plan_sheet(house, 1)] + [elevation_sheet(house, s) for s in ("south", "east", "north", "west")] \
            + [section_sheet(house, sd) for sd in section_defs(house)]
        for sh in sheets:
            svg = to_svg(sh)
            assert ET.fromstring(svg).tag.endswith("svg")
            assert "1:50" in svg and "UNKNOWN" in svg and "NOT FOR CONSTRUCTION" in svg

    def test_pdf_output_is_a_valid_pdf_with_the_title(self, house):
        pdf = to_pdf(plan_sheet(house, 0))
        assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 3000

    def test_walls_have_real_gaps_not_white_patches(self, house):
        sh = plan_sheet(house, 0)
        assert not any(e.get("fill") in ("white", "#ffffff") for e in sh.entities)
        lv = house["levels"][0]
        wall = next(w for w in lv["walls"] if w["id"] == "ext_s")
        poche = [e for e in sh.entities if e["t"] == "poly" and e["layer"] == "A-WALL"
                 and abs((e["pts"][0][1] + e["pts"][2][1]) / 2 - wall["a"][1]) < 1 and e["pts"][2][1] - e["pts"][0][1] == wall["t"]]
        covered = sum(e["pts"][2][0] - e["pts"][0][0] for e in poche)
        opened = sum(o["width"] for o in wall["openings"])
        assert covered == pytest.approx((wall["b"][0] - wall["a"][0]) - opened)

    def test_section_markers_only_on_the_ground_plan(self, house):
        count = lambda sh: sum(1 for e in sh.entities if e["layer"] == "A-ANNO-SECT")  # noqa: E731
        assert count(plan_sheet(house, 0)) >= 8 and count(plan_sheet(house, 1)) == 0

    def test_elevation_shows_every_window_and_door_of_its_facade(self, house):
        for side, wall in (("south", "ext_s"), ("north", "ext_n"), ("east", "ext_e"), ("west", "ext_w")):
            sh = elevation_sheet(house, side)
            glaz = sum(1 for e in sh.entities if e["t"] == "poly" and e["layer"] == "A-GLAZ")
            doors = sum(1 for e in sh.entities if e["t"] == "poly" and e["layer"] == "A-DOOR")
            ops = [o for lv in house["levels"] for o in lv["openings"] if o["wall"] == wall]
            assert glaz == sum(o["kind"] == "window" for o in ops) and doors == sum(o["kind"] == "door" for o in ops)

    def test_section_through_the_stair_cuts_the_flight_and_splits_the_slab(self, house):
        a = next(sd for sd in section_defs(house) if sd["id"] == "A-A")
        sh = section_sheet(house, a)
        assert any(e["t"] == "poly" and e["layer"] == "A-STRS" for e in sh.entities)
        g = house["geometry"]
        z0, z1 = g["h_w"], g["fh"]
        slab_pieces = [e for e in sh.entities if e["t"] == "poly" and e["layer"] == "A-SLAB"
                       and abs(e["pts"][0][1] - z0) < 1 and abs(e["pts"][2][1] - z1) < 1]
        assert len(slab_pieces) == 2      # the stair void splits the first floor slab

    def test_cross_section_does_not_cut_the_stair_flight_where_it_is_not(self, house):
        b = next(sd for sd in section_defs(house) if sd["id"] == "B-B")
        st = house["levels"][0]["stair"]
        assert not (st["x0"] <= b["at"] <= st["x1"])
        sh = section_sheet(house, b)
        assert not any(e["t"] == "poly" and e["layer"] == "A-STRS" for e in sh.entities)

    def test_single_storey_building_has_sections_and_no_stair(self, bungalow):
        for sd in section_defs(bungalow):
            sh = section_sheet(bungalow, sd)
            assert not any(e["layer"] == "A-STRS" for e in sh.entities)
        assert "A-A" in [sd["id"] for sd in section_defs(bungalow)]


class TestFurniture:
    def test_everything_is_inside_its_room_and_nothing_overlaps(self, house, furn):
        items, warnings = furn
        assert warnings == [] and len(items) > 25
        rooms = {r["id"]: r["net"] for lv in house["levels"] for r in lv["rooms"]}
        for it in items:
            x0, y0, x1, y1 = rooms[it["room_id"]]
            assert x0 - 1 <= it["x0"] < it["x1"] <= x1 + 1 and y0 - 1 <= it["y0"] < it["y1"] <= y1 + 1, it
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if a["room_id"] == b["room_id"]:
                    assert not (a["x0"] < b["x1"] - 1 and b["x0"] < a["x1"] - 1 and a["y0"] < b["y1"] - 1 and b["y0"] < a["y1"] - 1), (a, b)

    def test_nothing_blocks_a_door_swing(self, house, furn):
        items, _ = furn
        walls = {lv["index"]: {w["id"]: w for w in lv["walls"]} for lv in house["levels"]}
        for lv in house["levels"]:
            for o in lv["openings"]:
                if o["kind"] != "door" or not o["swing"]:
                    continue
                w = walls[lv["index"]][o["wall"]]
                d0, yw, into = w["a"][0] + o["offset"], w["a"][1], o["swing"]["into"][1]
                zy0, zy1 = (yw, yw + o["width"]) if into > 0 else (yw - o["width"], yw)
                for it in items:
                    if it["room_id"] == o["room"]:
                        assert not (it["x0"] < d0 + o["width"] - 1 and d0 + 1 < it["x1"] and it["y0"] < zy1 - 1 and zy0 + 1 < it["y1"]), (o["id"], it["name"])

    def test_rooms_get_the_furniture_they_need(self, house, furn):
        items, _ = furn
        kinds = {}
        for it in items:
            kinds.setdefault(it["room_id"], set()).add(it["kind"])
        for lv in house["levels"]:
            for r in lv["rooms"]:
                k = kinds.get(r["id"], set())
                if r["type"] in ("bedroom", "master_bedroom"):
                    assert {"wardrobe"} <= k and any(x.startswith("bed_") for x in k), r["name"]
                elif r["type"] == "bathroom":
                    assert {"wc", "basin", "shower"} <= k, r["name"]
                elif r["type"] == "toilet":
                    assert {"wc", "basin"} <= k
                elif r["type"] == "kitchen":
                    assert {"kitchen_run", "fridge"} <= k
                elif r["type"] == "living":
                    assert {"sofa", "tv_unit"} <= k
                elif r["type"] == "dining":
                    assert {"dining_table", "dining_chair"} <= k
                elif r["type"] == "stair_hall":
                    assert not k

    def test_schedule_counts(self, furn):
        items, _ = furn
        s = furniture_schedule(items)
        assert sum(s["totals"].values()) == len(items)
        assert s["plumbing_fixtures"]["WC"] == sum(1 for i in items if i["kind"] == "wc")
        assert s["totals"]["King bed"] == 1

    def test_a_piece_that_cannot_fit_is_refused_not_overlapped(self):
        room = _Room(0, {"id": "x", "name": "Tiny", "net": [0, 0, 1000, 1000], "type": "store"}, [], "N")
        assert room.place("bed_king", ["S", "E", "W"]) is None
        assert room.items == []

    def test_deterministic(self, house):
        a = json.dumps(place_furniture(house), sort_keys=True)
        assert a == json.dumps(place_furniture(house), sort_keys=True)

    def test_3d_furniture_parts_are_valid_feature_trees(self, house, furn):
        items, _ = furn
        parts = furniture_parts(items, house)
        assert len(parts) == len(items)
        for p in parts:
            assert validate_feature_tree(p["parameters"]["features"])[1] == []

    def test_library_dimensions_are_sane(self):
        for kind, (w, d, h, name, _) in LIB.items():
            assert 200 <= w <= 3000 and 200 <= d <= 2100 and 30 <= h <= 2100 and name


class TestCompliance:
    def test_concept_rules_never_claim_code_compliance(self, house):
        c = evaluate(house)
        assert c["counts"]["FAIL"] == 0 and c["counts"]["PASS"] > 30
        assert c["overall"] == "UNKNOWN" and c["building_code_compliance"] == "NOT_ASSESSED"
        assert set(c["unassessed_topics"]) == set(UNASSESSED) and "not a building code" in c["statement"]
        assert c["ruleset"]["authoritative"] is False

    def test_a_failing_rule_makes_the_overall_result_fail(self, house):
        rs, errs = load_ruleset({"name": "strict", "authoritative": False, "rules": [
            {"id": "S1", "title": "big bedrooms", "kind": "min_room_area", "by_type": {"bedroom": 40}}]})
        assert errs == []
        c = evaluate(house, rs)
        assert c["overall"] == "FAIL" and c["counts"]["FAIL"] >= 1
        assert all(r["required"] == 40 for r in c["rule_results"])

    def test_authoritative_rules_need_citations_and_overall_is_never_pass(self, house):
        cited = {"name": "X code", "authoritative": True, "jurisdiction": "Testland", "rules": [
            {"id": "X1", "title": "corridor", "kind": "min_corridor_width", "value": 900, "citation": "X Code s4.2"}]}
        rs, _ = load_ruleset(cited)
        c = evaluate(house, rs)
        assert c["counts"]["PASS"] == 1 and c["overall"] == "UNKNOWN" and c["building_code_compliance"] == "PARTIAL"
        uncited = {**cited, "rules": [{k: v for k, v in cited["rules"][0].items() if k != "citation"}]}
        rs2, _ = load_ruleset(uncited)
        c2 = evaluate(house, rs2)
        assert c2["counts"]["UNKNOWN"] == 1 and c2["counts"]["PASS"] == 0

    @pytest.mark.parametrize("bad", [None, {}, {"rules": []}, {"rules": [{"id": "a", "kind": "nope", "value": 1}]},
                                     {"rules": [{"id": "a", "kind": "min_corridor_width", "value": -3}]},
                                     {"rules": [{"id": "a", "kind": "min_corridor_width", "value": 1},
                                                {"id": "a", "kind": "min_corridor_width", "value": 2}]}, "not json"])
    def test_bad_rulesets_are_rejected(self, bad):
        rs, errs = load_ruleset(bad)
        assert rs is None and errs

    def test_json_text_and_files_load(self):
        text = json.dumps({"rules": [{"id": "a", "title": "t", "kind": "min_corridor_width", "value": 1000}]})
        assert load_ruleset(text)[1] == []
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text(text)
            assert load_ruleset(str(p))[1] == []

    def test_stair_rules_do_not_apply_to_a_single_storey(self, bungalow):
        c = evaluate(bungalow)
        assert {r["result"] for r in c["rule_results"] if r["rule_id"] in ("C4", "C5", "C6")} == {"NOT_APPLICABLE"}

    def test_shipped_rules_are_not_pretending_to_be_a_code(self):
        assert CONCEPT_RULESET["authoritative"] is False and CONCEPT_RULESET["jurisdiction"] is None
        assert all("citation" not in r for r in CONCEPT_RULESET["rules"])


class TestFoundation:
    def test_no_soil_data_means_no_estimate(self, house):
        r = preliminary_foundation(house)
        assert r["status"] == "UNKNOWN" and "Soil bearing" in r["reason"] and "footings" not in r
        assert foundation_parts(house, r) == []
        assert preliminary_foundation(house, 5)["status"] == "UNKNOWN"
        assert preliminary_foundation(house, 150, founding_depth_mm=300)["status"] == "UNKNOWN"

    def test_footing_width_matches_an_independent_hand_calculation(self, bungalow):
        import math
        q = 120.0
        r = preliminary_foundation(bungalow, q)
        assert r["status"] == "PRELIMINARY" and r["engineer_review_required"] is True
        g = bungalow["geometry"]
        ext_s = next(f for f in r["footings"] if f["wall"] == "ext_s")
        tw = (g["yc0"] - g["Y0"]) / 2000
        span = (g["D"] / 2 + 450) / 1000
        n = (g["h_w"] / 1000) * 3.0 + tw * (24 * g["slab"] / 1000) + tw * 0.25 + span * 1.0 + span * 0.5
        assert ext_s["service_line_load_kN_per_m"] == pytest.approx(n, abs=0.1)
        assert ext_s["width_mm"] == max(450.0, math.ceil(1.10 * n / q * 1000 / 50 - 1e-9) * 50)

    def test_weaker_soil_gives_wider_footings_and_minimums_hold(self, house):
        strong, weak = preliminary_foundation(house, 300), preliminary_foundation(house, 60, founding_depth_mm=1500)
        assert weak["status"] == "PRELIMINARY"
        for a, b in zip(strong["footings"], weak["footings"]):
            assert b["width_mm"] >= a["width_mm"] >= 450 and b["depth_mm"] >= a["depth_mm"] >= 225
        assert weak["footing_concrete_m3"] > strong["footing_concrete_m3"]
        assert any("not a design" in x for x in strong["limitations"]) and strong["assumptions"]

    def test_footing_parts_are_valid_and_the_plinth_reaches_ground_level(self, house):
        r = preliminary_foundation(house, 150)
        parts = foundation_parts(house, r)
        assert len(parts) == len(r["footings"])
        for p in parts:
            feats, errs = validate_feature_tree(p["parameters"]["features"])
            assert errs == []
            plinth = feats[1]
            assert plinth["offset"] + plinth["distance"] == pytest.approx(0.0)  # top of the plinth is ground level


class TestIntake:
    def test_the_reference_brief(self):
        p = parse_description("Design a two-storey residential building with three bedrooms, a living room, a dining room, "
                              "a kitchen, two bathrooms and a staircase.")
        lv = [[r["type"] for r in l["rooms"]] for l in p["spec"]["levels"]]
        assert len(lv) == 2 and lv[0][:3] == ["living", "dining", "kitchen"] and "toilet" in lv[0]
        assert lv[1].count("bedroom") + lv[1].count("master_bedroom") == 3 and lv[1].count("bathroom") == 2
        assert "master_bedroom" in lv[1]

    def test_counts_words_and_digits(self):
        assert parse_description("a 4 bedroom bungalow")["spec"]["levels"][0]["rooms"].count({"type": "bedroom"}) == 4
        master = parse_description("a 4 bedroom bungalow with a master bedroom")["spec"]["levels"][0]["rooms"]
        assert master.count({"type": "master_bedroom"}) == 1 and master.count({"type": "bedroom"}) == 3
        p = parse_description("five-bedroom three storey house")
        assert len(p["spec"]["levels"]) == 3

    def test_dimensions_and_units(self):
        assert parse_description("bungalow 12 x 9 m")["spec"]["width_mm"] == 12000
        feet = parse_description("bungalow 40 ft by 30 ft")["spec"]
        assert feet["width_mm"] == pytest.approx(12192) and feet["depth_mm"] == pytest.approx(9144)
        plot = parse_description("bungalow on a 50 by 100 plot")
        assert "width_mm" not in plot["spec"] and any("plot" in q for q in plot["questions"])

    def test_missing_information_becomes_assumptions_and_questions(self):
        p = parse_description("a house")
        assert any("floors" in q for q in p["questions"]) and any("bedrooms" in q for q in p["questions"])
        assert any("kitchen was added" in a for a in p["assumptions"])

    def test_the_proposal_is_never_auto_confirmed_and_the_layout_is_real(self):
        r = intake("two-storey house with three bedrooms, living room, dining room, kitchen and two bathrooms")
        assert r["needs_confirmation"] is True and r["confirmed"] is False and r["design_ok"]
        assert any("Footprint" in a for a in r["assumptions"]) and r["warnings"] == []
        assert design_building(r["spec"])["ok"]

    def test_a_footprint_that_does_not_fit_gets_a_suggestion(self):
        r = intake("3 bedroom bungalow with parlour, kitchen and store, 12m x 9m")
        assert not r["design_ok"] and r["suggested_footprint"]
        assert design_building({**r["spec"], **r["suggested_footprint"]})["ok"]

    def test_suggested_footprints_are_valid_and_compact(self):
        spec = parse_description("two-storey house with three bedrooms, living room, dining room, kitchen")["spec"]
        w, d = suggest_footprint(spec)
        assert d <= w <= 2.2 * d and design_building({**spec, "width_mm": w, "depth_mm": d})["ok"]

    def test_llm_route_validates_and_retries_with_the_exact_errors(self):
        seen = []

        def llm(system, user, images):
            seen.append(user)
            rooms = [{"type": "sauna"}] if len(seen) == 1 else [{"type": "living"}, {"type": "kitchen"}, {"type": "bedroom"}]
            return {"name": "H", "levels": [{"rooms": rooms}]}

        r = intake("small house", call_llm=llm)
        assert r["source"] == "llm" and len(seen) == 2 and "type must be" in seen[1]

    def test_llm_failure_falls_back_to_the_rule_parser_and_says_so(self):
        def boom(system, user, images):
            raise TimeoutError("down")

        r = intake("two-storey house with three bedrooms", call_llm=boom)
        assert r["source"] == "rules" and any("language model route failed" in a for a in r["assumptions"])

    def test_image_input_never_trusts_dimensions(self):
        def vision(system, user, images):
            assert images == [b"png-bytes"] and "Do NOT invent dimensions" in system
            return {"name": "From drawing", "width_mm": 14000, "depth_mm": 9000,
                    "levels": [{"rooms": [{"type": "living"}, {"type": "kitchen"}, {"type": "bedroom", "count": 2}, {"type": "bathroom"}]}],
                    "notes": ["room labels partly illegible"]}

        r = intake("my sketch", call_llm=vision, images=[b"png-bytes"])
        assert r["source"] == "image" and r["confirmed"] is False
        assert any("unreliable" in q for q in r["questions"]) and any("Confirm width" in q for q in r["questions"])
        assert r["notes"] == ["room labels partly illegible"]

    def test_images_without_a_vision_model_are_reported_as_ignored(self):
        r = intake("a bungalow with two bedrooms", images=[b"x"])
        assert any("ignored" in q for q in r["questions"])

    def test_confirmation_requires_a_layout_that_works(self):
        p = intake("3 bedroom bungalow with parlour, kitchen and store, 12m x 9m")
        bad = confirm(p)
        assert bad["confirmed"] is False
        good = confirm(p, p["suggested_footprint"])
        assert good["confirmed"] is True and good["design_ok"]

    def test_extract_with_llm_rejects_non_json_objects(self):
        spec, errs, _ = extract_with_llm("x", lambda s, u, i: ["not", "a", "dict"])
        assert spec is None and errs


class TestPackage:
    def test_full_package_contents_and_manifest_integrity(self):
        with tempfile.TemporaryDirectory() as d:
            res = generate_package(HOUSE, d, formats=("svg", "pdf"), step=False)
            assert res["ok"]
            man = json.loads((Path(d) / "manifest.json").read_text())
            names = {f["path"] for f in man["files"]}
            assert sum(1 for n in names if n.endswith(".pdf")) == 8 and sum(1 for n in names if n.endswith(".svg")) == 8
            for f in man["files"]:
                p = Path(d) / f["path"]
                assert p.stat().st_size == f["bytes"] and hashlib.sha256(p.read_bytes()).hexdigest() == f["sha256"]
            assert man["status"].startswith("CONCEPT") and man["compliance"]["building_code_compliance"] == "NOT_ASSESSED"
            assert man["foundation"]["status"] == "UNKNOWN" and "structural_design" in man["compliance"]["unassessed_topics"]
            assert man["assumptions"] and (Path(d) / "schedule.csv").exists() and (Path(d) / "furniture_schedule.json").exists()

    def test_soil_data_adds_footings_to_the_parts(self):
        with tempfile.TemporaryDirectory() as d:
            res = generate_package(HOUSE, d, soil_bearing_kpa=150, formats=("svg",), step=False)
            names = [p["name"] for p in res["parts"]]
            assert res["foundation"]["status"] == "PRELIMINARY" and any(n.startswith("footing_") for n in names)
            assert json.loads((Path(d) / "foundation_estimate.json").read_text())["engineer_review_required"] is True

    def test_furniture_can_be_left_out_or_added_in_3d(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            plain = generate_package(HOUSE, a, furniture=False, formats=("svg",), step=False)
            full = generate_package(HOUSE, b, furniture_3d=True, formats=("svg",), step=False)
            assert plain["furniture"] == [] and len(full["parts"]) == len(plain["parts"]) + len(full["furniture"])
            assert not (Path(a) / "furniture_schedule.json").exists()

    def test_an_impossible_building_returns_errors_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            res = generate_package(dict(HOUSE, width_mm=8000, depth_mm=7000), Path(d) / "out", step=False)
            assert res["ok"] is False and res["errors"] and not (Path(d) / "out").exists()

    def test_text_to_package_end_to_end(self):
        proposal = intake("two-storey house with three bedrooms, living room, dining room, kitchen and two bathrooms")
        with tempfile.TemporaryDirectory() as d:
            res = generate_package(proposal["spec"], d, formats=("svg",), step=False)
            assert res["ok"] and res["manifest"]["warnings"] == []

    def test_svg_and_json_outputs_are_reproducible(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra = generate_package(HOUSE, a, formats=("svg",), step=False)
            rb = generate_package(HOUSE, b, formats=("svg",), step=False)
            ha = {f["path"]: f["sha256"] for f in ra["manifest"]["files"]}
            hb = {f["path"]: f["sha256"] for f in rb["manifest"]["files"]}
            assert ha == hb
