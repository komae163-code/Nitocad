"""Starting from a template or an assembly: the reply must lead somewhere, and what it suggests must work."""
import conversation as C
import studio_templates as ST

U = lambda t: {"role": "user", "content": t}        # noqa: E731
A = lambda t: {"role": "assistant", "content": t}   # noqa: E731


def hint(t):
    return t.hint.replace("Try:", "").strip()


def test_every_template_hint_works_with_the_free_engine_plan_unless_the_template_says_it_needs_an_ai():
    """A new user is told to type the hint. Before this test, 11 of 30 hints answered "I did not catch which value to
    change", and the assembly hints said an AI was needed, so the first thing a template suggested was a dead end."""
    broken = []
    for t in ST.TEMPLATES:
        if t.needs_ai:
            continue
        p = C.plan_turn([U("Start from the template: " + t.title), A(ST.reply_for(t)), U(hint(t))], t.spec, llm=None)
        if p.intent not in ("edit", "build"):
            broken.append((t.id, hint(t), p.reply[:60]))
    assert broken == [], broken


def test_only_templates_with_no_engine_edit_are_marked_as_needing_an_ai():
    assert {t.id for t in ST.TEMPLATES if t.needs_ai} == {"key-25", "flange-nps2"}


def test_the_reply_says_what_to_do_next_and_how_it_ends():
    t = ST._BY_ID["gear-20"]
    r = ST.reply_for(t)
    assert r.startswith("Started from the template: ") and "free preview, built with no AI" in r
    assert "use 30 teeth" in r and "in your own words" in r and r.endswith("press Generate.")
    k = ST.reply_for(ST._BY_ID["key-25"])
    assert "needs an AI" in k and "Engine cannot edit it" in k and "use a 30 mm shaft" in k


def test_next_steps_send_the_exact_message_and_mark_what_the_engine_cannot_do():
    (q,) = ST.next_steps(ST._BY_ID["gear-20"])
    assert q["options"] == ["Use 30 teeth"] and q["say"] == ["use 30 teeth"] and q["needs_ai"] == [False]
    (q,) = ST.next_steps(ST._BY_ID["key-25"])
    assert q["needs_ai"] == [True]
    (q,) = ST.next_steps(ST._BY_ID["veh-car"], "a car with a 2.5 m wheelbase")
    assert q["options"][0] == "Change it to match what I asked before" and q["needs_ai"] == [True, False]
    assert q["say"][0] == "Change this to match what I asked for: a car with a 2.5 m wheelbase" and q["say"][1] == "make the wheelbase 2800 mm"


def test_what_the_person_asked_before_pressing_a_template_is_remembered():
    msgs = [U("hi"), A("Hello"), U("I want a small car with a 2.5 m wheelbase and 4 seats"), A("Before I build the car..."),
            U("Start from the template: Wheelbarrow")]
    assert ST.earlier_request(msgs) == "I want a small car with a 2.5 m wheelbase and 4 seats"
    assert ST.earlier_request([U("Start from the template: A"), U("just build it"), U("ok")]) is None
    assert ST.earlier_request([]) is None
    assert len(ST.earlier_request([U("x" * 400)])) == 140


def test_the_car_airplane_bin_and_engine_now_have_templates_that_build_and_edit():
    for tid in ("veh-car", "veh-light-airplane", "veh-wheelie-bin", "veh-engine-inline-four"):
        t = ST._BY_ID[tid]
        assert t.category == "vehicles" and t.spec["part_type"] == "assembly" and t.parts > 2, tid
    assert ST._BY_ID["veh-car"].parts == 97
