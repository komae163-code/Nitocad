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
    assert len(p["parts"]) == 13 and all(len(x["position"]) == 3 for x in p["parts"])
    assert p["vehicle"]["name"] == "wheelbarrow"
    assert "free preview" in plan.reply and "Generate" in plan.reply and "13 parts" in plan.reply
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
    assert plan.intent == "answer" and plan.source == "rules" and "13 parts" in plan.reply


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
        history = [{"role": "user", "content": "I want to create a wheelbarrow"}]
        first = conversation.plan_turn(history, None, llm=None)
        assert first.intent == "build" and first.spec["parameters"]["vehicle"]["name"] == "wheelbarrow"
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
