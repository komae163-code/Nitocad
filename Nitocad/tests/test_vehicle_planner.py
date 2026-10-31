"""Pure-Python tests for vehicle_planner with a scripted fake model."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import spec_library as SL
import vehicle_planner as VP


def seeded():
    l = SL.SpecLibrary(SL.MemoryStore())
    l.seed_from_dir(ROOT / "vehicles" / "examples")
    return l


def good_spec():
    return json.loads((ROOT / "vehicles" / "examples" / "two_axle_cart.json").read_text()) | {"name": "my_cart"}


class Script:
    """Fake model: returns the next scripted reply each call and remembers what it was sent."""
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, messages):
        self.calls.append(copy.deepcopy(messages))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def test_first_try_success_stores_a_candidate_and_uses_examples():
    lib = seeded()
    llm = Script("```json\n" + json.dumps(good_spec()) + "\n```")
    r = VP.plan_vehicle("a small two axle cart", llm, lib)
    assert r["ok"] and r["attempts"] == 1 and r["spec"]["name"] == "my_cart"
    assert lib.get(r["candidate_id"])["status"] == "candidate"
    system = llm.calls[0][0]["content"]
    assert "PART TYPES" in system and "MODULES" in system and "wheel_on_axle" in system
    assert "two_axle_cart" in system or "PROVEN EXAMPLES" in system
    assert r["examples_used"]


def test_errors_are_fed_back_and_the_model_repairs_the_spec():
    bad = good_spec()
    bad["components"][0]["type"] = "warp_drive"
    llm = Script(json.dumps(bad), json.dumps(good_spec()))
    r = VP.plan_vehicle("a cart", llm, seeded())
    assert r["ok"] and r["attempts"] == 2
    retry_msg = llm.calls[1][-1]["content"]
    assert "warp_drive" in retry_msg and "not in the part catalog" in retry_msg
    assert [h["errors"] != [] for h in r["history"]] == [True, False]


def test_garbage_replies_exhaust_attempts_and_are_remembered_as_lessons():
    lib = seeded()
    llm = Script("sorry, I cannot", "{not json", "[1,2]")
    r = VP.plan_vehicle("a cart", llm, lib, max_attempts=3)
    assert not r["ok"] and r["attempts"] == 3 and len(r["errors"]) >= 1
    assert lib.store.get("lessons") is not None
    assert all(c["role"] != "assistant" or isinstance(c["content"], str) for c in llm.calls[-1])


def test_failing_design_check_gets_a_second_chance_then_is_reported():
    wide = json.loads((ROOT / "vehicles" / "examples" / "loco_0_4_0_core.json").read_text())
    wide["params"]["max_width"] = {"default": 30, "min": 10, "max": 500}            # impossible: overall width > 30
    llm = Script(json.dumps(wide), json.dumps(wide))
    r = VP.plan_vehicle("a loco", llm, seeded(), max_attempts=2)
    assert r["ok"] and r["attempts"] == 2
    assert [c["name"] for c in r["unresolved_checks"]] == ["overall_width_within_limit"]
    assert "did not pass" in llm.calls[1][-1]["content"]


def test_model_crash_is_reported_not_raised():
    r = VP.plan_vehicle("a cart", Script(RuntimeError("503 overloaded")), seeded())
    assert not r["ok"] and "503 overloaded" in r["errors"][0] and r["attempts"] == 1


def test_inputs_are_validated():
    lib = seeded()
    assert not VP.plan_vehicle("", Script(), lib)["ok"]
    assert not VP.plan_vehicle("x" * 5000, Script(), lib)["ok"]
    assert not VP.plan_vehicle("a cart", "not callable", lib)["ok"]
    llm = Script(*(["nope"] * 6))
    assert VP.plan_vehicle("a cart", llm, lib, max_attempts=99)["attempts"] == 6      # capped


def test_learning_loop_accepted_candidates_become_examples_and_lessons_reach_the_prompt():
    lib = seeded()
    spec = good_spec() | {"name": "trolley_cart"}
    r = VP.plan_vehicle("a trolley cart for the garage", Script(json.dumps(spec)), lib)
    assert lib.feedback(r["candidate_id"], True)["ok"]
    for _ in range(2):
        lib.record_failure("x", ["derived 'a' must be an expression starting with '='"])
    llm = Script(json.dumps(spec))
    r2 = VP.plan_vehicle("another trolley cart", llm, lib)
    system = llm.calls[0][0]["content"]
    assert r["candidate_id"] in r2["examples_used"]
    assert "MISTAKES SEEN BEFORE" in system and "must be an expression" in system


def test_extract_json_handles_fences_chatter_and_rejects_non_objects():
    assert VP.extract_json("Sure!\n```json\n{\"a\": 1}\n```\nHope that helps") == {"a": 1}
    for bad in ("", "no json here", "{broken", "[1, 2]"):
        try:
            VP.extract_json(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_plan_and_build_runs_the_whole_pipeline_with_a_fake_kernel(tmp_path):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_vehicle_build import fake_bbox, fake_export
    lib = seeded()
    r = VP.plan_and_build("a cart", Script(json.dumps(good_spec())), lib, tmp_path / "pkg",
                          build_bbox_fn=fake_bbox, build_export_fn=fake_export, build_work_dir=tmp_path / "w")
    assert r["ok"] and r["stage"] == "build"
    assert (tmp_path / "pkg" / "manifest.json").is_file()
    bad = VP.plan_and_build("a cart", Script("{nope", "{nope", "{nope"), lib, tmp_path / "x")
    assert not bad["ok"] and bad["stage"] == "plan" and not (tmp_path / "x").exists()


def test_an_ai_plan_is_stored_without_the_persons_words_and_free_text_stays_out_of_prompts():
    import vehicle_spec as VS
    lib = SL.SpecLibrary(SL.MemoryStore())
    seed = json.loads((ROOT / "vehicles" / "examples" / "wheelbarrow.json").read_text(encoding="utf-8"))
    seed["notes"] = "SECRET-NOTE-TEXT"
    lib.add(seed, "", status="accepted")
    msgs, _used = VP.build_messages("a wheelbarrow for my secret garden", lib, VS._default_catalog(), None, 2)
    prompt = json.dumps(msgs)
    assert "SECRET-NOTE-TEXT" not in prompt and "secret garden" not in " ".join(
        r["description"] for r in lib.all()), "the person's words and free-text notes must not reach other prompts"


# ------------------------------------------------------------------------------------------ time budget
class FakeClock:
    """A clock the fake model advances, so the time budget is tested without sleeping."""
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class SlowScript(Script):
    def __init__(self, clock, seconds_per_call, *replies):
        super().__init__(*replies)
        self.clock, self.seconds = clock, seconds_per_call

    def __call__(self, messages):
        self.clock.now += self.seconds
        return super().__call__(messages)


def test_planning_stops_retrying_when_the_time_budget_is_nearly_used():
    clock = FakeClock()
    llm = SlowScript(clock, 70, "not json", "still not json", json.dumps(good_spec()))
    r = VP.plan_vehicle("a small two axle cart", llm, seeded(), time_budget_s=120, clock=clock)
    # call 1 ends at 70 s (50 s left, enough for a retry), call 2 ends at 140 s (over budget), so no third call
    assert r["ok"] is False and r["timed_out"] is True and len(llm.calls) == 2
    assert any("time limit" in e for e in r["errors"])


def test_a_retry_that_still_fits_in_the_budget_runs():
    clock = FakeClock()
    llm = SlowScript(clock, 20, "not json", json.dumps(good_spec()))
    r = VP.plan_vehicle("a small two axle cart", llm, seeded(), time_budget_s=120, clock=clock)
    assert r["ok"] and r["attempts"] == 2 and r["timed_out"] is False


def test_a_good_first_plan_with_failing_checks_is_returned_when_time_runs_out():
    clock = FakeClock()
    spec = good_spec() | {"checks": [{"name": "always_fails", "expr": "=1 > 2", "message": "x"}]}
    llm = SlowScript(clock, 100, json.dumps(spec), json.dumps(spec))
    r = VP.plan_vehicle("a small two axle cart", llm, seeded(), time_budget_s=120, clock=clock)
    assert r["ok"] and r["timed_out"] is True and len(llm.calls) == 1
    assert [c["name"] for c in r["unresolved_checks"]] == ["always_fails"]


def test_no_budget_means_the_old_behaviour():
    clock = FakeClock()
    llm = SlowScript(clock, 500, "nope", "nope", json.dumps(good_spec()))
    r = VP.plan_vehicle("a small two axle cart", llm, seeded(), time_budget_s=None, clock=clock)
    assert r["ok"] and r["attempts"] == 3


def test_revising_a_vehicle_also_respects_the_time_budget():
    clock = FakeClock()
    llm = SlowScript(clock, 70, "not json", "still not json", json.dumps(good_spec()))
    r = VP.revise_vehicle(good_spec(), "make it longer", llm, seeded(), time_budget_s=120, clock=clock)
    assert r["ok"] is False and r["timed_out"] is True and len(llm.calls) == 2
