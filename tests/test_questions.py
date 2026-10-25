"""Normal questions are answered in chat and never lead to a build or a charge."""
import conversation as cv


def _parse(text):          # stands in for the regex parser: it never finds a part
    class P:  # noqa: D401
        part_type = "unknown"
        parameters = {}
    return P()


def test_looks_like_question():
    assert cv.looks_like_question("What is the difference between steel and aluminium?")
    assert cv.looks_like_question("how thick should a bracket be")
    assert cv.looks_like_question("is this strong enough?")
    assert not cv.looks_like_question("L-bracket 50 by 60 mm, 3 mm thick")


def test_engine_tier_explains_instead_of_guessing_a_part():
    plan = cv.rules_plan("What is a fillet?", None, _parse)
    assert plan.intent == "answer" and plan.spec is None
    assert "Basic or higher" in plan.reply and "never cost credits" in plan.reply


def test_a_question_about_the_current_part_does_not_edit_it():
    spec = {"part_type": "l_bracket", "parameters": {"thickness_mm": 3}, "material": None, "operations": []}
    plan = cv.rules_plan("Is 3 mm thick enough?", spec, _parse)
    assert plan.intent == "answer" and plan.spec is None


def test_a_build_request_is_still_a_build():
    class Parsed:
        part_type = "l_bracket"
        parameters = {"width_mm": 50}
        material = None
        operations = []
    plan = cv.rules_plan("L-bracket 50 mm wide", None, lambda t: Parsed())
    assert plan.intent == "build" and plan.spec["part_type"] == "l_bracket"


def test_the_ai_prompt_allows_free_normal_questions():
    prompt = cv.build_system_prompt("")
    assert "Answer any normal question" in prompt and "never ask them to build or generate" in prompt
