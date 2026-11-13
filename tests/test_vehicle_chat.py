"""The vehicle conversation layer: no CadQuery, no network, a scripted model."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conversation
import spec_library as SL
import vehicle_chat as VC
import vehicle_spec as VS


def example(name):
    return json.loads((ROOT / "vehicles" / "examples" / f"{name}.json").read_text())


def lib():
    l = SL.SpecLibrary(SL.MemoryStore())
    l.seed_from_dir(ROOT / "vehicles" / "examples")
    return l


class Script:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, messages):
        self.calls.append(copy.deepcopy(messages))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def barrow_text(**over):
    s = example("wheelbarrow")
    for k, v in over.items():
        s["params"][k]["default"] = v
    return json.dumps(s)


# ---------------------------------------------------------------- recognising a request
def test_it_recognises_whole_vehicle_requests():
    for t in ["I want to create a wheelbarrow", "design a bicycle for a child", "build me an RC helicopter",
              "a garden wheelbarrow with a 90 litre tray", "make a two axle cart"]:
        assert VC.is_vehicle_request(t), t


def test_parts_and_other_messages_are_not_vehicles():
    for t in ["bicycle crank arm 170 mm", "make the wheelbarrow wheel bigger", "L-bracket 50 mm wide with 4 holes",
              "what is a bicycle", "gear 20 teeth on an 8 mm shaft"]:
        assert not VC.is_vehicle_request(t), t


def test_other_chats_are_left_alone():
    assert VC.plan_vehicle_turn("L-bracket 50 mm wide", None, llm=Script(), library=lib()) is None


# ---------------------------------------------------------------- start
def test_ai_plan_becomes_a_free_assembly_preview_that_carries_its_vehicle():
    llm = Script(barrow_text())
    plan = VC.plan_vehicle_turn("I want to create a wheelbarrow", None, llm=llm, library=lib())
    assert plan.intent == "build" and plan.source == "llm"
    p = plan.spec["parameters"]
    assert plan.spec["part_type"] == "assembly" and p["layout"] == "custom"
    assert len(p["parts"]) == 15 and all(len(x["position"]) == 3 for x in p["parts"])
    assert p["vehicle"]["name"] == "wheelbarrow"
    assert "free preview" in plan.reply and "Generate" in plan.reply and "15 parts" in plan.reply
    assert "wheel dia 400" in plan.reply


def test_engine_plan_uses_the_closest_built_in_example_and_says_so():
    plan = VC.plan_vehicle_turn("I want to create a wheelbarrow", None, llm=None, library=lib())
    assert plan.intent == "build" and plan.source == "rules"
    assert "built-in example" in plan.reply and plan.spec["parameters"]["vehicle"]["name"] == "wheelbarrow"


def test_a_failing_ai_falls_back_to_the_example_and_is_still_reported_as_rules():
    llm = Script(RuntimeError("down"))
    plan = VC.plan_vehicle_turn("create a bicycle", None, llm=llm, library=lib())
    assert plan.source == "rules" and plan.spec["parameters"]["vehicle"]["name"] == "bicycle"
    assert plan.reply.startswith("The AI could not finish that plan")


def test_nothing_matches_gives_a_plain_answer_and_builds_nothing():
    plan = VC.plan_vehicle_turn("I want to build a vehicle", None, llm=None, library=SL.SpecLibrary(SL.MemoryStore()))
    assert plan.intent == "answer" and plan.spec is None and "nothing was charged" in plan.reply


def test_the_assembly_panel_prefix_forces_a_vehicle_plan_for_any_wording():
    llm = Script(barrow_text())
    plan = VC.plan_vehicle_turn("Vehicle: something to carry soil around the garden", None, llm=llm, library=lib())
    assert plan.intent == "build" and "something to carry soil" in llm.calls[0][1]["content"]
    assert "Vehicle:" not in llm.calls[0][1]["content"]


# ---------------------------------------------------------------- edit
def current_barrow():
    return VC.plan_vehicle_turn("create a wheelbarrow", None, llm=None, library=lib()).spec


def test_ai_edit_returns_an_edit_plan_with_plain_changes():
    llm = Script(barrow_text(wheel_dia=450))
    plan = VC.plan_vehicle_turn("make the wheel 450 mm", current_barrow(), llm=llm, library=lib())
    assert plan.intent == "edit" and plan.source == "llm"
    assert plan.changes == ["wheel dia 400 to 450"] and plan.reply.startswith("Updated the wheelbarrow: wheel dia 400 to 450")
    assert "CURRENT SPEC" in llm.calls[0][1]["content"]
    assert plan.spec["parameters"]["vehicle"]["params"]["wheel_dia"]["default"] == 450


def test_an_edit_that_changes_nothing_is_an_answer_not_a_new_version():
    llm = Script(barrow_text())
    plan = VC.plan_vehicle_turn("make the wheel the same", current_barrow(), llm=llm, library=lib())
    assert plan.intent == "answer" and plan.spec is None


def test_edits_are_not_added_to_the_library():
    l = lib()
    n = len(l.all())
    VC.plan_vehicle_turn("make the wheel 450 mm", current_barrow(), llm=Script(barrow_text(wheel_dia=450)), library=l)
    assert len(l.all()) == n


def test_without_an_ai_a_named_size_can_still_be_set():
    plan = VC.plan_vehicle_turn("make the wheel diameter 450 mm", current_barrow(), llm=None, library=lib())
    assert plan.intent == "edit" and plan.source == "rules" and plan.changes == ["wheel dia 400 to 450"]


def test_size_limits_are_respected_and_explained():
    plan = VC.plan_vehicle_turn("make the wheel diameter 5000 mm", current_barrow(), llm=None, library=lib())
    assert plan.intent == "answer" and "between 200 and 700" in plan.reply


def test_ambiguous_size_is_never_guessed():
    n, ch, pr = VC.apply_param_edit_rules("make the tray 700", example("wheelbarrow"))
    assert ch == [] and n == example("wheelbarrow")


def test_unit_conversion_in_rules_edits():
    n, ch, _ = VC.apply_param_edit_rules("set tray length to 90 cm and tyre width 100", example("wheelbarrow"))
    assert n["params"]["tray_len"]["default"] == 900 and n["params"]["tyre_w"]["default"] == 100 and len(ch) == 2


def test_without_an_ai_other_edits_say_what_is_possible():
    plan = VC.plan_vehicle_turn("add a second axle", current_barrow(), llm=None, library=lib())
    assert plan.intent == "answer" and "Pick an AI" in plan.reply


def test_a_failed_ai_edit_that_names_a_size_still_applies_by_rules():
    llm = Script("not json", "still not json", "nope")
    plan = VC.plan_vehicle_turn("wheel diameter 450", current_barrow(), llm=llm, library=lib())
    assert plan.intent == "edit" and plan.source == "rules"


def test_a_failed_ai_edit_changes_nothing_and_costs_nothing():
    llm = Script("x", "y", "z")
    plan = VC.plan_vehicle_turn("add a bell", current_barrow(), llm=llm, library=lib())
    assert plan.intent == "answer" and plan.spec is None and "nothing was charged" in plan.reply


# ---------------------------------------------------------------- questions
def test_a_question_is_answered_not_built():
    llm = Script("The tray is 800 mm long. It is a concept layout.")
    plan = VC.plan_vehicle_turn("how long is the tray?", current_barrow(), llm=llm, library=lib())
    assert plan.intent == "answer" and plan.source == "llm" and plan.spec is None and "800" in plan.reply


def test_a_polite_edit_phrased_as_a_question_is_an_edit():
    plan = VC.plan_vehicle_turn("can you make the wheel diameter 450 mm?", current_barrow(), llm=None, library=lib())
    assert plan.intent == "edit"


def test_a_question_without_an_ai_is_answered_from_the_plan():
    plan = VC.plan_vehicle_turn("is it going to fit through a door?", current_barrow(), llm=None, library=lib())
    assert plan.intent == "answer" and plan.source == "rules" and "15 parts" in plan.reply


def test_a_new_vehicle_replaces_the_current_one():
    plan = VC.plan_vehicle_turn("now build a bicycle instead", current_barrow(), llm=None, library=lib())
    assert plan.intent == "build" and plan.spec["parameters"]["vehicle"]["name"] == "bicycle"


# ---------------------------------------------------------------- placing mated parts
def test_mated_parts_need_the_kernel_or_a_box_function():
    r = VC.to_chat_spec(example("loco_0_4_0_core"), bbox_fn=None)
    assert not r["ok"] and "CAD kernel" in r["errors"][0]


def test_mated_parts_are_placed_with_a_box_function_and_downloads_are_noted():
    box = lambda part: (0.0, 0.0, 0.0, 100.0, 50.0, 20.0)  # noqa: E731
    r = VC.to_chat_spec(example("loco_0_4_0_core"), bbox_fn=box)
    assert r["ok"], r["errors"]
    assert all(p["position"] is not None for p in r["spec"]["parameters"]["parts"])
    assert any("not in the preview" in n for n in r["notes"])


# ---------------------------------------------------------------- through the real planner entry
def test_plan_turn_routes_a_vehicle_and_skips_the_shaft_and_overlap_rules():
    VC.set_library(lib())
    try:
        history = [{"role": "user", "content": "I want to create a wheelbarrow, 90 litre steel tray, one wheel"}]
        first = conversation.plan_turn(history, None, llm=None)
        assert first.intent == "build" and first.spec["parameters"]["vehicle"]["name"] == "wheelbarrow"
        assert "stage" not in first.spec["parameters"]                    # a full order is built whole
        history.append({"role": "assistant", "content": first.reply})
        history.append({"role": "user", "content": "make the wheel diameter 450 mm"})
        second = conversation.plan_turn(history, first.spec, llm=None)
        assert second.intent == "edit" and second.changes == ["wheel dia 400 to 450"]
        assert "Also updated to match" not in second.reply
    finally:
        VC.set_library(None)


def test_plain_chats_still_go_to_the_normal_planner():
    VC.set_library(lib())
    try:
        plan = conversation.plan_turn([{"role": "user", "content": "L-bracket 50 mm wide, 60 mm tall, 3 mm thick, 4 holes"}],
                                      None, llm=None, regex_parse=None)
        assert plan.spec is None or plan.spec["part_type"] != "assembly"
    finally:
        VC.set_library(None)


def test_assemblies_in_chat_are_no_longer_limited_to_twelve_lines():
    parts = [{"part_type": "simple_box", "parameters": {}, "name": f"p{i}", "count": 1} for i in range(40)]
    out = conversation._clean_assembly_params({"parts": parts, "layout": "row"})
    assert len(out["parts"]) == 40
    out = conversation._clean_assembly_params({"parts": [dict(parts[0], count=60), parts[1]], "layout": "row"})
    assert out["parts"][0]["count"] == 60


def test_the_overlap_gate_skips_vehicles_but_still_checks_plain_custom_assemblies():
    import assembly_gate
    v = VC.plan_vehicle_turn("create a wheelbarrow", None, llm=None, library=lib()).spec
    assert assembly_gate.check(v).errors == []


def test_restore_vehicle_puts_the_spec_back_after_the_generator_drops_it():
    planned = VC.plan_vehicle_turn("create a wheelbarrow", None, llm=None, library=lib()).spec
    built = {"part_type": "assembly", "parameters": {"parts": planned["parameters"]["parts"], "layout": "custom"}}
    out = VC.restore_vehicle(planned, built)
    assert out["parameters"]["vehicle"]["name"] == "wheelbarrow" and out["parameters"]["layout"] == "custom"
    assert VC.vehicle_of(out) is not None


def test_a_chat_that_is_not_about_a_vehicle_touches_nothing(tmp_path):
    import os
    folder = tmp_path / "lib"
    old = os.environ.get("VEHICLE_LIBRARY_DIR")
    os.environ["VEHICLE_LIBRARY_DIR"] = str(folder)
    VC.set_library(None)
    try:
        assert VC.plan_vehicle_turn("L-bracket 50 mm wide", None, llm=Script()) is None
        assert not folder.exists()                       # no library was opened or created
        plan = VC.plan_vehicle_turn("I want to create a wheelbarrow", None, llm=None)
        assert plan.intent == "build" and folder.exists()   # a vehicle request does open it, seeded with the examples
    finally:
        VC.set_library(None)
        if old is None:
            os.environ.pop("VEHICLE_LIBRARY_DIR", None)
        else:
            os.environ["VEHICLE_LIBRARY_DIR"] = old


# ---------------------------------------------------------------- the library learns when someone presses Generate
def _fresh_library():
    import spec_library as SL
    return SL.SpecLibrary(SL.MemoryStore())


def test_generating_an_ai_plan_marks_its_library_record_accepted_and_stores_no_words():
    lib = _fresh_library()
    vspec = json.loads((ROOT / "vehicles" / "examples" / "wheelbarrow.json").read_text(encoding="utf-8"))
    added = lib.add(vspec, "", status="candidate")
    built = VC.to_chat_spec(vspec, library_id=added["id"])
    assert built["ok"] and built["spec"]["parameters"]["library_id"] == added["id"]
    assert VC.on_generated(built["spec"], lib) is True
    rec = lib.get(added["id"])
    assert rec["status"] == "accepted" and rec["description"] == ""


def test_generating_an_edited_version_adds_the_edited_vehicle_as_its_own_accepted_record():
    lib = _fresh_library()
    vspec = json.loads((ROOT / "vehicles" / "examples" / "wheelbarrow.json").read_text(encoding="utf-8"))
    first = lib.add(vspec, "", status="candidate")
    edited, changes, _ = VC.apply_param_edit_rules("wheel diameter 450 mm", vspec)
    assert changes
    built = VC.to_chat_spec(edited, library_id=first["id"])
    assert VC.on_generated(built["spec"], lib) is True
    assert lib.get(first["id"])["status"] == "candidate"                     # the unedited plan is not promoted
    assert [r for r in lib.all(("accepted",)) if r["spec"] == edited]


def test_on_generated_ignores_anything_that_is_not_a_vehicle_and_never_raises():
    assert VC.on_generated(None) is False
    assert VC.on_generated({"part_type": "shaft", "parameters": {}}) is False
    assert VC.on_generated({"part_type": "assembly", "parameters": {"vehicle": "nope"}}) is False


# ---------------------------------------------------------------- free size edits (no AI), relative edits
def test_a_pure_size_edit_is_free_even_when_an_ai_is_available():
    llm = Script()                                         # any model call would pop from an empty list and fail
    plan = VC.plan_vehicle_turn("set tray_len to 900", current_barrow(), llm=llm, library=lib())
    assert llm.calls == [] and plan.intent == "edit" and plan.source == "rules"
    assert plan.changes == ["tray len 800 to 900"]
    assert plan.spec["parameters"]["vehicle"]["params"]["tray_len"]["default"] == 900


def test_a_size_with_a_dimension_word_is_understood():
    plan = VC.plan_vehicle_turn("make the tray 900 long", current_barrow(), llm=Script(), library=lib())
    assert plan.source == "rules" and plan.changes == ["tray len 800 to 900"]


def test_increase_and_decrease_work_from_the_current_value_by_amount_or_percent():
    spec = example("wheelbarrow")
    n, ch, pr = VC.apply_param_edit_rules("increase tray_len by 10%", spec)
    assert n["params"]["tray_len"]["default"] == 880 and ch == ["tray len 800 to 880"] and pr == []
    n, ch, _ = VC.apply_param_edit_rules("decrease the wheel diameter by 50 mm", spec)
    assert n["params"]["wheel_dia"]["default"] == 350
    n, ch, _ = VC.apply_param_edit_rules("increase tray length by 10% and make the wheel diameter 450", spec)
    assert n["params"]["tray_len"]["default"] == 880 and n["params"]["wheel_dia"]["default"] == 450 and len(ch) == 2


def test_a_relative_edit_is_not_read_as_an_absolute_value():
    # "by 10%" used to be read as "set to 10", which then failed the size limits with a confusing message
    n, ch, pr = VC.apply_param_edit_rules("increase tray_len by 10%", example("wheelbarrow"))
    assert pr == [] and n["params"]["tray_len"]["default"] != 10


def test_a_message_with_more_than_sizes_still_goes_to_the_model():
    llm = Script(barrow_text(wheel_dia=450))
    plan = VC.plan_vehicle_turn("make the wheel diameter 450 mm and add a second axle", current_barrow(), llm=llm, library=lib())
    assert len(llm.calls) == 1 and plan.source == "llm"
    llm = Script(barrow_text())
    VC.plan_vehicle_turn("add 2 wheels with diameter 400", current_barrow(), llm=llm, library=lib())
    assert len(llm.calls) == 1                              # names a size and a number, but asks for parts


def test_free_edits_respect_limits_and_say_when_nothing_changes():
    llm = Script()
    plan = VC.plan_vehicle_turn("set wheel diameter to 5000 mm", current_barrow(), llm=llm, library=lib())
    assert llm.calls == [] and plan.intent == "answer" and plan.spec is None and "between 200 and 700" in plan.reply
    plan = VC.plan_vehicle_turn("set tray_len to 800", current_barrow(), llm=llm, library=lib())
    assert plan.intent == "answer" and plan.spec is None and "already" in plan.reply


def test_a_whole_number_size_rejects_a_fraction():
    spec = example("wheelbarrow")
    spec["params"]["legs"] = {"default": 2, "min": 1, "max": 6, "kind": "int"}
    n, ch, pr = VC.apply_param_edit_rules("set legs to 3.5", spec)
    assert ch == [] and pr and "whole number" in pr[0]
    n, ch, pr = VC.apply_param_edit_rules("increase legs by 1", spec)
    assert n["params"]["legs"]["default"] == 3 and pr == []


def test_a_free_edit_that_partly_hits_a_limit_applies_the_rest_and_says_what_was_left():
    llm = Script()
    plan = VC.plan_vehicle_turn("set tray_len to 900 and set wheel diameter to 5000 mm", current_barrow(), llm=llm, library=lib())
    assert llm.calls == [] and plan.changes == ["tray len 800 to 900"] and "Left as it was" in plan.reply


# ---------------------------------------------------------------- planning time budget
def test_a_slow_ai_plan_says_it_took_too_long_and_charges_nothing():
    import vehicle_planner as VP
    t = {"now": 0.0}

    def slow(messages):
        t["now"] += 100
        return "not json"
    real = VP.time.monotonic
    VP.time.monotonic = lambda: t["now"]
    try:
        plan = VC.plan_vehicle_turn("create a bicycle", None, llm=slow, library=SL.SpecLibrary(SL.MemoryStore()))
    finally:
        VP.time.monotonic = real
    assert plan.intent == "answer" and plan.spec is None
    assert "time limit" in plan.reply and "nothing was charged" in plan.reply


def test_a_slow_ai_plan_falls_back_to_the_example_and_says_why():
    import vehicle_planner as VP
    t = {"now": 0.0}

    def slow(messages):
        t["now"] += 100
        return "not json"
    real = VP.time.monotonic
    VP.time.monotonic = lambda: t["now"]
    try:
        plan = VC.plan_vehicle_turn("create a bicycle", None, llm=slow, library=lib())
    finally:
        VP.time.monotonic = real
    assert plan.spec is not None and plan.reply.startswith("The AI took too long to plan that")


def test_the_budget_comes_from_settings_or_the_environment(monkeypatch):
    monkeypatch.setenv("VEHICLE_PLAN_TIME_BUDGET_S", "45")
    assert VC._plan_budget() in (45.0, 120.0)               # 45 without the web config, the configured value with it


# ---------------------------------------------------------------- the shared library asks for review
def test_the_app_library_queues_instead_of_promoting_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("VEHICLE_LIBRARY_DIR", str(tmp_path / "lib"))
    VC.set_library(None)
    try:
        l = VC.get_library()
        assert l.review_required is True
        planned = VC.plan_vehicle_turn("create a wheelbarrow", None, llm=Script(barrow_text(tray_len=850)), library=l)
        assert VC.on_generated(planned.spec, l) is True
        rec = l.get(planned.spec["parameters"]["library_id"])
        assert rec["status"] == "candidate" and rec["pending_review"] is True and len(l.pending_review()) == 1
        assert l.approve(rec["id"])["status"] == "accepted"
    finally:
        VC.set_library(None)


# ---------------------------------------------------------------- asking before building, and building in steps
def _ask(history):
    return conversation.plan_turn(history, None, llm=None)


def test_a_bare_request_gets_a_question_not_a_model():
    VC.set_library(lib())
    try:
        plan = _ask([{"role": "user", "content": "Hey let's build a wheelbarrow"}])
        assert plan.intent == "answer" and plan.spec is None and plan.source == "rules"
        assert plan.reply.startswith("Before I build") and "tray and handles" in plan.reply
        # every question arrives together, as one card
        assert [c["question"] for c in plan.choices] == ["What size should it be?", "What is it made of?", "How many wheels?"]
        assert all(c.get("card") for c in plan.choices)
    finally:
        VC.set_library(None)


def test_a_full_order_is_built_whole_and_just_build_it_skips_the_questions():
    VC.set_library(lib())
    try:
        full = _ask([{"role": "user", "content": "let's build a wheelbarrow: tray 800 x 560 x 280, 400 mm wheel, steel"}])
        assert full.intent == "build" and "stage" not in full.spec["parameters"]
        skip = _ask([{"role": "user", "content": "build a wheelbarrow, just build it"}])
        assert skip.intent == "build" and len(skip.spec["parameters"]["parts"]) == 15
    finally:
        VC.set_library(None)


def _talk(first, answers):
    history = [{"role": "user", "content": first}]
    plan = _ask(history)
    for a in answers:
        history += [{"role": "assistant", "content": plan.reply}, {"role": "user", "content": a}]
        plan = _ask(history)
    return history, plan


def test_answering_the_questions_builds_the_first_step_only():
    VC.set_library(lib())
    try:
        history, plan = _talk("Hey let's build a wheelbarrow",
                              ["What size should it be? Garden, about 90 litre. What is it made of? Steel tray and steel "
                               "tube frame. How many wheels? One wheel"])
        assert plan.intent == "build" and plan.spec["parameters"]["stage"] == {"shown": 1, "of": 3}
        assert len(plan.spec["parameters"]["parts"]) == 5 and "Step 1 of 3" in plan.reply and "the legs and crossbars" in plan.reply
        history += [{"role": "assistant", "content": plan.reply}, {"role": "user", "content": "Next step"}]
        second = conversation.plan_turn(history, plan.spec, llm=None)
        assert second.intent == "edit" and second.spec["parameters"]["stage"] == {"shown": 2, "of": 3}
        assert len(second.spec["parameters"]["parts"]) == 9
        history += [{"role": "assistant", "content": second.reply}, {"role": "user", "content": "Finish the whole thing"}]
        done = conversation.plan_turn(history, second.spec, llm=None)
        assert len(done.spec["parameters"]["parts"]) == 15 and "stage" not in done.spec["parameters"]
        again = VC.plan_vehicle_turn("next", done.spec, llm=None, library=lib())
        assert again.intent == "answer" and "already showing" in again.reply
    finally:
        VC.set_library(None)


def test_a_size_edit_keeps_the_step_and_restore_vehicle_keeps_it_after_generate():
    VC.set_library(lib())
    try:
        _, plan = _talk("I want a wheelbarrow", ["just build it"])
        assert plan.spec["parameters"].get("stage") is None            # "just build it" skips the steps
        _, staged = _talk("I want a wheelbarrow", ["What size should it be? Garden, about 90 litre. "
                                                   "What is it made of? Steel. How many wheels? One wheel"])
        edit = VC.plan_vehicle_turn("make the wheel diameter 450 mm", staged.spec, llm=None, library=lib())
        assert edit.spec["parameters"]["stage"] == {"shown": 1, "of": 3} and len(edit.spec["parameters"]["parts"]) == 5
        built = VC.restore_vehicle(edit.spec, {"part_type": "assembly", "parameters": {"parts": []}})
        assert built["parameters"]["stage"] == {"shown": 1, "of": 3}
    finally:
        VC.set_library(None)


def test_cars_bins_planes_and_engines_start_a_conversation_but_part_names_do_not():
    VC.set_library(lib())
    try:
        for text, q in [("I want to build a car", "What kind of car?"), ("let's build a bin", "What kind of bin?"),
                        ("build an airplane", "What kind of aircraft?"), ("I want a V8 engine", "What kind of engine?")]:
            plan = _ask([{"role": "user", "content": text}])
            assert plan.intent == "answer" and plan.choices[0]["question"] == q, text
        for text in ("a car door hinge", "engine block 80 mm bore", "bin lid 200 mm"):
            assert not VC.is_vehicle_request(text), text
    finally:
        VC.set_library(None)


def test_an_example_of_a_different_kind_is_never_handed_back():
    # An airplane has a built-in example since the merge with the car/bin/airplane/engine branch,
    # so a truck (no example of its own) stands in for "a kind with nothing built in".
    plan = VC.plan_vehicle_turn("build a truck 6 m long steel", None, llm=None, library=lib())
    assert plan.intent == "answer" and plan.spec is None and "Without an AI" in plan.reply


def test_stage_plans_always_have_two_parts_per_step():
    for name in ("wheelbarrow", "bicycle", "rc_helicopter", "two_axle_cart"):
        v = example(name)
        ex = VS.expand_vehicle(v, None, None, None)
        steps = VC.stage_plan(v, ex)
        assert all(len(m) >= 2 for _, m in steps)
        if steps:
            assert sorted(n for _, m in steps for n in m) == sorted(p["name"] for p in ex["parts"])


# ---------------------------------------------------------------- the wheelbarrow's shape
def test_the_wheelbarrow_has_a_sloped_tray_and_handles_that_rise():
    v = example("wheelbarrow")
    ex = VS.expand_vehicle(v, None, None, None)
    assert ex["ok"] and all(c["status"] == "pass" for c in ex["checks"])
    parts = {p["name"]: p for p in ex["parts"]}
    assert parts["tray"]["part_type"] == "tapered_tray"
    p = parts["tray"]["parameters"]
    assert p["bottom_length_mm"] < p["length_mm"] and p["bottom_width_mm"] < p["width_mm"]
    rail, handle = parts["rail_left"], parts["handle_left"]
    assert max(e[2] for e in _tube_ends(handle)) > max(e[2] for e in _tube_ends(rail))   # the grips are higher than the rails
    assert abs(handle["rotation"][1]) not in (0, 90)                     # and the handle is angled, not level
    assert abs(parts["tray"]["position"][2] - (rail["position"][2] + 12.5)) < 1   # the tray stands on the rails


def test_a_tapered_tray_with_a_floor_bigger_than_its_opening_is_rejected():
    import validator
    bad = validator.validate_tapered_tray({"length_mm": 400, "width_mm": 300, "bottom_length_mm": 500, "bottom_width_mm": 200,
                                           "height_mm": 100, "wall_thickness_mm": 2})
    assert bad.errors
    ok = validator.validate_tapered_tray({})
    assert not ok.errors


# ---------------------------------------------------------------- tubes that slant in plan view
def _tube_ends(part):
    """Where a tube's two ends land. The tube and shaft templates extrude along local +Z from the origin, so the
    position is one end and the rotated +Z axis times the length gives the other."""
    import mating
    t = mating.transform_from_pose(tuple(part["position"]), tuple(part["rotation"]))
    length = part["parameters"]["length_mm"]
    return [list(part["position"]), [t.R[i][2] * length + part["position"][i] for i in range(3)]]


def _same(a, b, tol=1e-3):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def test_between_accepts_any_direction_and_mirrors_it():
    spec = {"name": "t", "params": {}, "components": [
        {"id": "rail", "type": "tube", "assembly": "a", "mirror": "y",
         "between": [[100, 20, 50], [-300, 200, 80]],
         "parameters": {"outer_diameter_mm": 25, "wall_thickness_mm": 2}}]}
    ex = VS.expand_vehicle(spec, None, None, None)
    assert ex["ok"], ex["errors"]
    parts = {p["name"]: p for p in ex["parts"]}
    left = sorted(_tube_ends(parts["rail_left"]), key=lambda e: -e[0])
    right = sorted(_tube_ends(parts["rail_right"]), key=lambda e: -e[0])
    assert _same(left[0], [100, 20, 50]) and _same(left[1], [-300, 200, 80])
    assert _same(right[0], [100, -20, 50]) and _same(right[1], [-300, -200, 80])


def test_the_wheelbarrow_rails_close_in_and_meet_the_forks():
    v = example("wheelbarrow")
    ex = VS.expand_vehicle(v, None, None, None)
    assert ex["ok"] and all(c["status"] == "pass" for c in ex["checks"])
    parts = {p["name"]: p for p in ex["parts"]}
    fork_y = parts["fork_left"]["position"][1]
    for side, sign in (("left", 1), ("right", -1)):
        front, rear = sorted(_tube_ends(parts["rail_" + side]), key=lambda e: -e[0])
        assert abs(front[1] - sign * fork_y) < 1e-3 and abs(front[0] - parts["fork_" + side]["position"][0]) < 1e-3
        assert abs(rear[1]) > abs(front[1]) + 50                    # the rails are wider at the back than at the fork
        grip = sorted(_tube_ends(parts["handle_" + side]), key=lambda e: e[0])[0]
        assert abs(grip[1]) > abs(rear[1]) and grip[2] > rear[2]    # the handles flare out and rise
        assert _same(sorted(_tube_ends(parts["handle_" + side]), key=lambda e: -e[0])[0], rear)   # and join the rails


# ---------------------------------------------------------------- the piston engine modules
def _engine(n, crank_deg=0):
    name = {1: "mini_engine_single_cylinder", 2: "mini_engine_inline_two", 4: "mini_engine_inline_four"}[n]
    v = example(name)
    v["params"]["crank_deg"]["default"] = crank_deg
    ex = VS.expand_vehicle(v, None, None, None)
    assert ex["ok"], ex["errors"]
    return v, ex, {p["name"]: p for p in ex["parts"]}


def _point(part, local):
    """A point given in a part's own coordinates, placed in the engine's coordinates."""
    import mating
    t = mating.transform_from_pose(tuple(part["position"]), tuple(part["rotation"]))
    return [sum(t.R[i][j] * local[j] for j in range(3)) + part["position"][i] for i in range(3)]


def test_every_rod_joins_its_crank_pin_to_its_piston_pin_at_every_crank_angle():
    import math
    for n, phases in ((1, [0]), (2, [0, 180]), (4, [0, 180, 180, 0])):
        for deg in (0, 37, 90, 145, 180, 250, 311):
            v, ex, parts = _engine(n, deg)
            crank = parts["engine_crank"]["parameters"]
            r, web, rodj, main = crank["stroke_mm"] / 2, crank["web_thickness_mm"], crank["rod_journal_length_mm"], crank["main_journal_length_mm"]
            pitch = 2 * web + rodj + main
            for k, ph in enumerate(phases, start=1):
                rod = parts[f"engine_rod_{k}"]
                length = rod["parameters"]["center_distance_mm"]
                a = math.radians(ph + deg)
                # the crank turned by [0, 90, 0]: a journal at angle a is at (x, y, z) = (along the shaft, r sin a, -r cos a)
                journal = [main + (k - 1) * pitch + web + rodj / 2, r * math.sin(a), -r * math.cos(a)]
                big = _point(rod, [length, 0, rod["parameters"]["thickness_mm"] / 2])
                small = _point(rod, [0, 0, rod["parameters"]["thickness_mm"] / 2])
                assert all(abs(big[i] - journal[i]) < 1e-3 for i in range(3)), (n, deg, k, big, journal)
                pin = parts[f"engine_pin_{k}"]
                pin_centre = _point(pin, [0, 0, pin["parameters"]["length_mm"] / 2])
                assert all(abs(small[i] - pin_centre[i]) < 1e-3 for i in range(3)), (n, deg, k, small, pin_centre)
                assert abs(pin_centre[1]) < 1e-9                                  # the pin stays on the cylinder axis


def test_pistons_stay_inside_their_liners_and_a_four_has_its_cylinders_apart():
    for deg in (0, 90, 180, 270):
        v, ex, parts = _engine(4, deg)
        assert all(c["status"] == "pass" for c in ex["checks"])
        for k in range(1, 5):
            liner, piston = parts[f"engine_liner_{k}"], parts[f"engine_piston_{k}"]
            lo, hi = liner["position"][2], liner["position"][2] + liner["parameters"]["length_mm"]
            assert lo <= piston["position"][2] and piston["position"][2] + piston["parameters"]["length_mm"] <= hi
        xs = [parts[f"engine_liner_{k}"]["position"][0] for k in range(1, 5)]
        assert all(b - a >= parts["engine_liner_1"]["parameters"]["outer_diameter_mm"] for a, b in zip(xs, xs[1:]))


def test_an_engine_request_without_an_ai_gets_the_matching_engine_in_three_steps():
    VC.set_library(lib())
    try:
        history, plan = _talk("I want to build a four cylinder inline engine",
                              ["What size should it be? Small, bore about 30 mm. What is it made of? Steel and aluminium"])
        assert plan.intent == "build", plan.reply
        assert plan.spec["parameters"]["vehicle"]["name"] == "mini_engine_inline_four"
        assert plan.spec["parameters"]["stage"] == {"shown": 1, "of": 3} and len(plan.spec["parameters"]["parts"]) == 2
        again = VC.plan_vehicle_turn("next", plan.spec, llm=None, library=lib())
        assert again.spec["parameters"]["stage"] == {"shown": 2, "of": 3} and len(again.spec["parameters"]["parts"]) == 14
        done = VC.plan_vehicle_turn("finish", again.spec, llm=None, library=lib())
        assert len(done.spec["parameters"]["parts"]) == 19 and "stage" not in done.spec["parameters"]
    finally:
        VC.set_library(None)


def test_the_questions_come_as_one_card_and_any_reply_to_it_builds():
    """"A Lambo" used to get "What kind of car?" three times running. Now the card shows all three questions once, and
    whatever comes back (a typed word, chips, Skip) starts the build."""
    VC.set_library(lib())
    try:
        history = [{"role": "user", "content": "I want to build a car"}]
        card = _ask(history)
        assert [c["question"] for c in card.choices] == ["What kind of car?", "How big is it?", "Should the inside be modelled too?"]
        history += [{"role": "assistant", "content": card.reply}, {"role": "user", "content": "A Lambo"}]
        assert _ask(history).intent == "build"                  # one typed word answers the card; the rest are defaults
    finally:
        VC.set_library(None)


def test_tapped_and_typed_answers_can_be_mixed():
    VC.set_library(lib())
    try:
        history, plan = _talk("I want to build a car", ["What kind of car? Sedan. full size"])
        assert plan.intent == "build" and not plan.choices
    finally:
        VC.set_library(None)
