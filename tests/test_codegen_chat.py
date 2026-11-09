"""Code parts in the Studio chat: when a message goes to the code builder, what gets saved, and which Studio checks
work on the saved part. No FastAPI, no CadQuery, no network: the code builder is replaced by fakes."""
import copy

import numpy as np
import pytest

import chat_service as svc
import chat_store as cs
import codegen_chat as cg
import conversation
import requirements as reqmod
from _chat_fakes import BRACKET, fresh_store
from conversation import Plan

U = "0xuser"
STATS = {"size": [40.0, 30.0, 20.0], "volume": 24000.0, "solids": 1, "valid": True}
BOX_TRIS = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
            [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]


def box_mesh(x=40.0, y=30.0, z=20.0):
    v = np.array([[0, 0, 0], [x, 0, 0], [x, y, 0], [0, y, 0], [0, 0, z], [x, 0, z], [x, y, z], [0, y, z]], float)
    t = np.array(BOX_TRIS)
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    flip = np.einsum("ij,ij->i", n, v[t].mean(axis=1) - v.mean(axis=0)) < 0
    t[flip] = t[flip][:, ::-1]
    return v, t


class World:
    """Scripted planner plus a fake code builder. Records every call so the tests can say what was and was not used."""

    def __init__(self, plans=None, credits=50, ok=True, message="", needs_credits=False, mesh=True, with_codegen=True):
        self.plans, self.credits, self.mesh = list(plans or []), credits, mesh
        self.ok, self.message, self.needs_credits = ok, message, needs_credits
        self.plan_calls, self.started, self.revised, self.mesh_calls, self.ai_logged = [], [], [], [], 0
        self.charged_checks = []
        self.with_codegen = with_codegen

    def _out(self, n):
        if not self.ok:
            return {"ok": False, "message": self.message, "needs_credits": self.needs_credits}
        return {"ok": True, "id": f"cg{n}", "summary": "A bracket shaped like a lightning bolt.", "values": {"width": 40.0},
                "stats": STATS, "params": [{"name": "width"}, {"name": "slot"}], "charged": 3}

    def deps(self):
        def plan(history, spec, facts=None):
            self.plan_calls.append(spec)
            return self.plans.pop(0)

        def generate(desc, spec, u, source):
            return {"success": True, "job_id": "job1", "parameters": copy.deepcopy(spec),
                    "validation": {"warnings": [], "corrections": {}}}

        def start(u, prompt):
            self.started.append(prompt)
            return self._out(len(self.started))

        def revise(u, model_id, instruction):
            self.revised.append((model_id, instruction))
            out = self._out(len(self.started) + len(self.revised))
            return out

        def mesh(u, ref):
            self.mesh_calls.append(ref)
            if not self.mesh:
                raise RuntimeError("kernel busy")
            return box_mesh()

        def charge(u, amount):
            if self.credits < amount:
                return None
            self.credits -= amount
            self.charged_checks.append(amount)
            return "purchased"

        def ai_used(u):
            self.ai_logged += 1

        kw = dict(codegen_start=start, codegen_revise=revise, codegen_mesh=mesh) if self.with_codegen else {}
        return svc.Deps(plan=plan, generate=generate, balance=lambda u: self.credits, ai_used=ai_used,
                        charge_credits=charge, refund_credits=lambda *a: None, check_cost=5, **kw)


def no_match(source="rules"):
    return Plan("answer", "I could not tell which part you want yet.", None, source, no_match=True)


def setup():
    fresh_store()
    return cs.create_chat(U)["id"]


# ------------------------------------------------------------------------------------------- pure rules

def test_a_polite_request_is_an_edit_and_a_real_question_is_not():
    assert not cg.is_question("can you make the hole bigger?")
    assert cg.is_question("what is the volume?")
    assert cg.is_question("is it printable")


def test_asking_for_code_and_new_parts_are_recognised():
    assert cg.asks_for_code("write code for a lightning bolt bracket")
    assert cg.asks_for_code("use codegen for this")
    assert not cg.asks_for_code("make it 5 mm thick")
    assert cg.looks_like_build("make a bracket shaped like a lightning bolt")
    assert not cg.looks_like_build("hello")
    assert not cg.looks_like_build("what can you make for me?")


def test_routing_before_the_planner():
    plain = copy.deepcopy(BRACKET)
    code = cg.make_spec("cg1", {"width": 40.0})
    assert cg.pre_plan_mode("make it thicker", code) == "revise"
    assert cg.pre_plan_mode("what is its weight?", code) is None
    assert cg.pre_plan_mode("start over with a new part", code) is None
    assert cg.pre_plan_mode("write code for a spring", plain) == "start"
    assert cg.pre_plan_mode("make it thicker", plain) is None


def test_the_spec_round_trips_and_ignores_junk_values():
    spec = cg.make_spec("cg9", {"a": 1.5, "b": "x", "c": True})
    assert cg.ref(spec) == {"id": "cg9", "values": {"a": 1.5}}
    assert cg.ref(BRACKET) is None and cg.ref(None) is None


def test_facts_give_size_and_mass_when_a_material_is_set():
    assert cg.facts_from_stats(STATS, "aluminium 6061")["mass_g"] == 64.8
    plain = cg.facts_from_stats(STATS, None)
    assert plain["size_mm"] == [40.0, 30.0, 20.0] and plain["mass_g"] is None and "material" in plain["mass_note"]
    assert cg.facts_from_stats(None)["size_mm"] is None


def test_the_llm_custom_intent_and_the_rules_fallback_carry_the_no_match_flag():
    p = conversation.coerce_plan({"intent": "custom", "reply": "I will write it as code."}, None)
    assert p.intent == "answer" and p.spec is None and p.no_match and p.source == "llm"
    assert not conversation.coerce_plan({"intent": "answer", "reply": "Use 6061."}, None).no_match
    rules = conversation.plan_turn([{"role": "user", "content": "a flibbertigibbet with a wobble"}], None, llm=None, regex_parse=None)
    assert rules.intent == "answer" and rules.no_match


# ------------------------------------------------------------------------------------- through run_turn

def test_nothing_changes_when_the_code_builder_is_not_available():
    cid, w = setup(), World(plans=[no_match()], with_codegen=False)
    out = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")
    assert out["part"] is None and "could not tell" in out["assistant_message"]["content"]


def test_a_request_no_template_fits_becomes_a_saved_code_part():
    cid, w = setup(), World(plans=[no_match()])
    out = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")
    part = out["part"]
    assert w.started == ["make a bracket shaped like a lightning bolt"] and w.revised == []
    assert part["part_type"] == "codegen" and part["job_id"] is None and part["version"] == 1
    assert cg.ref(part["spec"]) == {"id": "cg1", "values": {"width": 40.0}}
    assert part["facts"]["size_mm"] == [40.0, 30.0, 20.0]
    assert out["charged"] == 3 and out["intent"] == "build"
    assert "wrote it as code" in out["assistant_message"]["content"] and "2 adjustable values" in out["assistant_message"]["content"]
    assert cs.latest_part(U, cid)["id"] == part["id"]


def test_an_llm_planner_that_already_counted_the_message_is_not_counted_twice():
    cid, w = setup(), World(plans=[no_match("llm")])
    svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")
    assert w.ai_logged == 1                      # the planner's own count, none added by the code builder
    cid2, w2 = setup(), World(plans=[no_match("rules")])
    svc.run_turn(w2.deps(), U, cid2, "make a bracket shaped like a lightning bolt")
    assert w2.ai_logged == 1                     # the rules planner called no AI, so the code builder's call is counted


def test_vague_chatter_is_not_sent_to_the_code_builder():
    cid, w = setup(), World(plans=[no_match()])
    out = svc.run_turn(w.deps(), U, cid, "hello")
    assert w.started == [] and out["part"] is None


def test_a_requirement_note_is_never_turned_into_a_part():
    cid, w = setup(), World(plans=[no_match()])
    out = svc.run_turn(w.deps(), U, cid, "it must weigh under 80 g")
    assert w.started == [] and out["part"] is None and out["new_requirements"]


def test_a_failed_code_build_saves_nothing_and_says_so():
    cid, w = setup(), World(plans=[no_match()], ok=False, message="The code could not be built.")
    out = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")
    assert out["part"] is None and out["charged"] == 0 and "could not be built" in out["assistant_message"]["content"]
    assert cs.latest_part(U, cid) is None


def test_running_out_of_allowance_asks_for_credits():
    cid, w = setup(), World(plans=[no_match()], ok=False, message="You have used your allowance.", needs_credits=True)
    out = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")
    assert out["needs_credits"] is True and out["part"] is None


def test_a_crash_in_the_code_builder_never_breaks_the_chat():
    cid, w = setup(), World(plans=[no_match()])
    deps = w.deps()
    deps.codegen_start = lambda u, p: (_ for _ in ()).throw(RuntimeError("boom"))
    out = svc.run_turn(deps, U, cid, "make a bracket shaped like a lightning bolt")
    assert out["part"] is None and "only charged when a part is saved" in out["assistant_message"]["content"]


def test_asking_for_code_outright_skips_the_planner_even_when_a_template_would_fit():
    cid, w = setup(), World(plans=[])
    out = svc.run_turn(w.deps(), U, cid, "write code for an L bracket 50 mm wide")
    assert w.plan_calls == [] and out["part"]["part_type"] == "codegen"


def test_an_edit_to_a_code_part_is_a_revision_and_never_reaches_the_planner():
    cid, w = setup(), World(plans=[no_match()])
    first = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")["part"]
    out = svc.run_turn(w.deps(), U, cid, "make the slots wider")
    assert w.revised == [("cg1", "make the slots wider")] and len(w.plan_calls) == 1
    assert out["part"]["version"] == 2 and out["part"]["part_type"] == "codegen" and out["intent"] == "edit"
    assert cg.ref(out["part"]["spec"])["id"] != cg.ref(first["spec"])["id"]
    assert "Updated the code" in out["assistant_message"]["content"]


def test_a_question_about_a_code_part_goes_to_the_planner_without_the_code_spec():
    cid, w = setup(), World(plans=[no_match(), Plan("answer", "About 65 g in aluminium.", None, "llm")])
    svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")
    out = svc.run_turn(w.deps(), U, cid, "how heavy would it be in aluminium?")
    assert w.revised == [] and w.plan_calls[-1] is None and out["part"] is None


def test_requirements_are_checked_against_a_code_part():
    cid, w = setup(), World(plans=[no_match()])
    svc.run_turn(w.deps(), U, cid, "it must fit inside 35 mm")
    out = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")
    # the first message was a requirement note, so only the second one is planned
    assert out["part"]["facts"]["size_mm"] == [40.0, 30.0, 20.0]
    assert out["requirements"] is not None


# ---------------------------------------------------------------- the Studio's own answers and checks

def test_can_this_be_printed_never_says_no_problems_for_a_shape_it_has_no_rules_for():
    part = {"part_type": "codegen", "spec": cg.make_spec("cg1", {}), "facts": {}}
    answer = svc.dfm_answer("can this be 3D printed?", part)
    assert answer and "manufacturability check" in answer and "no problems" not in answer.lower()
    imported = {"part_type": "imported", "spec": {"part_type": "imported", "parameters": {}, "material": None}, "facts": {}}
    assert "manufacturability check" in svc.dfm_answer("can this be machined?", imported)


def test_template_parts_still_get_the_rule_of_thumb_answer():
    part = {"part_type": "l_bracket", "spec": copy.deepcopy(BRACKET), "facts": {}}
    assert "manufacturability check" not in svc.dfm_answer("can this be 3D printed?", part)


def test_the_manufacturability_check_reads_the_mesh_of_a_code_part():
    cid, w = setup(), World(plans=[no_match()])
    part = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")["part"]
    out = svc.run_check(w.deps(), U, cid, part["id"], "fdm")
    ids = {c["id"]: c["status"] for c in out["report"]["checks"]}
    assert ids["closed"] == "pass" and ids["mesh_walls"] == "pass" and out["charged"] == 5
    assert w.mesh_calls == [{"id": "cg1", "values": {"width": 40.0}}]
    again = svc.run_check(w.deps(), U, cid, part["id"], "fdm")
    assert again["charged"] == 0                       # a saved report opens free, like any other part


def test_a_check_that_cannot_read_the_mesh_is_refused_and_never_charged():
    cid, w = setup(), World(plans=[no_match()], mesh=False)
    part = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")["part"]
    with pytest.raises(svc.CheckFailed):
        svc.run_check(w.deps(), U, cid, part["id"], "cnc")
    assert w.charged_checks == [] and w.credits == 50


def test_the_requirement_parser_reads_a_code_parts_facts():
    facts = cg.facts_from_stats(STATS, "aluminium 6061")
    reqs = reqmod.parse_requirements("it must weigh under 80 g")
    assert reqs and reqmod.evaluate([{**r, "id": "1"} for r in reqs], facts)


# ------------------------------------------------------------------------------------------------ the bridge

class FakeRefusal(Exception):
    """Shaped like FastAPI's HTTPException, which codegen_api raises for refusals."""
    def __init__(self, status_code, detail):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


PAYLOAD = {"id": "cg7", "summary": "A hook.", "values": {"w": 3.0}, "stats": STATS, "params": [{"name": "w"}], "charged": 4, "library": []}


class FakeService:
    def __init__(self, error=None, slow=0.0):
        self.error, self.slow, self.calls, self.cancelled, self.loops = error, slow, [], False, []

    async def generate(self, uid, prompt, model_id, previous):
        import asyncio
        self.loops.append(asyncio.get_running_loop())
        self.calls.append((uid, prompt, model_id, previous))
        try:
            if self.slow:
                await asyncio.sleep(self.slow)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error:
            raise self.error
        return dict(PAYLOAD)

    def owned(self, uid, cid):
        if cid == "missing":
            raise FakeRefusal(404, "Model not found.")
        return {"id": cid, "code": "# the saved code"}

    async def build(self, uid, cid, values, exports):
        import mesh_formats
        v, t = box_mesh()
        return {"id": cid}, {"stl": mesh_formats.stl_bytes(v, t)}, []


def test_the_bridge_returns_the_services_answer_in_the_shape_the_chat_expects():
    svc_ = FakeService()
    out = cg.Bridge(svc_, "basic:deepseek").start("0xu", "a hook")
    assert out == {"ok": True, "id": "cg7", "summary": "A hook.", "values": {"w": 3.0}, "stats": STATS,
                   "params": [{"name": "w"}], "charged": 4, "library": []}
    assert svc_.calls == [("0xu", "a hook", "basic:deepseek", None)]


def test_a_refusal_becomes_a_chat_message_and_out_of_allowance_asks_for_credits():
    out = cg.Bridge(FakeService(FakeRefusal(402, "You have used your allowance.")), None).start("0xu", "x")
    assert out == {"ok": False, "message": "You have used your allowance.", "needs_credits": True}
    out = cg.Bridge(FakeService(FakeRefusal(422, "The code could not be built.")), None).start("0xu", "x")
    assert out["ok"] is False and out["needs_credits"] is False


def test_a_real_fault_is_not_swallowed_as_a_refusal():
    with pytest.raises(ValueError):
        cg.Bridge(FakeService(ValueError("bug")), None).start("0xu", "x")


def test_a_revision_loads_the_saved_model_first_and_a_missing_one_is_a_message():
    svc_ = FakeService()
    cg.Bridge(svc_, None).revise("0xu", "cg1", "thicker")
    assert svc_.calls[0][3] == {"id": "cg1", "code": "# the saved code"}
    gone = cg.Bridge(svc_, None).revise("0xu", "missing", "thicker")
    assert gone["ok"] is False and "not found" in gone["message"].lower()


def test_the_bridge_reads_the_mesh_at_the_parts_own_values():
    v, t = cg.Bridge(FakeService(), None).mesh("0xu", {"id": "cg1", "values": {"w": 3.0}})
    assert v.shape[1] == 3 and t.shape[1] == 3 and abs(float(v[:, 0].max() - v[:, 0].min()) - 40.0) < 1e-3


def test_with_a_server_loop_the_call_runs_on_that_loop_not_a_second_one():
    import asyncio
    import threading
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        svc_ = FakeService()
        out = cg.Bridge(svc_, None, loop).start("0xu", "a hook")
        assert out["ok"] and svc_.loops == [loop]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)


def test_a_call_that_takes_too_long_is_cancelled_so_the_code_builder_can_refund():
    import asyncio
    import threading
    import time
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    old = cg.BRIDGE_TIMEOUT_S
    cg.BRIDGE_TIMEOUT_S = 0.2
    try:
        svc_ = FakeService(slow=30)
        with pytest.raises(Exception):
            cg.Bridge(svc_, None, loop).start("0xu", "a hook")
        for _ in range(50):
            if svc_.cancelled:
                break
            time.sleep(0.05)
        assert svc_.cancelled
    finally:
        cg.BRIDGE_TIMEOUT_S = old
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)


# ------------------------------------------------------------------------------------------ the material

def test_a_message_that_only_sets_the_material_is_recognised():
    assert cg.material_of("make it stainless steel") == "304 stainless"
    assert cg.material_of("use aluminium 6061") == "6061-T6"
    assert cg.material_of("brass please") == "brass"
    for not_just_material in ("make the slot wider in steel", "make it 5 mm thicker", "what material is best?", "thicker"):
        assert cg.material_of(not_just_material) is None
    code = cg.make_spec("cg1", {})
    assert cg.pre_plan_mode("make it aluminium", code) == "material"
    assert cg.pre_plan_mode("make the slot wider in steel", code) == "revise"


def test_setting_the_material_of_a_code_part_is_free_and_gives_it_a_mass():
    cid, w = setup(), World(plans=[no_match(), no_match()])
    svc.run_turn(w.deps(), U, cid, "it must weigh under 80 g")
    first = svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")["part"]
    assert first["facts"]["mass_g"] is None                      # no material yet, so the weight cannot be known
    out = svc.run_turn(w.deps(), U, cid, "make it aluminium")
    part = out["part"]
    assert out["charged"] == 0 and w.revised == [] and len(w.started) == 1 and w.ai_logged == 1
    assert part["version"] == 2 and part["job_id"] is None and part["spec"]["material"] == "6061-T6"
    assert cg.ref(part["spec"]) == cg.ref(first["spec"])         # same saved model, same sliders
    assert part["facts"]["mass_g"] == 64.8 and part["facts"]["size_mm"] == [40.0, 30.0, 20.0]
    assert any(r["status"] == "pass" for r in out["requirements"])
    assert "nothing was charged" in out["assistant_message"]["content"] and "64.8" in out["assistant_message"]["content"]


def test_the_material_survives_a_later_revision_of_the_code():
    cid, w = setup(), World(plans=[no_match()])
    svc.run_turn(w.deps(), U, cid, "make a bracket shaped like a lightning bolt")
    svc.run_turn(w.deps(), U, cid, "make it aluminium")
    out = svc.run_turn(w.deps(), U, cid, "make the slots wider")
    assert out["part"]["version"] == 3 and out["part"]["spec"]["material"] == "6061-T6"
    assert out["part"]["facts"]["mass_g"] == 64.8


# ------------------------------------------------------------------ a bare yes carries the earlier request along

CAR_CHAT = [("user", "I want to build a car"), ("assistant", "Before I build the car, I need a few choices"),
            ("user", "What kind of car? Pickup"), ("assistant", "Before I build the car, one more choice."),
            ("user", "How big is it? Full size, about 4 m long"), ("assistant", "Before I build the car, one more choice."),
            ("user", "Should the inside be modelled too? Yes, engine and gearbox too"),
            ("assistant", "That is beyond what the catalogue parts can form, so I will write it as code instead.")]


def test_a_message_that_names_its_own_shape_is_sent_as_written():
    assert cg.start_prompt("make a bracket shaped like a lightning bolt", [{"role": "user", "content": "I want a car"}]) \
        == "make a bracket shaped like a lightning bolt"
    assert cg.start_prompt("write code for a 40 mm spring", []) == "write code for a 40 mm spring"


def test_a_bare_yes_takes_the_request_and_the_answers_but_not_our_questions():
    hist = [{"role": r, "content": c} for r, c in CAR_CHAT] + [{"role": "user", "content": "Yes write it as code"}]
    p = cg.start_prompt("Yes write it as code", hist)
    assert "I want to build a car" in p and "Pickup" in p and "4 m long" in p and "engine and gearbox" in p
    assert "What kind of car?" not in p and "Yes write it as code" not in p and len(p) <= cg.PROMPT_LIMIT


def test_messages_before_a_saved_part_are_not_mixed_into_a_new_request():
    hist = [{"role": "user", "content": "a 5 mm washer"}, {"role": "assistant", "content": "done", "part_id": "p1"},
            {"role": "user", "content": "write it as code"}]
    assert cg.start_prompt("write it as code", hist) == ""      # nothing to refer to: the chat asks, it never invents


def test_a_very_long_chat_keeps_the_first_request_and_the_newest_answers_inside_the_limit():
    hist = [{"role": "user", "content": "I want a gearbox"}] + [{"role": "user", "content": "x" * 400 + str(i)} for i in range(8)]
    p = cg.start_prompt("yes", hist)
    assert len(p) <= cg.PROMPT_LIMIT and "I want a gearbox" in p and p.rstrip().endswith("7")


def test_agreeing_to_code_after_a_car_chat_sends_the_car_to_the_builder_not_just_the_yes():
    cid, w = setup(), World(plans=[no_match()])
    for role, content in CAR_CHAT:
        cs.add_message(U, cid, role, content, max_messages=100)
    svc.run_turn(w.deps(), U, cid, "Yes write it as code")
    assert len(w.started) == 1
    assert "Pickup" in w.started[0] and "4 m long" in w.started[0] and "engine and gearbox" in w.started[0]
