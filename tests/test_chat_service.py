"""chat_service: free previews, the one paid Generate step, refunds, locking - with fakes for CAD and credits."""
import copy

from _chat_fakes import BRACKET, fresh_store

import chat_service as svc
import chat_store as cs
from conversation import Plan

U = "0xuser"


class World:
    def __init__(self, credits=5, plans=None, gen=None, messages_left=100, build_error=None, cost=1,
                 answer_cost=0, uses_ai=False):
        self.credits, self.refunds, self.generated, self.charges = credits, [], [], 0
        self.answer_cost, self.uses_ai, self.answer_charges = answer_cost, uses_ai, 0
        self.plans = list(plans or [])
        self.gen = gen
        self.messages_left = messages_left
        self.cost = cost
        self.ai_logged = 0
        self.build_error = build_error
        self.paid, self.built = set(), []
        self.seen_history = []

    def deps(self):
        def plan(history, spec):
            self.seen_history.append((history, spec))
            return self.plans.pop(0)

        def ai_used(u):
            self.ai_logged += 1
            self.messages_left -= 1

        def generate(desc, spec, u, source):
            self.generated.append((desc, copy.deepcopy(spec), source))
            if self.gen:
                return self.gen(desc, spec)
            return {"success": True, "job_id": f"job{len(self.generated)}", "parameters": copy.deepcopy(spec),
                    "validation": {"warnings": [], "corrections": {}}}

        def charge_job(job_id, u):
            if job_id in self.paid:
                return "already_paid", None
            if self.credits < self.cost:
                return "insufficient", None
            self.credits -= self.cost
            self.charges += 1
            self.paid.add(job_id)
            return "charged", "purchased"

        def refund_job(job_id, u):
            self.credits += self.cost
            self.paid.discard(job_id)
            self.refunds.append(job_id)

        def build_files(job_id):
            self.built.append(job_id)
            if self.build_error:
                raise self.build_error

        def charge_credits(u, amount):
            if self.credits < amount:
                return None
            self.credits -= amount
            self.answer_charges += amount
            return "purchased"

        return svc.Deps(plan=plan, generate=generate, balance=lambda u: self.credits,
                        ai_allowed=lambda u: self.messages_left > 0, ai_used=ai_used,
                        answer_cost=self.answer_cost, uses_ai=self.uses_ai, charge_credits=charge_credits,
                        job_cost=lambda job_id: self.cost,
                        charge_job=charge_job, refund_job=refund_job, build_files=build_files)


def build_plan(spec=BRACKET, reply="Built."):
    return Plan("build", reply, copy.deepcopy(spec), "llm")


def edit_plan(spec, changes=("thickness_mm: 3 to 4",)):
    return Plan("edit", "", spec, "llm", list(changes))


def setup():
    fresh_store()
    return cs.create_chat(U)["id"]


def test_message_builds_a_free_preview_and_never_charges():
    cid, w = setup(), World(plans=[build_plan()])
    out = svc.run_turn(w.deps(), U, cid, "a bracket for a motor")
    assert out["charged"] == 0 and w.credits == 5 and w.charges == 0 and out["balance"] == 5
    assert out["part"]["version"] == 1 and out["part"]["job_id"] == "job1"
    chat = cs.get_chat(U, cid)
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][1]["part_id"] == out["part"]["id"]
    assert chat["title"] == "a bracket for a motor"


def test_default_reply_tells_the_person_it_is_a_free_preview():
    cid, w = setup(), World(plans=[build_plan(reply="")])
    out = svc.run_turn(w.deps(), U, cid, "bracket")
    text = out["assistant_message"]["content"].lower()
    assert "free preview" in text and "generate" in text


def test_fixing_a_mistake_is_free_and_makes_version_two():
    cid = setup()
    w = World(plans=[build_plan()])
    svc.run_turn(w.deps(), U, cid, "bracket")
    new = copy.deepcopy(BRACKET); new["parameters"]["thickness_mm"] = 4
    w.plans.append(edit_plan(new))
    out = svc.run_turn(w.deps(), U, cid, "make it 4mm thick")
    history, spec = w.seen_history[-1]
    assert spec["parameters"]["thickness_mm"] == 3                      # planner saw the stored v1 spec
    assert [m["role"] for m in history] == ["user", "assistant", "user"]  # and the whole conversation
    assert out["part"]["version"] == 2 and out["part"]["spec"]["parameters"]["thickness_mm"] == 4
    assert out["charged"] == 0 and w.credits == 5 and w.charges == 0
    assert "version 2" in out["assistant_message"]["content"]


def test_many_edits_in_a_row_never_spend_credits():
    cid, w = setup(), World(plans=[build_plan()])
    svc.run_turn(w.deps(), U, cid, "bracket")
    for _ in range(6):
        w.plans.append(edit_plan(copy.deepcopy(BRACKET)))
        svc.run_turn(w.deps(), U, cid, "tweak")
    assert w.credits == 5 and w.charges == 0 and len(cs.get_chat(U, cid)["parts"]) == 7


def test_answers_are_free_and_build_nothing():
    cid, w = setup(), World(plans=[Plan("answer", "Which size?", None, "llm")])
    out = svc.run_turn(w.deps(), U, cid, "hmm")
    assert out["charged"] == 0 and out["part"] is None and w.generated == [] and w.credits == 5
    assert out["assistant_message"]["content"] == "Which size?"


def test_daily_ai_limit_stops_before_the_model_is_called():
    cid, w = setup(), World(plans=[build_plan()], messages_left=0)
    out = svc.run_turn(w.deps(), U, cid, "bracket")
    assert out["part"] is None and w.generated == [] and w.credits == 5
    assert w.seen_history == []                       # the AI was never asked, so no cost
    assert out["assistant_message"]["meta"]["limit"] is True
    assert "messages for this ai" in out["assistant_message"]["content"].lower()
    assert cs.get_chat(U, cid)["parts"] == []


def test_only_messages_that_really_used_the_ai_count_against_the_allowance():
    cid = setup()
    w = World(plans=[build_plan(), Plan("answer", "Which size?", None, "rules"), edit_plan(copy.deepcopy(BRACKET))])
    svc.run_turn(w.deps(), U, cid, "bracket")           # llm planner
    svc.run_turn(w.deps(), U, cid, "hmm")               # rules fallback, free
    svc.run_turn(w.deps(), U, cid, "tweak")             # llm planner
    assert w.ai_logged == 2


def test_guidance_answers_carry_tappable_choices():
    cid = setup()
    choices = [{"question": "Which material?", "options": ["Aluminum", "Steel"]}]
    w = World(plans=[Plan("answer", "A few quick questions.", None, "llm", choices=choices)])
    out = svc.run_turn(w.deps(), U, cid, "help me choose")
    assert out["part"] is None and w.credits == 5 and w.generated == []
    assert out["assistant_message"]["meta"]["choices"] == choices
    assert cs.get_chat(U, cid)["messages"][-1]["meta"]["choices"] == choices


def test_failed_preview_costs_nothing_and_stores_no_part():
    cid = setup()
    w = World(plans=[build_plan()], gen=lambda d, s: {"success": False, "error": "Geometry invalid."})
    out = svc.run_turn(w.deps(), U, cid, "bracket")
    assert w.credits == 5 and w.charges == 0 and out["part"] is None
    assert "not charged" in out["assistant_message"]["content"].lower()
    assert cs.get_chat(U, cid)["parts"] == []


def test_generator_exception_does_not_break_the_chat():
    cid = setup()

    def boom(d, s):
        raise RuntimeError("OCCT crashed")
    w = World(plans=[build_plan()], gen=boom)
    out = svc.run_turn(w.deps(), U, cid, "bracket")
    assert w.credits == 5 and out["part"] is None and out["assistant_message"]["meta"]["error"] is True


def test_validator_corrections_are_what_the_next_edit_starts_from():
    cid = setup()
    corrected = copy.deepcopy(BRACKET); corrected["parameters"]["thickness_mm"] = 6

    def gen(d, s):
        return {"success": True, "job_id": "j", "parameters": corrected,
                "validation": {"warnings": ["thin"], "corrections": {"thickness_mm": {"old": 1, "new": 6}}}}
    w = World(plans=[build_plan()], gen=gen)
    out = svc.run_turn(w.deps(), U, cid, "bracket")
    assert out["part"]["spec"]["parameters"]["thickness_mm"] == 6
    assert "thin" in out["part"]["notes"] and "thickness_mm: 1 to 6" in out["part"]["notes"]
    assert "adjusted" in out["assistant_message"]["content"].lower()


def test_unknown_chat_and_other_users_chat():
    fresh_store()
    cid = cs.create_chat(U)["id"]
    w = World(plans=[build_plan()])
    for who, chat in (("0xother", cid), (U, "missing")):
        try:
            svc.run_turn(w.deps(), who, chat, "hi")
            raise AssertionError("expected ChatNotFound")
        except svc.ChatNotFound:
            pass
    assert w.generated == [] and w.credits == 5


def test_second_message_while_busy_is_rejected_without_building():
    cid = setup()
    w = World(plans=[build_plan()])
    lock = svc._lock_for(cid)
    lock.acquire()
    try:
        try:
            svc.run_turn(w.deps(), U, cid, "again")
            raise AssertionError("expected ChatBusy")
        except svc.ChatBusy:
            pass
    finally:
        lock.release()
    assert w.credits == 5 and w.generated == [] and cs.get_chat(U, cid)["messages"] == []


# ---------------------------------------------------------------- Generate (the only charge)

def _preview(w, cid):
    return svc.run_turn(w.deps(), U, cid, "bracket")["part"]


def test_generate_charges_once_builds_the_files_and_unlocks():
    cid, w = setup(), World(plans=[build_plan()])
    part = _preview(w, cid)
    out = svc.unlock_part(w.deps(), U, cid, part["id"])
    assert out == {"paid": True, "charged": 1, "balance": 4, "job_id": "job1", "part_id": part["id"]}
    assert w.credits == 4 and w.built == ["job1"]


def test_pressing_generate_again_does_not_charge_again():
    cid, w = setup(), World(plans=[build_plan()])
    part = _preview(w, cid)
    svc.unlock_part(w.deps(), U, cid, part["id"])
    again = svc.unlock_part(w.deps(), U, cid, part["id"])
    assert again["charged"] == 0 and again["paid"] is True and w.credits == 4 and w.charges == 1


def test_each_version_is_paid_separately():
    cid, w = setup(), World(plans=[build_plan()])
    v1 = _preview(w, cid)
    w.plans.append(edit_plan(copy.deepcopy(BRACKET)))
    v2 = svc.run_turn(w.deps(), U, cid, "tweak")["part"]
    svc.unlock_part(w.deps(), U, cid, v2["id"])
    assert w.credits == 4 and "job1" not in w.paid and "job2" in w.paid
    svc.unlock_part(w.deps(), U, cid, v1["id"])
    assert w.credits == 3


def test_generate_without_credits_builds_nothing():
    cid, w = setup(), World(credits=0, plans=[build_plan()])
    part = _preview(w, cid)
    try:
        svc.unlock_part(w.deps(), U, cid, part["id"])
        raise AssertionError("expected NeedCredits")
    except svc.NeedCredits as exc:
        assert exc.balance == 0
    assert w.built == [] and w.paid == set()


def test_generate_refunds_when_the_files_cannot_be_built():
    cid, w = setup(), World(plans=[build_plan()], build_error=RuntimeError("OCCT crashed"))
    part = _preview(w, cid)
    try:
        svc.unlock_part(w.deps(), U, cid, part["id"])
        raise AssertionError("expected FilesFailed")
    except svc.FilesFailed:
        pass
    assert w.credits == 5 and w.refunds == ["job1"] and w.paid == set()


def test_generate_rejects_unknown_parts_and_other_users():
    cid, w = setup(), World(plans=[build_plan()])
    part = _preview(w, cid)
    for who, chat, pid, exc in ((U, cid, "missing", svc.PartNotFound), ("0xother", cid, part["id"], svc.ChatNotFound),
                                (U, "missing", part["id"], svc.ChatNotFound)):
        try:
            svc.unlock_part(w.deps(), who, chat, pid)
            raise AssertionError("expected " + exc.__name__)
        except exc:
            pass
    assert w.credits == 5 and w.built == []


def test_a_higher_ai_tier_costs_more_to_generate_and_refunds_exactly_that():
    cid, w = setup(), World(credits=30, plans=[build_plan()], cost=25)
    part = _preview(w, cid)
    out = svc.unlock_part(w.deps(), U, cid, part["id"])
    assert out["charged"] == 25 and w.credits == 5
    cid2, w2 = setup(), World(credits=30, plans=[build_plan()], cost=25, build_error=RuntimeError("x"))
    part2 = _preview(w2, cid2)
    try:
        svc.unlock_part(w2.deps(), U, cid2, part2["id"])
        raise AssertionError("expected FilesFailed")
    except svc.FilesFailed:
        pass
    assert w2.credits == 30


def test_not_enough_credits_for_a_higher_price_reports_the_price():
    cid, w = setup(), World(credits=24, plans=[build_plan()], cost=25)
    part = _preview(w, cid)
    try:
        svc.unlock_part(w.deps(), U, cid, part["id"])
        raise AssertionError("expected NeedCredits")
    except svc.NeedCredits as exc:
        assert exc.balance == 24 and exc.cost == 25
    assert w.built == [] and w.credits == 24


# --- credits for conversation: an AI answer costs credits, a preview does not, and nothing runs at zero ---

def answer_plan(reply="Use 3 mm for a bracket this size.", source="llm"):
    return Plan("answer", reply, None, source)


def test_an_ai_answer_costs_one_credit():
    cid, w = setup(), World(plans=[answer_plan()], answer_cost=1, uses_ai=True)
    out = svc.run_turn(w.deps(), U, cid, "what thickness should a 50 mm motor bracket be?")
    assert out["charged"] == 1 and w.credits == 4 and out["balance"] == 4 and out["part"] is None
    assert out["assistant_message"]["content"] == "Use 3 mm for a bracket this size."


def test_a_preview_built_by_the_ai_is_still_free():
    cid, w = setup(), World(plans=[build_plan()], answer_cost=1, uses_ai=True)
    out = svc.run_turn(w.deps(), U, cid, "a bracket for a motor")
    assert out["charged"] == 0 and w.credits == 5 and out["part"]["version"] == 1


def test_a_reply_from_the_built_in_rules_is_free():
    cid, w = setup(), World(plans=[answer_plan("Which size?", source="rules")], answer_cost=1, uses_ai=True)
    out = svc.run_turn(w.deps(), U, cid, "bracket")
    assert out["charged"] == 0 and w.credits == 5


def test_no_credits_stops_an_ai_message_before_the_model_is_asked():
    cid, w = setup(), World(credits=0, plans=[build_plan()], answer_cost=1, uses_ai=True)
    out = svc.run_turn(w.deps(), U, cid, "a bracket for a motor")
    assert out["needs_credits"] is True and out["assistant_message"]["meta"]["needs_credits"] is True
    assert w.seen_history == [] and w.generated == [] and out["part"] is None
    assert "upgrade" in out["assistant_message"]["content"].lower()


def test_the_engine_plan_is_never_blocked_at_zero_credits():
    cid, w = setup(), World(credits=0, plans=[Plan("build", "", copy.deepcopy(BRACKET), "rules")], answer_cost=1, uses_ai=False)
    out = svc.run_turn(w.deps(), U, cid, "a bracket for a motor")
    assert out["needs_credits"] is False and out["part"]["version"] == 1 and out["charged"] == 0


def test_the_last_credit_pays_for_one_answer_then_the_next_message_asks_to_upgrade():
    cid, w = setup(), World(credits=1, plans=[answer_plan(), answer_plan("again")], answer_cost=1, uses_ai=True)
    first = svc.run_turn(w.deps(), U, cid, "what material suits this?")
    assert first["charged"] == 1 and w.credits == 0 and first["needs_credits"] is False
    second = svc.run_turn(w.deps(), U, cid, "and the thickness?")
    assert second["needs_credits"] is True and second["charged"] == 0 and len(w.seen_history) == 1


def test_noting_a_requirement_is_not_charged():
    cid, w = setup(), World(plans=[answer_plan("")], answer_cost=1, uses_ai=True)
    out = svc.run_turn(w.deps(), U, cid, "must fit in 100 x 80 x 60 mm")
    assert out["new_requirements"] and out["charged"] == 0 and w.credits == 5
