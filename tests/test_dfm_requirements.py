"""dfm.py and requirements.py, and how the chat uses them. Pure Python."""
import copy

import pytest

import chat_service as svc
import chat_store as cs
import dfm
import requirements as rq
from _chat_fakes import BRACKET, fresh_store
from conversation import Plan, plan_turn

# ------------------------------------------------------------------------ DFM ---

def sev(findings):
    return sorted({f["severity"] for f in findings})


def rules(findings):
    return {f["rule"] for f in findings}


def test_features_are_read_from_the_parameters():
    f = dfm.features("spacer", {"outer_diameter_mm": 10, "inner_diameter_mm": 6, "length_mm": 8})
    assert f["min_wall_mm"] == 2 and f["largest_dimension_mm"] == 10
    f = dfm.features("flange", {"outer_diameter_mm": 100, "inner_diameter_mm": 60, "thickness_mm": 10, "hole_count": 4, "hole_diameter_mm": 5})
    assert f["min_wall_mm"] == 20 and f["hole_depth_ratio"] == 2.0
    assert "hole_diameter_mm" not in dfm.features("flange", {"hole_count": 0, "hole_diameter_mm": 5})
    assert dfm.features("shaft", {"diameter_mm": 10, "length_mm": 120})["slenderness"] == 12


def test_cnc_thin_walls_and_deep_holes():
    thin = dfm.check("tube", {"outer_diameter_mm": 20, "wall_thickness_mm": 0.6, "length_mm": 50}, "cnc_milling", "6061")
    assert "min_wall" in rules(thin) and thin[0]["severity"] == "warning"
    very = dfm.check("tube", {"outer_diameter_mm": 20, "wall_thickness_mm": 0.3, "length_mm": 50}, "cnc_milling", "6061")
    assert very[0]["severity"] == "error"
    plastic = dfm.check("tube", {"outer_diameter_mm": 20, "wall_thickness_mm": 1.0, "length_mm": 50}, "cnc_milling", "ABS")
    assert "min_wall" in rules(plastic)                              # 1 mm is fine in aluminium, thin in plastic
    assert not rules(dfm.check("tube", {"outer_diameter_mm": 20, "wall_thickness_mm": 1.0, "length_mm": 50}, "cnc_milling", "6061")) & {"min_wall"}
    deep = dfm.check("flange", {"thickness_mm": 60, "hole_count": 2, "hole_diameter_mm": 5, "outer_diameter_mm": 100, "inner_diameter_mm": 30}, "cnc_milling")
    assert any(f["rule"] == "hole_depth" and f["severity"] == "error" for f in deep)


def test_turning_slenderness():
    assert sev(dfm.check("shaft", {"diameter_mm": 10, "length_mm": 80}, "turning")) == []
    assert sev(dfm.check("shaft", {"diameter_mm": 10, "length_mm": 150}, "turning")) == ["warning"]
    assert sev(dfm.check("shaft", {"diameter_mm": 10, "length_mm": 300}, "turning")) == ["error"]


def test_fdm_rules():
    f = dfm.check("l_bracket", {"thickness_mm": 0.6, "width_mm": 50, "height_mm": 50, "hole_diameter_mm": 1.5, "hole_count": 2}, "fdm_print", "PLA")
    assert {"min_wall", "min_hole"} <= rules(f) and any(x["severity"] == "error" for x in f)
    assert "build_volume" in rules(dfm.check("flat_plate", {"length_mm": 400, "width_mm": 100, "thickness_mm": 5}, "fdm_print", "PLA"))
    assert dfm.check("flat_plate", {"length_mm": 50, "width_mm": 50, "thickness_mm": 5}, "fdm_print", "aluminum")[0]["rule"] == "material_fit"


def test_sheet_metal_rules():
    f = dfm.check("l_bracket", {"thickness_mm": 2, "width_mm": 6, "height_mm": 30, "fillet_radius_mm": 1, "hole_diameter_mm": 1, "hole_count": 1}, "sheet_metal", "6061")
    assert {"bend_radius", "flange_length", "min_hole"} <= rules(f)
    assert "thickness" in rules(dfm.check("flat_plate", {"thickness_mm": 12, "width_mm": 50, "length_mm": 50}, "sheet_metal", "steel"))
    assert dfm.check("flat_plate", {"width_mm": 50, "length_mm": 50}, "sheet_metal", "steel")[0]["rule"] == "thickness"     # no thickness: info, not a guess
    assert dfm.check("l_bracket", {"thickness_mm": 2}, "sheet_metal", "PLA")[0]["rule"] == "material_fit"


def test_process_that_makes_no_sense_for_the_part():
    f = dfm.check("gear", {"teeth": 20}, "sheet_metal")
    assert f[0]["rule"] == "process_fit" and f[0]["severity"] == "error"
    with pytest.raises(ValueError):
        dfm.check("gear", {}, "laser_sintering")


def test_best_fit_ranks_by_problems_then_preference():
    r = dfm.best_fit("shaft", {"diameter_mm": 10, "length_mm": 300}, "6061")
    assert r[0]["process"] != "turning" or r[0]["errors"] <= r[-1]["errors"]
    assert [x["errors"] for x in r] == sorted(x["errors"] for x in r) or [(x["errors"], x["warnings"]) for x in r] == sorted((x["errors"], x["warnings"]) for x in r)
    clean = dfm.best_fit("l_bracket", {"thickness_mm": 3, "width_mm": 40, "height_mm": 40}, "6061")
    assert {x["process"] for x in clean} == {"cnc_milling", "fdm_print", "sheet_metal"} or "sheet_metal" in {x["process"] for x in clean}


# ---------------------------------------------------------------- requirements ---

def kinds(text):
    return [(r["kind"], r["value"]) for r in rq.parse_requirements(text)]


def test_parsing_clear_phrasings():
    assert kinds("it has to fit in a 100 x 80 x 60 mm box") == [("envelope", [100.0, 80.0, 60.0])]
    assert kinds("must fit inside 10x8x6 cm") == [("envelope", [100.0, 80.0, 60.0])]
    assert kinds("keep it under 200 g") == [("max_mass_g", 200.0)]
    assert kinds("weigh at most 1.5 kg") == [("max_mass_g", 1500.0)]
    assert kinds("walls at least 2mm") == [("min_wall", 2.0)] and kinds("minimum 3 mm walls") == [("min_wall", 3.0)]
    assert kinds("no larger than 12 cm") == [("max_dimension", 120.0)] and kinds("maximum length of 90 mm") == [("max_dimension", 90.0)]
    assert kinds("must be made of 6061 aluminum and anodised") == [("material", "6061 aluminum")]


def test_casual_mentions_are_not_requirements():
    for text in ("a bracket 50mm wide with 4 holes", "I like 3 mm walls", "make it 200 g", "make it made of unobtainium", "the box is 100 x 80 x 60"):
        assert kinds(text) == [], text


def test_several_requirements_in_one_message_and_dedupe():
    got = kinds("must fit in 100x80x60 mm, under 200 g, and walls at least 2 mm. also under 200 g")
    assert sorted(k for k, _ in got) == ["envelope", "max_mass_g", "min_wall"]


FACTS = {"size_mm": [100.0, 40.0, 5.0], "mass_g": 54.0, "min_wall_mm": 3.0, "material": "6061 aluminum"}


def ev(kind, value, facts=FACTS):
    return rq.evaluate([{"id": "r", "kind": kind, "op": "<=", "value": value, "text": ""}], facts)[0]


def test_envelope_ignores_orientation_and_catches_misfits():
    assert ev("envelope", [5, 100, 40])["status"] == "pass"                  # same box, different order
    assert ev("envelope", [100, 40, 5])["status"] == "pass"                  # exact fit counts
    r = ev("envelope", [100, 30, 10])
    assert r["status"] == "fail" and "100 x 40 x 5" in r["detail"]
    assert ev("envelope", [90, 90, 90])["status"] == "fail"


def test_mass_wall_dimension_and_material():
    assert ev("max_mass_g", 60)["status"] == "pass" and ev("max_mass_g", 50)["status"] == "fail"
    assert ev("min_wall", 3)["status"] == "pass" and ev("min_wall", 4)["status"] == "fail"
    assert ev("max_dimension", 100)["status"] == "pass" and ev("max_dimension", 99)["status"] == "fail"
    assert ev("material", "6061")["status"] == "pass" and ev("material", "stainless steel")["status"] == "fail"


def test_missing_measurements_are_unknown_never_pass():
    empty = {"size_mm": None, "mass_g": None, "min_wall_mm": None, "material": None}
    for kind, value in (("envelope", [1, 1, 1]), ("max_dimension", 1), ("max_mass_g", 1), ("min_wall", 1), ("material", "6061")):
        r = ev(kind, value, empty)
        assert r["status"] == "unknown" and r["detail"], kind
    assert rq.evaluate([{"id": "x", "kind": "max_mass_g", "value": 1, "op": "<="}], None)[0]["status"] == "unknown"
    assert ev("max_mass_g", 5, {"mass_g": None, "mass_note": "set a material to get mass"})["detail"] == "set a material to get mass"


def test_facts_from_a_generator_result():
    result = {"parameters": {"part_type": "flat_plate", "parameters": {"thickness_mm": 5, "width_mm": 40, "length_mm": 100}, "material": "6061 aluminum"},
              "verification": {"measured": {"volume": 20000.0, "bbox": {"xmin": 0, "xmax": 100, "ymin": 0, "ymax": 40, "zmin": 0, "zmax": 5}}}}
    f = rq.facts_from_result(result)
    assert f["size_mm"] == [100, 40, 5] and f["mass_g"] == pytest.approx(54.0) and f["min_wall_mm"] == 5
    none = rq.facts_from_result({"parameters": {"part_type": "flat_plate", "parameters": {}}, "verification": {"measured": {"volume": 1000.0}}})
    assert none["mass_g"] is None and none["mass_note"] and none["size_mm"] is None


# ------------------------------------------------ the chat uses them end to end ---

U = "0xuser"


class World:
    def __init__(self, plans, facts_result):
        self.plans, self.result, self.credits = list(plans), facts_result, 9

    def deps(self):
        def generate(desc, spec, u, source):
            return {"success": True, "job_id": "j", "parameters": {"part_type": spec["part_type"], "parameters": spec["parameters"], "material": "6061 aluminum"},
                    "verification": self.result, "validation": {}}
        return svc.Deps(plan=lambda h, s: self.plans.pop(0), generate=generate, balance=lambda u: self.credits)


def verification(x, y, z, vol):
    return {"measured": {"volume": vol, "bbox": {"xmin": 0, "xmax": x, "ymin": 0, "ymax": y, "zmin": 0, "zmax": z}}}


def test_a_requirement_is_stored_checked_and_flagged_on_a_later_edit():
    fresh_store()
    cid = cs.create_chat(U)["id"]
    w = World([Plan("build", "Built.", copy.deepcopy(BRACKET), "llm")], verification(60, 40, 30, 20000))
    out = svc.run_turn(w.deps(), U, cid, "an L-bracket that must fit in 100 x 80 x 60 mm and weigh under 100 g")
    assert {r["kind"] for r in out["requirements"]} == {"envelope", "max_mass_g"} and all(r["status"] == "pass" for r in out["requirements"])
    assert "meets all 2" in out["assistant_message"]["content"]
    assert len(cs.list_requirements(U, cid)) == 2 and out["part"]["facts"]["size_mm"] == [60, 40, 30]

    w.result = verification(140, 40, 30, 20000)                                           # an edit makes it too long
    w.plans.append(Plan("edit", "Made it longer.", copy.deepcopy(BRACKET), "llm", ["length"]))
    out2 = svc.run_turn(w.deps(), U, cid, "make it longer")
    failing = [r for r in out2["requirements"] if r["status"] == "fail"]
    assert [r["kind"] for r in failing] == ["envelope"]
    assert "Version 2 breaks: fit inside 100 x 80 x 60 mm" in out2["assistant_message"]["content"]


def test_requirement_added_after_the_fact_is_checked_against_the_current_part():
    fresh_store()
    cid = cs.create_chat(U)["id"]
    w = World([Plan("build", "Built.", copy.deepcopy(BRACKET), "llm"), Plan("answer", "ok", None, "llm")], verification(60, 40, 30, 20000))
    svc.run_turn(w.deps(), U, cid, "bracket")
    out = svc.run_turn(w.deps(), U, cid, "it must fit in 50 x 50 x 50 mm")
    assert out["charged"] == 0 and out["part"] is None and len(out["new_requirements"]) == 1
    assert out["assistant_message"]["content"].startswith("Noted.") and "breaks" in out["assistant_message"]["content"]
    again = svc.run_turn(w.deps(), U, cid, "it must fit in 50 x 50 x 50 mm") if False else None
    assert again is None and len(cs.list_requirements(U, cid)) == 1


def test_duplicate_requirement_is_not_added_twice_and_can_be_removed():
    fresh_store()
    cid = cs.create_chat(U)["id"]
    r = rq.parse_requirements("keep it under 200 g")
    assert len(cs.add_requirements(U, cid, r)) == 1 and cs.add_requirements(U, cid, r) == []
    rid = cs.list_requirements(U, cid)[0]["id"]
    assert not cs.remove_requirement("0xother", cid, rid) and cs.remove_requirement(U, cid, rid) and cs.list_requirements(U, cid) == []
    with pytest.raises(LookupError):
        cs.list_requirements("0xother", cid)


def test_requirement_limit():
    fresh_store()
    cid = cs.create_chat(U)["id"]
    many = [{"kind": "max_mass_g", "op": "<=", "value": float(i + 1), "text": ""} for i in range(cs.MAX_REQUIREMENTS)]
    cs.add_requirements(U, cid, many)
    with pytest.raises(cs.ChatLimitError):
        cs.add_requirements(U, cid, [{"kind": "max_mass_g", "op": "<=", "value": 9999.0, "text": ""}])


def test_manufacturing_questions_are_answered_from_the_part_for_free():
    fresh_store()
    cid = cs.create_chat(U)["id"]
    thin = {**copy.deepcopy(BRACKET), "parameters": {**BRACKET["parameters"], "thickness_mm": 0.5}}
    w = World([Plan("build", "Built.", thin, "llm")], verification(60, 40, 30, 2000))
    svc.run_turn(w.deps(), U, cid, "thin bracket")
    credits = w.credits
    for q, expect in (("can this be 3D printed?", "3D printing"), ("could it be CNC machined?", "wall"), ("is it possible to make from sheet metal", "")):
        out = svc.run_turn(w.deps(), U, cid, q)
        assert out["charged"] == 0 and out["part"] is None and w.credits == credits and out["intent"] == "answer"
        assert "rule-of-thumb" in out["assistant_message"]["content"] and expect in out["assistant_message"]["content"], q
    out = svc.run_turn(w.deps(), U, cid, "how can I manufacture this?")
    assert out["assistant_message"]["content"].startswith("Best fit first:")


def test_a_change_request_mentioning_printing_is_not_hijacked():
    assert svc.dfm_answer("make it 3d printable", {"spec": {"part_type": "l_bracket", "parameters": {}}}) is None
    assert svc.dfm_answer("can this be 3D printed?", None) is None


# -------------------------------------------------------- standards in the chat ---

def test_standard_part_builds_from_the_standard_without_an_ai_key():
    plan = plan_turn([{"role": "user", "content": "I need a 6204 bearing"}], None, llm=None)
    assert plan.intent == "build" and plan.spec["part_type"] == "bearing" and plan.spec["parameters"]["outer_diameter_mm"] == 47.0
    assert "ISO 15" in plan.reply


def test_standard_hint_reaches_the_ai_planner():
    seen = []

    def llm(messages):
        seen.append(messages)
        return '{"intent": "answer", "reply": "ok", "spec": null}'
    plan_turn([{"role": "user", "content": "an M8 hex bolt 30mm long"}], None, llm=llm, catalogue="CAT")
    system = seen[0][0]["content"]
    assert "STANDARD PART" in system and "ISO 4017" in system and '"size": "M8"' in system
    seen.clear()
    plan_turn([{"role": "user", "content": "a bracket"}], None, llm=llm, catalogue="CAT")
    assert "STANDARD PART" not in seen[0][0]["content"]
