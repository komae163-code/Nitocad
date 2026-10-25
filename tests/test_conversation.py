"""conversation: planner parsing, LLM validation, rules fallback."""
import json
from types import SimpleNamespace

from _chat_fakes import BRACKET  # noqa: F401  (also fixes sys.path)

import conversation as cv

H = lambda *pairs: [{"role": r, "content": c} for r, c in pairs]  # noqa: E731


def llm_returning(*outputs):
    calls = []
    it = iter(outputs)

    def llm(messages):
        calls.append(messages)
        out = next(it)
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)
    llm.calls = calls
    return llm


def fake_regex(text):
    t = text.lower()
    if "gear" in t:
        return SimpleNamespace(part_type="gear", parameters={"teeth": 20, "module": 2, "thickness_mm": 10}, material=None, operations=[])
    return SimpleNamespace(part_type="unknown", parameters={}, material=None, operations=[])


# ---------------------------------------------------------------- LLM path ---

def test_llm_edit_returns_complete_spec_and_diff():
    new = {**BRACKET, "parameters": {**BRACKET["parameters"], "thickness_mm": 4}}
    llm = llm_returning({"intent": "edit", "reply": "Made it 4 mm thick.", "spec": new})
    plan = cv.plan_turn(H(("user", "make it 4mm thick")), BRACKET, llm=llm)
    assert plan.intent == "edit" and plan.source == "llm"
    assert plan.spec["parameters"]["hole_count"] == 4          # untouched values carry over
    assert plan.changes == ["thickness_mm: 3 to 4"]


def test_llm_prompt_contains_history_and_current_spec():
    llm = llm_returning({"intent": "answer", "reply": "Which size?", "spec": None})
    cv.plan_turn(H(("user", "a bracket"), ("assistant", "Which size?"), ("user", "bigger")), BRACKET, llm=llm, catalogue="CAT")
    msgs = llm.calls[0]
    assert msgs[0]["role"] == "system" and "CAT" in msgs[0]["content"] and '"thickness_mm": 3' in msgs[0]["content"]
    assert [m["content"] for m in msgs[1:]] == ["a bracket", "Which size?", "bigger"]


def test_llm_json_in_fences_and_prose_is_accepted():
    raw = 'Sure!\n```json\n{"intent":"build","reply":"ok","spec":{"part_type":"gear","parameters":{"teeth":24}}}\n```'
    plan = cv.plan_turn(H(("user", "gear")), None, llm=llm_returning(raw))
    assert plan.intent == "build" and plan.spec["parameters"]["teeth"] == 24 and plan.spec["operations"] == []


def test_edit_with_no_part_becomes_build():
    out = {"intent": "edit", "reply": "", "spec": {"part_type": "gear", "parameters": {"teeth": 24}}}
    assert cv.plan_turn(H(("user", "x")), None, llm=llm_returning(out)).intent == "build"


def test_bad_output_retries_then_succeeds_with_correction_note():
    good = {"intent": "answer", "reply": "Which part?", "spec": None}
    llm = llm_returning("not json at all", good)
    plan = cv.plan_turn(H(("user", "hi")), None, llm=llm)
    assert plan.intent == "answer" and plan.source == "llm" and len(llm.calls) == 2
    assert "unusable" in llm.calls[1][-1]["content"]


def test_unusable_twice_falls_back_to_rules_and_never_raises():
    llm = llm_returning(RuntimeError("timeout"), {"intent": "build", "spec": {"part_type": "unknown", "parameters": {}}})
    plan = cv.plan_turn(H(("user", "make me a gear")), None, llm=llm, regex_parse=fake_regex)
    assert plan.source == "rules" and plan.intent == "build" and plan.spec["part_type"] == "gear"


def test_unknown_types_and_assemblies_with_no_parts_are_rejected():
    for bad in ("assembly", "unknown", ""):
        try:
            cv.coerce_plan({"intent": "build", "spec": {"part_type": bad, "parameters": {}}}, None)
            raise AssertionError(bad)
        except ValueError:
            pass


# ------------------------------------------------------------ rules planner ---

def edit(text, spec=BRACKET):
    return cv.rules_plan(text, spec, fake_regex)


def test_rules_explicit_dimension():
    p = edit("make it 5mm thick")
    assert p.intent == "edit" and p.spec["parameters"]["thickness_mm"] == 5
    assert p.spec["parameters"]["width_mm"] == 50 and BRACKET["parameters"]["thickness_mm"] == 3  # input not mutated


def test_rules_relative_and_percent():
    assert edit("make it thicker").spec["parameters"]["thickness_mm"] == 3.6
    assert edit("make it 50% longer", {**BRACKET, "parameters": {"length_mm": 100}}).spec["parameters"]["length_mm"] == 150


def test_rules_hole_counts():
    assert edit("add 2 more holes").spec["parameters"]["hole_count"] == 6
    assert edit("use 8 holes").spec["parameters"]["hole_count"] == 8
    assert edit("add a hole").spec["parameters"]["hole_count"] == 5


def test_rules_tube_diameter_prefers_outer():
    tube = {"part_type": "tube", "parameters": {"outer_diameter_mm": 20, "inner_diameter_mm": 16, "length_mm": 100}, "material": None, "operations": []}
    p = edit("make the diameter 30mm", tube)
    assert p.spec["parameters"]["outer_diameter_mm"] == 30 and p.spec["parameters"]["inner_diameter_mm"] == 16


def test_rules_gear_teeth_and_bore():
    gear = {"part_type": "gear", "parameters": {"teeth": 20, "module": 2, "thickness_mm": 10, "bore_diameter_mm": 5}, "material": None, "operations": []}
    p = edit("use 30 teeth and an 8mm bore", gear)
    assert p.spec["parameters"]["teeth"] == 30 and p.spec["parameters"]["bore_diameter_mm"] == 8


def test_rules_new_part_in_existing_chat_is_a_build():
    p = edit("now a gear please")
    assert p.intent == "build" and p.spec["part_type"] == "gear"


def test_rules_unreadable_asks_instead_of_guessing():
    p = edit("make it prettier")
    assert p.intent == "answer" and p.spec is None
    p2 = cv.rules_plan("hello", None, fake_regex)
    assert p2.intent == "answer" and "which part" in p2.reply.lower()


def test_no_llm_uses_rules():
    plan = cv.plan_turn(H(("user", "add 2 more holes")), BRACKET, llm=None, regex_parse=fake_regex)
    assert plan.source == "rules" and plan.spec["parameters"]["hole_count"] == 6


def test_choices_are_cleaned_and_only_kept_on_answers():
    from conversation import coerce_plan
    raw = {"intent": "answer", "reply": "A few questions.", "spec": None, "choices": [
        {"question": "Which material?", "options": ["Aluminum", "Steel", "Steel", ""]},
        {"question": "Only one option", "options": ["x"]},
        "junk",
        {"question": "", "options": ["a", "b"]},
        {"question": "Finish?", "options": ["Raw", "Anodized", "Painted", "Plated", "Polished", "Extra"]},
    ]}
    plan = coerce_plan(raw, None)
    assert plan.choices[0] == {"question": "Which material?", "options": ["Aluminum", "Steel"]}
    assert len(plan.choices) == 2 and plan.choices[1]["options"] == ["Raw", "Anodized", "Painted", "Plated", "Polished"]


def test_bad_choices_never_break_a_plan():
    from conversation import coerce_plan
    plan = coerce_plan({"intent": "answer", "reply": "Which size?", "choices": "nope"}, None)
    assert plan.choices == [] and plan.reply == "Which size?"
