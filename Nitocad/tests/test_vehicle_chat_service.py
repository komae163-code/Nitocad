"""A vehicle through chat_service: free preview, free edit, one charge at Generate, refund on a failed build."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from _chat_fakes import fresh_store

import chat_service as svc
import chat_store as cs
import conversation
import spec_library as SL
import vehicle_chat as VC

U = "0xuser"
ROOT = Path(__file__).resolve().parent.parent


class Wallet:
    def __init__(self, credits=5, cost=3, build_error=None):
        self.credits, self.cost, self.paid, self.refunds, self.build_error = credits, cost, set(), [], build_error
        self.ai_used = 0

    def deps(self, llm=None):
        def plan(history, spec, facts=None):
            return conversation.plan_turn(history, spec, llm=llm, facts=facts)

        def generate(desc, spec, user_id, source):
            # What the real generator returns for an assembly: only the parts list and the layout.
            p = spec["parameters"]
            return {"success": True, "job_id": f"job{len(self.paid) + id(spec) % 1000}",
                    "parameters": {"part_type": "assembly", "parameters": {"parts": p["parts"], "layout": p["layout"]},
                                   "material": None, "operations": []},
                    "validation": {"warnings": [], "corrections": {}}}

        def charge_job(job_id, uid):
            if job_id in self.paid:
                return "already_paid", None
            if self.credits < self.cost:
                return "insufficient", None
            self.credits -= self.cost
            self.paid.add(job_id)
            return "charged", "purchased"

        def refund_job(job_id, uid):
            self.credits += self.cost
            self.paid.discard(job_id)
            self.refunds.append(job_id)

        def build_files(job_id):
            if self.build_error:
                raise self.build_error

        def used(u):
            self.ai_used += 1

        return svc.Deps(plan=plan, generate=generate, balance=lambda u: self.credits, ai_used=used,
                        job_cost=lambda j: self.cost, charge_job=charge_job, refund_job=refund_job,
                        build_files=build_files, uses_ai=llm is not None, answer_cost=0)


def setup():
    fresh_store()
    VC.set_library(_lib())
    return cs.create_chat(U)["id"]


def _lib():
    l = SL.SpecLibrary(SL.MemoryStore())
    l.seed_from_dir(ROOT / "vehicles" / "examples")
    return l


def test_a_vehicle_message_builds_a_free_preview_that_keeps_its_vehicle():
    chat = setup()
    w = Wallet()
    out = svc.run_turn(w.deps(), U, chat, "I want to create a wheelbarrow")
    assert out["charged"] == 0 and w.credits == 5
    part = out["part"]
    assert part["part_type"] == "assembly" and len(part["spec"]["parameters"]["parts"]) == 13
    assert part["spec"]["parameters"]["vehicle"]["name"] == "wheelbarrow"
    assert "Here is the plan for your wheelbarrow" in out["assistant_message"]["content"]
    VC.set_library(None)


def test_chatting_edits_the_plan_for_free_and_makes_version_two():
    chat = setup()
    w = Wallet()
    svc.run_turn(w.deps(), U, chat, "I want to create a wheelbarrow")
    out = svc.run_turn(w.deps(), U, chat, "make the wheel diameter 450 mm")
    assert out["charged"] == 0 and w.credits == 5
    assert out["part"]["version"] == 2 and out["intent"] == "edit"
    assert out["part"]["spec"]["parameters"]["vehicle"]["params"]["wheel_dia"]["default"] == 450
    out3 = svc.run_turn(w.deps(), U, chat, "make the tray length 900 mm")
    assert out3["part"]["version"] == 3 and out3["part"]["spec"]["parameters"]["vehicle"]["params"]["tray_len"]["default"] == 900
    assert out3["part"]["spec"]["parameters"]["vehicle"]["params"]["wheel_dia"]["default"] == 450   # earlier edit kept
    VC.set_library(None)


def test_only_generate_spends_credits_and_only_once():
    chat = setup()
    w = Wallet(credits=5, cost=3)
    out = svc.run_turn(w.deps(), U, chat, "I want to create a wheelbarrow")
    r1 = svc.unlock_part(w.deps(), U, chat, out["part"]["id"])
    r2 = svc.unlock_part(w.deps(), U, chat, out["part"]["id"])
    assert r1["charged"] == 3 and r2["charged"] == 0 and w.credits == 2
    VC.set_library(None)


def test_a_failed_file_build_refunds_the_generate_charge():
    chat = setup()
    w = Wallet(credits=5, cost=3, build_error=RuntimeError("kernel down"))
    out = svc.run_turn(w.deps(), U, chat, "I want to create a wheelbarrow")
    try:
        svc.unlock_part(w.deps(), U, chat, out["part"]["id"])
        raise AssertionError("should have failed")
    except svc.FilesFailed:
        pass
    assert w.credits == 5 and len(w.refunds) == 1
    VC.set_library(None)


def test_an_ai_vehicle_plan_counts_one_ai_message_and_costs_no_credits():
    chat = setup()
    w = Wallet()
    ex = json.loads((ROOT / "vehicles" / "examples" / "wheelbarrow.json").read_text())
    out = svc.run_turn(w.deps(llm=lambda messages: json.dumps(ex)), U, chat, "I want to create a wheelbarrow")
    assert out["plan_source"] == "llm" and w.ai_used == 1 and out["charged"] == 0 and w.credits == 5
    VC.set_library(None)
