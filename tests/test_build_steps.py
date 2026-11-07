"""Build in steps for every kind of build, not only vehicles. Pure Python: no CAD kernel, no web stack."""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_scope as BS  # noqa: E402
import build_steps as S  # noqa: E402
import conversation as C  # noqa: E402


def U(t):
    return {"role": "user", "content": t}


def A(t):
    return {"role": "assistant", "content": t}


def spec(part_type, **params):
    return {"part_type": part_type, "parameters": params, "material": None, "operations": []}


def titles(steps):
    return [s.title for s in steps]


def all_valid(target):
    steps = S.steps_for(target)
    return steps and all(S._valid(s.view(copy.deepcopy(target))) for s in steps)


# ------------------------------------------------------------------------------------- parts and their features
def test_a_bracket_goes_body_then_holes_then_edges():
    t = spec("l_bracket", width_mm=50, height_mm=60, depth_mm=40, thickness_mm=3, hole_count=2, hole_diameter_mm=3.2,
             fillet_radius_mm=1.0)
    steps = S.steps_for(t)
    assert titles(steps) == ["the main body of the L bracket", "the holes", "the rounded edges"]
    v1, v2, v3 = (S.view_at(t, steps, k) for k in (1, 2, 3))
    assert v1["parameters"]["hole_count"] == 0 and v1["parameters"]["fillet_radius_mm"] == 0
    assert v2["parameters"]["hole_count"] == 2 and v2["parameters"]["fillet_radius_mm"] == 0
    assert S.STAGE_KEY in v1 and v1["stage"]["k"] == 1 and v1["stage"]["of"] == 3 and v1["stage"]["target"] == S._plain(t)
    assert S.STAGE_KEY not in v3 and v3["parameters"] == t["parameters"]       # the last step is the finished design
    assert all_valid(t)


def test_features_left_at_their_defaults_are_still_steps():
    assert len(S.steps_for(spec("l_bracket", width_mm=50, height_mm=60, depth_mm=40, thickness_mm=3))) == 3


def test_parts_with_one_kind_of_feature_get_two_steps():
    assert len(S.steps_for(spec("flat_plate", length_mm=100, width_mm=60, thickness_mm=3))) == 2           # edges only
    assert len(S.steps_for(spec("flange", outer_diameter_mm=100, inner_diameter_mm=50, thickness_mm=10, hole_count=4))) == 2
    assert len(S.steps_for(spec("motor_mount", face_size_mm=42, thickness_mm=5))) == 2
    plate = spec("flat_plate", length_mm=100, width_mm=60, thickness_mm=3, hole_count_x=2, hole_count_y=2)
    assert titles(S.steps_for(plate))[1] == "the holes" and len(S.steps_for(plate)) == 3


def test_a_part_with_nothing_to_split_has_no_steps():
    assert S.steps_for(spec("gear", module=2, teeth=20, bore_mm=8, thickness_mm=10)) == []
    assert S.steps_for(spec("l_bracket", width_mm=50, height_mm=60, depth_mm=40, thickness_mm=3, hole_count=0,
                            fillet_radius_mm=0)) == []
    assert S.steps_for(spec("spaceship")) == []


# ---------------------------------------------------------------------------------------------------- assemblies
def part(name, pt="shaft"):
    return {"name": name, "part_type": pt, "parameters": {}, "count": 1}


def test_an_assembly_adds_its_parts_in_order():
    t = spec("assembly", layout="row", parts=[part("shaft"), part("gear", "gear"), part("bearing", "bearing")])
    steps = S.steps_for(t)
    assert titles(steps) == ["the shaft and the gear", "the bearing"]
    assert [p["name"] for p in S.view_at(t, steps, 1)["parameters"]["parts"]] == ["shaft", "gear"]
    assert len(S.view_at(t, steps, 2)["parameters"]["parts"]) == 3 and S.STAGE_KEY not in S.view_at(t, steps, 2)


def test_a_long_assembly_is_grouped_so_it_stays_a_few_steps():
    t = spec("assembly", layout="row", parts=[part(f"p{i}") for i in range(10)])
    steps = S.steps_for(t)
    assert 2 <= len(steps) <= 5 and len(S.view_at(t, steps, len(steps))["parameters"]["parts"]) == 10


def test_assemblies_that_cannot_be_cut_are_not_staged():
    assert S.steps_for(spec("assembly", layout="row", parts=[part("a"), part("b")])) == []           # two parts only
    assert S.steps_for(spec("assembly", layout="mated", parts=[part("a"), part("b"), part("c")], mates=[{"x": 1}])) == []
    assert S.steps_for(spec("assembly", layout="custom", parts=[part("a"), part("b"), part("c")], vehicle={"name": "car"})) == []


# ----------------------------------------------------------------------------------------------------- buildings
def test_a_building_keeps_its_footprint_and_ends_on_the_roof():
    t = spec("building", floors=2, bedrooms=2, bathrooms=1, roof_type="gable")
    steps = S.steps_for(t)
    assert titles(steps)[-1] == "the roof" and len(steps) >= 2
    from building.flat import resolve_flat
    final = resolve_flat(S.view_at(t, steps, len(steps))["parameters"])["model"]["geometry"]
    for k in range(1, len(steps)):
        v = S.view_at(t, steps, k)
        g = resolve_flat(v["parameters"])["model"]["geometry"]
        assert (g["W"], g["D"]) == (final["W"], final["D"]), titles(steps)[k - 1]
        assert v["parameters"]["roof_type"] == "flat"
    assert S.view_at(t, steps, len(steps))["parameters"] == t["parameters"]    # the finished design is untouched


def test_a_building_whose_rooms_do_not_fit_on_one_floor_starts_at_the_floors():
    t = spec("building", floors=2, bedrooms=3, bathrooms=2, roof_type="gable")
    assert titles(S.steps_for(t)) == ["the floors and walls", "the roof"]


def test_a_one_floor_flat_roof_building_has_nothing_to_split():
    assert S.steps_for(spec("building", floors=1, bedrooms=2, roof_type="flat")) == []


# ---------------------------------------------------------------------------------------------- boards and civil
def pcb_target(text="design a PCB with an I2C sensor header and a status LED powered from 5V"):
    return C.plan_turn([U(text)], None).spec


def test_a_board_adds_power_then_parts_then_connectors():
    t = pcb_target()
    steps = S.steps_for(t)
    assert titles(steps) == ["the power supply", "the parts that do the work", "the connectors and headers"]
    first = [b["type"] for b in S.view_at(t, steps, 1)["parameters"]["spec"]["blocks"]]
    assert first and all(x.startswith(("power", "ldo", "buck", "reg")) for x in first)
    assert len(S.view_at(t, steps, 3)["parameters"]["spec"]["blocks"]) == len(t["parameters"]["spec"]["blocks"])
    assert all_valid(t)


def test_roads_and_culverts_have_their_own_steps():
    road = spec("road_segment", length_mm=100000, lane_count=2, has_kerbs=True)
    assert titles(S.steps_for(road)) == ["the road surface", "the kerbs"]
    culvert = spec("box_culvert", barrel_count=3, span_mm=3000, rise_mm=2000, length_mm=12000)
    assert titles(S.steps_for(culvert)) == ["the first barrel", "the extra barrels"]


# --------------------------------------------------------------------------------------------- through the planner
def fake_parse(text):
    low = text.lower()
    params = {"width_mm": 50.0, "height_mm": 60.0, "depth_mm": 40.0, "thickness_mm": 3.0, "hole_count": 2,
              "hole_diameter_mm": 3.2, "fillet_radius_mm": 1.0, "seen": text}
    return SimpleNamespace(part_type="l_bracket" if "bracket" in low else "unknown", parameters=params, material=None,
                           operations=[])


def build_staged(extra=None):
    h = [U("I want a bracket")]
    first = C.plan_turn(h, None, regex_parse=fake_parse)
    h += [A(first.reply), U(first.choices[0]["question"] + " Medium, 50 mm wide, 60 mm tall, 4 mm thick")]
    return h, C.plan_turn(h, None, regex_parse=fake_parse)


def test_after_the_questions_a_build_starts_at_step_one_with_a_beginner_explanation():
    _, plan = build_staged()
    assert plan.intent == "build" and plan.spec["stage"]["k"] == 1 and plan.spec["stage"]["of"] == 3
    assert plan.spec["parameters"]["hole_count"] == 0
    assert plan.reply.startswith("Step 1 of 3 for your L bracket: the main body of the L bracket.")
    assert "Next is the holes." in plan.reply and "free preview" in plan.reply


def test_next_adds_a_step_and_finish_adds_the_rest():
    h, plan = build_staged()
    cur = plan.spec
    nxt = C.plan_turn(h + [A(plan.reply), U("next step")], cur, regex_parse=fake_parse)
    assert nxt.intent == "edit" and nxt.spec["stage"]["k"] == 2 and nxt.spec["parameters"]["hole_count"] == 2
    assert nxt.reply.startswith("Step 2 of 3: added the holes.") and nxt.changes == ["added the holes"]
    done = C.plan_turn(h + [A(plan.reply), U("Finish the whole thing")], cur, regex_parse=fake_parse)
    assert "stage" not in done.spec and done.spec["parameters"]["fillet_radius_mm"] == 1.0
    assert done.reply.startswith("Step 3 of 3") and "That is every step" in done.reply
    after = C.plan_turn([U("hi"), A("x"), U("ok")], done.spec, regex_parse=fake_parse)
    assert not after.stage_move                                     # a finished build is an ordinary part again


def test_a_change_mid_way_is_planned_on_the_finished_design_and_the_step_is_kept():
    h, plan = build_staged()
    nxt = C.plan_turn(h + [A(plan.reply), U("next")], plan.spec, regex_parse=fake_parse)
    seen = {}

    def llm(messages):
        seen["spec"] = messages
        t = copy.deepcopy(S.target_of(nxt.spec))
        t["parameters"]["thickness_mm"] = 5.0
        import json
        return json.dumps({"intent": "edit", "reply": "Made it 5 mm thick.", "spec": t, "choices": []})

    edit = C.plan_turn([U("make it 5 mm thick")], nxt.spec, llm=llm)
    assert edit.intent == "edit" and edit.spec["stage"]["k"] == 2
    assert edit.spec["parameters"]["thickness_mm"] == 5.0 and edit.spec["parameters"]["hole_count"] == 2
    assert edit.spec["parameters"]["fillet_radius_mm"] == 0                      # the edges still wait for step 3
    assert edit.spec["stage"]["target"]["parameters"]["fillet_radius_mm"] == 1.0
    assert '"stage"' not in str(seen["spec"])                                    # the AI never sees the staging fields


def test_a_change_to_something_not_built_yet_says_when_it_will_show():
    h, plan = build_staged()
    import json

    def llm(messages):
        t = copy.deepcopy(S.target_of(plan.spec))
        t["parameters"]["hole_diameter_mm"] = 6.0
        return json.dumps({"intent": "edit", "reply": "Made the holes 6 mm.", "spec": t, "choices": []})

    edit = C.plan_turn([U("make the holes 6 mm")], plan.spec, llm=llm)
    assert edit.spec["stage"]["k"] == 1 and "It will show when its step arrives." in edit.reply
    assert edit.spec["stage"]["target"]["parameters"]["hole_diameter_mm"] == 6.0


def test_a_question_mid_way_does_not_move_the_steps():
    h, plan = build_staged()
    q = C.plan_turn(h + [A(plan.reply), U("what is a fillet?")], plan.spec, regex_parse=fake_parse)
    assert q.intent != "edit" or q.spec is None or q.spec.get("stage", {}).get("k") == 1


def test_just_build_it_builds_everything_at_once():
    h = [U("I want a bracket")]
    first = C.plan_turn(h, None, regex_parse=fake_parse)
    h += [A(first.reply), U("just build it")]
    plan = C.plan_turn(h, None, regex_parse=fake_parse)
    assert plan.intent == "build" and "stage" not in plan.spec and plan.spec["parameters"]["hole_count"] == 2


def test_a_complete_request_builds_whole_unless_the_person_asks_for_steps():
    whole = C.plan_turn([U("L-bracket 50 mm wide, 60 mm tall, 3 mm thick, 4 holes")], None, regex_parse=fake_parse)
    assert "stage" not in whole.spec
    for text in ("walk me through building an L-bracket 50 mm wide, 60 mm tall, 3 mm thick",
                 "I'm a beginner, show me how to make an L-bracket 50 mm wide and 60 mm tall step by step"):
        plan = C.plan_turn([U(text)], None, regex_parse=fake_parse)
        assert plan.spec.get("stage", {}).get("k") == 1, text


def test_a_beginner_with_a_vague_request_still_gets_questions_first():
    plan = C.plan_turn([U("teach me to build a bracket step by step")], None, regex_parse=fake_parse)
    assert plan.intent == "answer" and plan.choices


def test_a_design_with_nothing_to_split_builds_normally_after_the_questions():
    def gear_parse(text):
        return SimpleNamespace(part_type="gear", parameters={"module": 2, "teeth": 20}, material=None, operations=[])

    h = [U("I want a gear")]
    first = C.plan_turn(h, None, regex_parse=gear_parse)
    h += [A(first.reply), U(first.choices[0]["question"] + " Medium, module 2, 30 teeth, 10 mm bore")]
    plan = C.plan_turn(h, None, regex_parse=gear_parse)
    assert plan.intent == "build" and "stage" not in plan.spec


def test_staging_survives_being_saved_after_generation():
    _, plan = build_staged()
    built = {"part_type": "l_bracket", "parameters": {"thickness_mm": 3.9}, "material": None, "operations": []}
    kept = S.keep_stage(plan.spec, built)
    assert kept["stage"]["k"] == 1 and kept["parameters"] == {"thickness_mm": 3.9}
    assert S.keep_stage(spec("l_bracket"), dict(built)) == built                  # an unstaged spec is left alone


def test_a_changed_part_type_ends_the_steps():
    h, plan = build_staged()
    import json

    def llm(messages):
        return json.dumps({"intent": "edit", "reply": "Now a plate.", "spec": spec("flat_plate", length_mm=100, width_mm=60,
                                                                                 thickness_mm=3), "choices": []})

    edit = C.plan_turn([U("make it a plate")], plan.spec, llm=llm)
    assert "stage" not in (edit.spec or {})
