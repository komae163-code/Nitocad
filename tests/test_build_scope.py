"""Questions before building, for everything that is not a whole vehicle. Pure Python: no CAD kernel, no web stack."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_scope as BS  # noqa: E402
import conversation as C  # noqa: E402


def U(t):
    return {"role": "user", "content": t}


def A(t):
    return {"role": "assistant", "content": t}


def ask(history, spec=None, ai=False):
    return BS.scope_turn(history, spec, has_ai=ai)


# ----------------------------------------------------------------- the first message
def test_a_vague_request_for_a_part_gets_one_tappable_question():
    kind, plan = ask([U("I want a bracket")])
    assert kind == "ask" and plan.intent == "answer" and plan.spec is None
    assert plan.reply.startswith("Before I build the bracket") and "I would start with" in plan.reply
    assert len(plan.choices) == 1 and plan.choices[0]["question"] == "What size should the bracket be?"
    assert len(plan.choices[0]["options"]) == 3


def test_every_family_asks_something_for_a_bare_request():
    for name, fam in BS.FAMILIES.items():
        word = fam["words"][0] if fam["words"] else "pcb"
        text = f"I want a {word}" if word != "pcb" else "I want a pcb"
        got = ask([U(text)], ai=True)
        assert got is not None and got[0] == "ask", (name, text, got)
        assert got[1].choices and all(2 <= len(c["options"]) <= 5 for c in got[1].choices), name


def test_a_request_with_enough_details_builds_as_before():
    assert ask([U("L-bracket 50 mm wide, 60 mm tall, 3 mm thick, 4 holes")]) is None
    assert ask([U("make a gear with 20 teeth, 8 mm bore, steel")]) is None


def test_skip_phrases_build_at_once():
    assert ask([U("make me a plate, use the defaults")]) is None
    assert ask([U("a gear, you decide")]) is None
    assert ask([U("I want a gear")])[0] == "ask"             # "just build a gear" is still a vague request


def test_questions_and_edits_are_left_to_the_planner():
    assert ask([U("what is a bracket?")]) is None
    assert ask([U("how thick should a plate be")]) is None
    assert ask([U("make the bracket thicker")]) is None


def test_things_with_no_buildable_kind_are_left_to_the_planner():
    assert ask([U("I want a spaceship hull")]) is None
    assert ask([U("hello")]) is None


def test_standard_parts_build_from_their_standard():
    assert ask([U("a 6204 bearing")]) is None


def test_vehicles_still_belong_to_the_vehicle_layer():
    assert BS.family_of("I want to build a wheelbarrow") is None
    assert ask([U("I want to build a wheelbarrow")]) is None
    assert ask([U("I want a car")]) is None


# ------------------------------------------------------------------- the conversation
def tapped(first_ask, option):
    return U(first_ask.choices[0]["question"] + " " + option)


def test_one_tap_with_sizes_in_it_is_enough_for_a_simple_part():
    kind, plan = ask([U("I want a bracket")])
    h = [U("I want a bracket"), A(plan.reply), tapped(plan, "Medium, 50 mm wide, 60 mm tall, 4 mm thick")]
    kind2, desc, hist, staged = ask(h)
    assert kind2 == "build"
    assert "50 mm wide" in desc and "What size" not in desc and desc.startswith("I want a bracket")
    assert hist == [U(desc)]                                  # the planner sees a plain request, no question chat


def test_a_choice_without_numbers_leads_to_the_next_question():
    kind, plan = ask([U("I want a gear")])
    h = [U("I want a gear"), A(plan.reply), U("What size should it be? Small")]
    kind2, plan2 = ask(h)
    assert kind2 == "ask" and plan2.reply.startswith("Before I build the gear, one more choice")
    assert plan2.choices[0]["question"] == "How thick should it be?"


def test_a_building_needs_two_answers_and_earlier_messages_are_kept():
    pre = [U("hi"), A("Hello, what would you like to design?")]
    h = pre + [U("I want a house")]
    _, p1 = ask(h)
    h += [A(p1.reply), tapped(p1, "2 storeys")]
    _, p2 = ask(h)
    assert p2.choices[0]["question"] == "How big is the footprint?"
    h += [A(p2.reply), tapped(p2, "Medium, 10 x 12 m")]
    kind, desc, hist, staged = ask(h)
    assert kind == "build" and hist[:2] == pre and hist[2] == U(desc) and len(hist) == 3
    assert "2 storeys" in desc and "10 x 12 m" in desc


def test_just_build_it_mid_conversation_builds_with_what_there_is():
    _, p = ask([U("I want a house")])
    kind, desc, hist, staged = ask([U("I want a house"), A(p.reply), U("just build it")])
    assert kind == "build" and desc.startswith("I want a house")


def test_the_chat_builds_once_its_questions_are_answered_and_never_after_five_messages():
    h = [U("I want a bracket")]
    asked = []
    for filler in ("maybe", "not sure", "dunno", "hmm", "ok"):    # words with no numbers, so the details never add up
        res = ask(h)
        if res[0] == "build":
            break
        asked.append(res[1].choices[0]["question"])
        h += [A(res[1].reply), U(filler)]
    assert res[0] == "build"
    assert len(set(asked)) == len(asked)                          # a typed answer moves on, it never repeats a question
    assert len([m for m in h if m["role"] == "user"]) <= 5


def test_a_part_on_the_bench_is_edited_not_questioned():
    on_bench = {"part_type": "l_bracket", "parameters": {}}
    assert ask([U("I want a bracket")], spec=on_bench) is None
    assert ask([U("I want a new bracket")], spec=on_bench)[0] == "ask"


def test_assemblies_ask_only_when_an_ai_can_build_them():
    assert ask([U("I want an assembly")], ai=False) is None
    assert ask([U("I want an assembly")], ai=True)[0] == "ask"


def test_circuit_boards_get_board_questions():
    kind, plan = ask([U("I need a PCB")])
    assert kind == "ask" and plan.choices[0]["question"] == "What should the board do?"
    assert "circuit board" in plan.reply
    assert ask([U("a hex standoff for pcb mounting")])[0] == "ask"   # a mechanical part: it gets part questions
    assert BS.family_of("a hex standoff for pcb mounting") == "round"


# --------------------------------------------------------------------- through the planner
def fake_parse(text):
    low = text.lower()
    ptype = "l_bracket" if "bracket" in low else "gear" if "gear" in low else "unknown"
    return SimpleNamespace(part_type=ptype, parameters={"seen": text}, material=None, operations=[])


def test_the_planner_asks_first_then_builds_the_folded_request():
    h = [U("I want a bracket")]
    plan = C.plan_turn(h, None, regex_parse=fake_parse)
    assert plan.intent == "answer" and plan.choices and plan.spec is None

    h += [A(plan.reply), U(plan.choices[0]["question"] + " Small, 30 mm wide, 30 mm tall, 3 mm thick")]
    plan2 = C.plan_turn(h, None, regex_parse=fake_parse)
    assert plan2.intent == "build" and plan2.spec["part_type"] == "l_bracket"
    seen = plan2.spec["parameters"]["seen"]
    assert "30 mm wide" in seen and "What size" not in seen


def test_the_ai_sees_one_plain_request_not_the_question_chat():
    seen = {}

    def llm(messages):
        seen["messages"] = messages
        return ('{"intent":"build","reply":"Built.","spec":{"part_type":"l_bracket","parameters":{},"material":null,'
                '"operations":[]},"choices":[]}')

    first = C.plan_turn([U("I want a bracket")], None, llm=llm)
    assert first.intent == "answer" and not seen            # asked before calling the AI at all
    h = [U("I want a bracket"), A(first.reply), U(first.choices[0]["question"] + " Large, 100 mm wide, 120 mm tall, 6 mm thick")]
    plan = C.plan_turn(h, None, llm=llm)
    assert plan.intent == "build" and plan.source == "llm"
    user_texts = [m["content"] for m in seen["messages"] if m["role"] == "user"]
    assert any("100 mm wide" in t for t in user_texts)
    assert not any("Before I build" in m["content"] for m in seen["messages"])


def test_a_detailed_request_goes_straight_to_the_planner():
    plan = C.plan_turn([U("L-bracket 50 mm wide, 60 mm tall, 3 mm thick, 4 holes")], None, regex_parse=fake_parse)
    assert plan.intent == "build" and plan.choices == []


def test_a_board_that_names_what_it_does_is_already_a_full_order():
    for text in ("design a PCB with a status LED", "PCB with a status LED powered from 5V",
                 "design a PCB with a wifi module and an OLED", "design a PCB with a 555 blinking LED powered from 5V"):
        assert ask([U(text)]) is None, text
    assert ask([U("I need a PCB")])[0] == "ask"


def test_a_message_that_combines_several_things_is_not_questioned():
    assert BS.names_several_kinds("a gear on a shaft") and not BS.names_several_kinds("a gear")
    assert ask([U("a gear on a shaft")], ai=True) is None
    assert ask([U("a plate with a bracket on top")], ai=True) is None
