"""A vehicle chat never falls through to the generic planner, and the code builder's rebuild request tolerates stray values."""
import pytest

import conversation as C
import vehicle_chat as VC

CAR = [{"role": "user", "content": "I want to build a car"},
       {"role": "assistant", "content": "Before I build the car, I need a few choices"},
       {"role": "user", "content": "What kind of car? Pickup"},
       {"role": "assistant", "content": "Before I build the car, one more choice."},
       {"role": "user", "content": "How big is it? Full size, about 4 m long"},
       {"role": "assistant", "content": "Before I build the car, one more choice."},
       {"role": "user", "content": "Should the inside be modelled too? Yes, engine and gearbox too"}]
CUSTOM = lambda messages: '{"intent":"custom","reply":"A full 4 m pickup is beyond the catalogue, so I will write it as code.","spec":null,"choices":[]}'


@pytest.mark.parametrize("vehicle_turn", ["none", "raises"])
def test_a_failed_vehicle_turn_says_so_and_never_becomes_a_code_request(monkeypatch, vehicle_turn):
    def boom(*a, **k):
        raise RuntimeError("planner exploded")
    monkeypatch.setattr(VC, "plan_vehicle_turn", (lambda *a, **k: None) if vehicle_turn == "none" else boom)
    plan = C.plan_turn(CAR, None, llm=CUSTOM)
    assert plan.intent == "answer" and not plan.no_match and plan.spec is None
    assert "could not plan that vehicle" in plan.reply and "nothing was charged" in plan.reply


def test_a_chat_that_is_not_about_a_vehicle_still_reaches_the_generic_planner(monkeypatch):
    monkeypatch.setattr(VC, "plan_vehicle_turn", lambda *a, **k: None)
    hist = [{"role": "user", "content": "a sculpted dolphin shaped paperweight"}]
    plan = C.plan_turn(hist, None, llm=CUSTOM)
    assert plan.no_match                                   # unchanged: this is what "custom" is for


def test_slider_values_are_cleaned_to_finite_numbers():
    api = pytest.importorskip("codegen_api")
    nan = float("nan")
    assert api.clean_params({"a": "12", "b": "", "c": None, "d": True, "e": nan, "f": 3, "g": "x", "h": 1e9}) == {"a": 12.0, "d": 1.0, "f": 3.0}
    assert api.RebuildBody(params={"w": "40", "empty": "", "none": None}).params == {"w": 40.0}
    assert api.ExportBody(params={"w": "40", "bad": "x"}).params == {"w": 40.0}
