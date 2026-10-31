"""Pro and Max plans: weekly allowance, daily fallback, model access, lapse and renew."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import ai_models as ai
from config import settings


def test_plan_credits_and_model_access(tmp_db):
    db = tmp_db
    FB_PRO = settings.subscription_tiers["pro"]["fallback_daily_credits"]
    FB_MAX = settings.subscription_tiers["max"]["fallback_daily_credits"]
    W = "0x" + "aa"*20
    now = datetime.now(timezone.utc)
    iso = lambda d: d.isoformat()
    def sub(w, tier, days=25, status="active"):
        db.upsert_subscription(w, stripe_customer_id="c"+w[-4:], stripe_subscription_id="s"+w[-4:], status=status,
                               tier=tier, subscription_credits=0, current_period_end=iso(now+timedelta(days=days)))

    # free account: no plan, signup grant works and is one-time
    assert db.get_plan(W) == "free"
    assert db.grant_signup_bonus(W) is True and db.grant_signup_bonus(W) is False
    assert db.get_credit_balance(W) == settings.EMAIL_SIGNUP_BONUS_CREDITS == 250

    # pro: weekly 1800 appears once, however many times we look
    P = "0x" + "bb"*20
    sub(P, "pro")
    assert db.get_plan(P) == "pro"
    assert db.get_subscription_credits(P) == 1800
    for _ in range(5): db.ensure_plan_credits(P)
    assert db.get_subscription_credits(P) == 1800
    # spend order: weekly first, then the purchased top-up
    db.add_credits(P, 500)
    pool = db.charge_credits(P, 1900)
    assert pool.startswith("ledger:") and db.get_subscription_credits(P) == 0 and db.get_credit_balance(P) == 400
    db.refund_credits(P, pool, 1900)
    assert db.get_subscription_credits(P) == 1800 and db.get_credit_balance(P) == 500

    # week rollover on its own wallet (the simulated clock writes a future grant)
    V = "0x" + "99"*20
    sub(V, "pro"); assert db.get_subscription_credits(V) == 1800
    db.charge_credits(V, 1000)
    later = now + timedelta(days=8)
    db.ensure_plan_credits(V, later)
    assert db._ledger.balance(V, later, pool="subscription")["total"] == 1800   # last week's leftover is gone, new week granted

    # fallback: only the cheap model may use it, only after weekly+purchased are empty
    db.charge_credits(P, 1800); db.consume_credit(P, 500)
    assert db.get_subscription_credits(P) == 0 and db.get_credit_balance(P) == 0
    assert db._reserve_for_plan(P, 10, "plus") is None           # GPT/Qwen tier cannot use fallback
    assert db._reserve_for_plan(P, 10, None) is None
    got = db._reserve_for_plan(P, 10, "basic"); assert got and got.startswith("ledger:")
    assert db._ledger.balance(P.lower(), pool="fallback")["total"] == FB_PRO - 10
    db.refund_generation(P, got); assert db._ledger.balance(P.lower(), pool="fallback")["total"] == FB_PRO
    assert db.get_credit_balance(P) == 0                          # fallback never leaks into purchased balance
    assert db.consume_credit(P, 1) is False

    # charge_job end to end for a plan user
    jid = db.record_job(description="x", success=True, user_id=P, part_type="shaft", parameters={}, draft=True, ai_tier="basic")
    if True:
        st, pl = db.charge_job(jid, P, cost=10, ai_tier="basic")
        assert st == "charged" and pl.startswith("ledger:")
        assert db.refund_job_charge(jid, P) is True
        assert db._ledger.balance(P.lower(), pool="fallback")["total"] == FB_PRO

    # max plan amounts and fallback size
    M = "0x" + "cc"*20
    sub(M, "max"); assert db.get_subscription_credits(M) == 9000
    assert db._ledger.balance(M, pool="fallback")["total"] == FB_MAX

    # lapsed subscription: nothing granted, credits already granted stop at period end
    L = "0x" + "dd"*20
    sub(L, "pro", days=-1)
    assert db.get_plan(L) == "free" and db.get_subscription_credits(L) == 0

    # lapse then renew: weekly clock restarts
    R = "0x" + "ee"*20
    sub(R, "pro", days=10); a1 = db.get_subscription(R)["allowance_anchor"]
    db.upsert_subscription(R, stripe_customer_id="x", stripe_subscription_id="y", status="canceled",
                           tier="pro", subscription_credits=0, current_period_end=iso(now-timedelta(days=1)))
    sub(R, "pro", days=30)
    assert db.get_subscription(R)["allowance_anchor"] is None
    assert db.get_subscription_credits(R) == 1800
    # an ordinary mid-period renewal does not restart the clock
    a2 = db.get_subscription(R)["allowance_anchor"]; sub(R, "pro", days=60)
    assert db.get_subscription(R)["allowance_anchor"] == a2

    # legacy plan keeps old behaviour and full model access
    G = "0x" + "ff"*20
    sub(G, "starter"); db.upsert_subscription(G, stripe_customer_id="g", stripe_subscription_id="g", status="active",
        tier="starter", subscription_credits=3, current_period_end=iso(now+timedelta(days=5)))
    assert db.get_plan(G) == "legacy"

    # model access by plan
    cat = {m.provider: m for m in [ai.ModelSpec("a","basic","deepseek","D",True), ai.ModelSpec("b","plus","qwen","Q",True),
           ai.ModelSpec("c","plus","openai","G",True), ai.ModelSpec("d","advanced","anthropic","C",True), ai.ModelSpec("e","engine","rules","R",True)]}
    for plan, expect in {"free": {"deepseek","qwen","rules"}, "pro": {"deepseek","qwen","rules","openai"},
                         "max": set(cat), "legacy": set(cat)}.items():
        assert {p for p, m in cat.items() if ai.allowed_for_plan(plan, m)} == expect, plan
    assert ai.required_plan(cat["openai"]) == "pro" and ai.required_plan(cat["anthropic"]) == "max"


def test_limits_and_labels_follow_the_plan(tmp_db):
    # No plan has a daily message limit by default. A limit set above 0 in the environment still applies.
    for plan in ("free", "pro", "max", "legacy"):
        assert [ai.messages_per_day(settings, t, plan) for t in ai.TIERS] == [None] * 4
    from types import SimpleNamespace
    limited = SimpleNamespace(AI_MESSAGES_PER_DAY_BASIC=40, AI_MESSAGES_PER_DAY_PAID_BASIC=500)
    assert ai.messages_per_day(limited, "basic", "free") == 40 and ai.messages_per_day(limited, "basic", "pro") == 500
    assert ai.messages_per_day(limited, "engine", "max") is None
    mk = lambda provider: ai.ModelSpec("x", "basic", provider, "X", True)
    assert [ai.model_class(mk(p)) for p in ("rules", "deepseek", "qwen", "openai", "anthropic")] == \
        ["standard", "standard", "standard", "expert", "expert"]


def test_usage_meter_shows_percent_not_credits(tmp_db):
    db = tmp_db
    now = datetime.now(timezone.utc)
    W = "0x" + "12" * 20
    db.upsert_subscription(W, stripe_customer_id="c", stripe_subscription_id="s", status="active", tier="pro",
                           subscription_credits=0, current_period_end=(now + timedelta(days=25)).isoformat())
    m = db.get_ai_usage_meter(W)
    assert m["plan"] == "pro" and m["used_pct"] == 0 and m["standard_open"] is True and m["resets_at"]
    db.charge_credits(W, 900)
    assert db.get_ai_usage_meter(W)["used_pct"] == 50
    assert set(db.get_ai_usage_meter(W)) == {"plan", "used_pct", "resets_at", "standard_open"}

    F = "0x" + "34" * 20
    assert db.get_ai_usage_meter(F)["used_pct"] is None            # no grant yet, nothing to measure
    db.grant_signup_bonus(F)
    db.charge_credits(F, 125)                                       # half of the 250 signup credits
    assert db.get_ai_usage_meter(F) | {"resets_at": None} == {"plan": "free", "used_pct": 50, "resets_at": None, "standard_open": True}


def test_paid_accounts_get_the_wide_chat_limit(tmp_db):
    assert settings.CHAT_MAX_CHATS_PAID > 20 * settings.CHAT_MAX_CHATS_PER_USER
