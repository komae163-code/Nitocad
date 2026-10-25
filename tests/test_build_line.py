"""The 'Built in 1.2 s, no AI cost' line on template builds."""
import copy
from unittest import mock

from _chat_fakes import fresh_store

import chat_service as svc
import chat_store as cs

U = "0xuser"


def deps(fail=False):
    def generate(desc, spec, user, source):
        if fail:
            return {"success": False, "error": "boom"}
        return {"success": True, "job_id": "j1", "parameters": copy.deepcopy(spec), "validation": {"warnings": [], "corrections": {}}}
    return svc.Deps(plan=lambda h, s: None, generate=generate, balance=lambda u: 5)


def chat():
    fresh_store()
    return cs.create_chat(U)["id"]


def test_build_note_rounds_to_a_tenth_and_never_reads_zero():
    assert svc.build_note(1.234) == {"seconds": 1.2, "ai": False, "ai_cost": 0}
    assert svc.build_note(0.0)["seconds"] == 0.1


def test_template_build_reports_measured_time_and_no_ai():
    cid = chat()
    with mock.patch.object(svc.time, "perf_counter", side_effect=[10.0, 11.26]):
        out = svc.start_template_part(deps(), U, cid, "gear-20")
    assert out["build"] == {"seconds": 1.3, "ai": False, "ai_cost": 0}
    assert out["charged"] == 0


def test_the_line_is_saved_so_it_survives_reopening_the_chat():
    cid = chat()
    out = svc.start_template_part(deps(), U, cid, "gear-20")
    stored = cs.get_chat(U, cid)["messages"][-1]["meta"]
    assert stored["build"]["ai"] is False and stored["build"]["seconds"] == out["build"]["seconds"]


def test_a_failed_template_build_shows_no_line():
    cid = chat()
    out = svc.start_template_part(deps(fail=True), U, cid, "gear-20")
    assert out["part"] is None and out["build"] is None
