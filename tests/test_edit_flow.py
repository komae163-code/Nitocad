"""The edit loop end to end with fakes: planner gets the measured facts, the reply and the stored
message carry a before and after diff, and a shaft edit carries through to the parts on it."""
import copy
import json

from _chat_fakes import fresh_store

import chat_service as svc
import chat_store as cs
import conversation as conv
import studio_templates as st
from conversation import Plan

U = "0xuser"


def _asm():
    return st.spec_of(st.get("asm-gear-shaft"))


def _checks(overlaps=(), mass=30.0, size=(60, 22, 22)):
    return {"overall_size_mm": list(size), "mass_properties": {"mass_g": mass, "complete": True},
            "bom": {"part_count": 2}, "interferences": [{"a": a, "b": b, "volume_mm3": v, "severity": s} for a, b, v, s in overlaps],
            "disconnected_groups": [], "unchecked_pairs": [], "notes": [], "clean": not overlaps}


class World:
    def __init__(self, plans, checks, plan_takes_facts=True):
        self.plans, self.checks, self.calls, self.seen = list(plans), list(checks), 0, []
        self.plan_takes_facts = plan_takes_facts

    def deps(self):
        if self.plan_takes_facts:
            def plan(history, spec, facts=None):
                self.seen.append(facts)
                return self.plans.pop(0)
        else:
            def plan(history, spec):
                self.seen.append("two-arg planner")
                return self.plans.pop(0)

        def generate(desc, spec, u, source):
            n = self.calls
            self.calls += 1
            return {"success": True, "job_id": f"job{n + 1}", "parameters": copy.deepcopy(spec),
                    "validation": {"warnings": [], "corrections": {}}, "assembly_checks": self.checks[n]}

        return svc.Deps(plan=plan, generate=generate, balance=lambda u: 5)


def _edited(**shaft):
    s = _asm()
    next(p for p in s["parameters"]["parts"] if p["name"] == "shaft")["parameters"].update(shaft)
    return s


def _two_turns(world, second_text="make the shaft 80 mm long"):
    fresh_store()
    cid = cs.create_chat(U)["id"]
    svc.run_turn(world.deps(), U, cid, "a gear on a shaft, mass under 40 g")
    return svc.run_turn(world.deps(), U, cid, second_text), cid


def test_planner_is_given_the_measured_facts_and_requirement_status():
    w = World([Plan("build", "Built.", _asm(), "llm"), Plan("edit", "Done.", _edited(length_mm=80), "llm", [])],
              [_checks(), _checks()])
    _two_turns(w)
    assert w.seen[0] is None                                   # nothing built yet
    facts = w.seen[1]
    assert facts["size_mm"] == [60, 22, 22] and facts["mass_g"] == 30.0 and facts["parts"] == 2 and facts["overlaps"] == []
    assert facts["requirements"][0]["status"] == "pass"


def test_a_planner_that_only_takes_two_arguments_still_works():
    w = World([Plan("build", "Built.", _asm(), "llm"), Plan("edit", "Done.", _edited(length_mm=80), "llm", [])],
              [_checks(), _checks()], plan_takes_facts=False)
    out, _ = _two_turns(w)
    assert w.seen == ["two-arg planner", "two-arg planner"] and out["part"]["version"] == 2


def test_an_edit_returns_a_diff_and_stores_it_on_the_message():
    w = World([Plan("build", "Built.", _asm(), "llm"),
               Plan("edit", "Done.", _edited(length_mm=80), "llm", ["shaft length_mm: 60 to 80"])],
              [_checks(), _checks(size=(80, 22, 22), mass=38.0)])
    out, cid = _two_turns(w)
    d = out["diff"]
    assert d["from_version"] == 1 and d["to_version"] == 2 and not d["worse"]
    assert d["spec"] == ["shaft length_mm: 60 to 80"]
    assert {r["label"] for r in d["facts"]} == {"Overall size", "Mass"}
    assert out["assistant_message"]["meta"]["diff"] == d
    saved = cs.get_chat(U, cid)["messages"][-1]
    assert saved["meta"]["diff"]["to_version"] == 2           # still there when the chat is loaded again


def test_the_spec_diff_comes_from_what_was_built_so_positions_show():
    new = _edited(length_mm=80)
    next(p for p in new["parameters"]["parts"] if p["name"] == "gear")["position"] = [0.0, 0.0, 28.0]
    w = World([Plan("build", "Built.", _asm(), "llm"), Plan("edit", "Done.", new, "llm", [])], [_checks(), _checks()])
    out, _ = _two_turns(w)
    assert "gear position: (0, 0, 20) to (0, 0, 28)" in out["diff"]["spec"]


def test_a_new_overlap_and_a_broken_requirement_are_called_out():
    w = World([Plan("build", "Built.", _asm(), "llm"), Plan("edit", "Done.", _edited(diameter_mm=10), "llm", [])],
              [_checks(), _checks(overlaps=[("gear", "shaft", 12.5, "major")], mass=45.0)])
    out, _ = _two_turns(w, "make the shaft 10 mm")
    text = out["assistant_message"]["content"]
    assert out["diff"]["worse"]
    assert "Check this: new overlap between gear and shaft" in text
    assert "breaks: " in text and text.count("mass") >= 1       # the requirement sentence, stated once
    assert {r["direction"] for r in out["diff"]["requirements"]} == {"worse"}


def test_a_first_build_has_no_diff():
    fresh_store()
    cid = cs.create_chat(U)["id"]
    w = World([Plan("build", "Built.", _asm(), "llm")], [_checks()])
    out = svc.run_turn(w.deps(), U, cid, "a gear on a shaft")
    assert out.get("diff") is None and "diff" not in out["assistant_message"]["meta"]


# ---- the planner itself -------------------------------------------------------------------

def test_the_model_sees_the_facts_in_its_prompt():
    seen = []

    def llm(msgs):
        seen.append(msgs[0]["content"])
        return json.dumps({"intent": "edit", "reply": "Done.", "spec": _edited(length_mm=80)})

    facts = conv.facts_digest({"size_mm": [60, 22, 22], "mass_g": 30.0, "assembly": {"bom": {"part_count": 2}, "interferences": [
        {"a": "gear", "b": "shaft", "volume_mm3": 3, "severity": "minor"}]}},
        [{"text": "mass at most 40 g", "status": "pass", "detail": "mass 30 g"}])
    conv.plan_turn([{"role": "user", "content": "make the shaft 80 mm long"}], _asm(), llm=llm, facts=facts)
    prompt = seen[0]
    assert "MEASURED FACTS of that version" in prompt and '"mass_g": 30.0' in prompt and "gear" in prompt and "mass at most 40 g" in prompt
    conv.plan_turn([{"role": "user", "content": "hi"}], _asm(), llm=llm)
    assert "MEASURED FACTS of that version" not in seen[1]


def test_facts_digest_is_small_and_empty_when_there_is_nothing():
    assert conv.facts_digest(None) is None and conv.facts_digest({}) is None
    big = {"size_mm": [1, 2, 3], "assembly": {"bom_csv": "x" * 5000, "parts": [{"n": i} for i in range(500)],
                                               "interferences": [{"a": str(i), "b": "z", "volume_mm3": 1, "severity": "minor"} for i in range(40)]}}
    d = conv.facts_digest(big)
    assert len(d["overlaps"]) == 8 and len(json.dumps(d)) < 1500


def test_an_ai_that_only_changes_the_shaft_still_gets_a_fitted_assembly():
    def llm(msgs):
        return json.dumps({"intent": "edit", "reply": "The shaft is now 10 mm.", "spec": _edited(diameter_mm=10)})

    plan = conv.plan_turn([{"role": "user", "content": "make the shaft 10 mm"}], _asm(), llm=llm)
    gear = next(p for p in plan.spec["parameters"]["parts"] if p["name"] == "gear")
    assert gear["parameters"]["bore_diameter_mm"] == 10.2
    assert "gear bore_diameter_mm: 8.2 to 10.2 (follows shaft diameter)" in plan.changes
    assert plan.reply.startswith("The shaft is now 10 mm.") and "Also updated to match" in plan.reply


def test_the_rules_planner_edits_assemblies_by_naming_the_part():
    hist = lambda t: [{"role": "user", "content": t}]
    p = conv.plan_turn(hist("make the shaft 80 mm long"), st.spec_of(st.get("asm-shaft-bearings")))
    assert p.intent == "edit" and p.source == "rules"
    parts = {x["name"]: x for x in p.spec["parameters"]["parts"]}
    assert parts["shaft"]["parameters"]["length_mm"] == 80 and parts["bearing_right"]["position"][2] == 68
    p = conv.plan_turn(hist("use 30 teeth"), _asm())
    assert p.intent == "edit" and next(x for x in p.spec["parameters"]["parts"] if x["name"] == "gear")["parameters"]["teeth"] == 30
    p = conv.plan_turn(hist("make it thicker"), _asm())            # no part named, no number: asks for an AI, changes nothing
    assert p.intent == "answer"
    p = conv.plan_turn(hist("make it 150mm long"), _asm())         # "it" names no part: never guessed, even though only one part has a length
    assert p.intent == "answer"
    p = conv.plan_turn(hist("make the assembly 150mm long"), _asm())
    assert p.intent == "answer"
    p = conv.plan_turn(hist("make the gear bigger"), _asm())       # would scale the bore too and loosen the fit
    assert p.intent == "answer"
    p = conv.plan_turn(hist("make the gear thicker"), _asm())
    assert p.intent == "edit" and next(x for x in p.spec["parameters"]["parts"] if x["name"] == "gear")["parameters"]["thickness_mm"] == 12


def test_label_changes_borrows_the_reason_when_the_line_matches():
    built = ["gear bore_diameter_mm: 8.2 to 10.2", "shaft diameter_mm: 8 to 10"]
    planned = ["gear bore_diameter_mm: 8.2 to 10.2 (follows shaft diameter)"]
    assert conv.label_changes(built, planned) == ["gear bore_diameter_mm: 8.2 to 10.2 (follows shaft diameter)", "shaft diameter_mm: 8 to 10"]
