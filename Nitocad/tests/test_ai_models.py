"""ai_models: which AIs are on offer, what they cost, and how messages are shaped for Claude."""
from types import SimpleNamespace

import ai_models as am


def cfg(**kw):
    base = dict(
        AI_MESSAGES_PER_DAY_BASIC=0, AI_MESSAGES_PER_DAY_PLUS=0, AI_MESSAGES_PER_DAY_ADVANCED=0,
        AI_MESSAGES_PER_DAY_PAID_BASIC=0, AI_MESSAGES_PER_DAY_PAID_PLUS=0, AI_MESSAGES_PER_DAY_PAID_ADVANCED=0,
        GENERATE_CREDITS_ENGINE=2, GENERATE_CREDITS_BASIC=10, GENERATE_CREDITS_PLUS=25, GENERATE_CREDITS_ADVANCED=40,
        OPENAI_API_KEY=None, OPENAI_MODEL=None, QWEN_API_KEY=None, QWEN_MODEL=None,
        ANTHROPIC_API_KEY=None, ANTHROPIC_MODEL="claude-sonnet-5-5", DEEPSEEK_API_KEY=None,
        AI_TIMEOUT_SECONDS=30.0, DEEPSEEK_BASE_URL="u", DEEPSEEK_DEFAULT_MODEL="m", DEEPSEEK_TIMEOUT_SECONDS=30.0,
        CHAT_PLANNER_MODEL=None, OPENAI_BASE_URL="u", QWEN_BASE_URL="u",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_prices_and_allowances_by_tier():
    s = cfg()
    assert am.TIERS == ("engine", "basic", "plus", "advanced")
    assert [am.messages_per_day(s, t) for t in am.TIERS] == [None, None, None, None]     # no daily limit on any plan
    assert [am.generate_credits(s, t) for t in am.TIERS] == [2, 10, 25, 40]


def test_unknown_or_missing_tiers_are_priced_as_basic():
    s = cfg()
    assert am.generate_credits(s, None) == 10 and am.generate_credits(s, "gold") == 10
    assert am.messages_per_day(s, "gold") is None


def test_engine_needs_no_key_has_no_limit_and_calls_no_ai():
    spec = am.find(cfg(), "engine:rules")
    assert spec.available and spec.tier == "engine" and am.messages_per_day(cfg(), "engine") is None
    assert am.make_llm(spec, cfg()) is None and am.model_name(spec, cfg()) is None


def test_a_version_is_priced_by_what_actually_planned_it():
    s = cfg()
    gpt, engine = am.find(s, "plus:gpt"), am.find(s, "engine:rules")
    assert am.pricing_tier(gpt, "llm") == "plus"
    assert am.pricing_tier(gpt, "rules") == "engine"      # the AI failed, the rules took over
    assert am.pricing_tier(engine, "rules") == "engine"
    assert am.generate_credits(s, am.pricing_tier(gpt, "rules")) == 2


def test_basic_is_always_available_and_the_default():
    spec = am.find(cfg(), None)
    assert spec.id == "basic:deepseek" and spec.available is True


def test_a_model_needs_both_its_key_and_its_model_name():
    assert not am.find(cfg(), "plus:gpt").available
    assert not am.find(cfg(OPENAI_API_KEY="k"), "plus:gpt").available
    assert am.find(cfg(OPENAI_API_KEY="k", OPENAI_MODEL="m"), "plus:gpt").available
    assert not am.find(cfg(QWEN_API_KEY="k"), "plus:qwen").available
    assert am.find(cfg(QWEN_API_KEY="k", QWEN_MODEL="m"), "plus:qwen").available
    assert not am.find(cfg(), "advanced:claude").available
    assert am.find(cfg(ANTHROPIC_API_KEY="k"), "advanced:claude").available


def test_plus_offers_gpt_and_qwen_and_unknown_ids_are_rejected():
    plus = [m.id for m in am.catalogue(cfg()) if m.tier == "plus"]
    assert plus == ["plus:gpt", "plus:qwen"]
    assert am.find(cfg(), "plus:llama") is None


def test_basic_without_a_deepseek_key_uses_the_free_rules_planner():
    assert am.make_llm(am.find(cfg(), None), cfg()) is None


def test_claude_messages_alternate_and_start_with_the_user():
    system, turns = am.to_anthropic([
        {"role": "system", "content": "rules"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
        {"role": "assistant", "content": "ok"}, {"role": "user", "content": "c"},
    ])
    assert system == "rules"
    assert [t["role"] for t in turns] == ["user", "assistant", "user", "assistant", "user"]
    assert turns[2]["content"] == "a\n\nb"


def test_claude_conversion_handles_a_lone_system_prompt():
    system, turns = am.to_anthropic([{"role": "system", "content": "rules"}])
    assert system == "rules" and turns[0]["role"] == "user"


def test_a_daily_limit_is_off_by_default_and_can_be_turned_back_on():
    s = cfg(AI_MESSAGES_PER_DAY_PLUS=15, AI_MESSAGES_PER_DAY_PAID_PLUS=150)
    assert am.messages_per_day(s, "basic") is None and am.messages_per_day(s, "basic", "pro") is None
    assert am.messages_per_day(s, "plus") == 15 and am.messages_per_day(s, "plus", "max") == 150
    assert am.messages_per_day(s, "engine") is None       # Engine calls no AI, so a limit never applies
