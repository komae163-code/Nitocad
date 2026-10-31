"""Credit rules on top of the Pro and Max plans: 250 signup credits, 1 credit per AI answer, no daily limits,
and the Standard fallback credits counting where they can actually pay."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import uuid

import ai_models
from config import settings


def _free():
    return "email:google-" + uuid.uuid4().hex


def _pro_wallet():
    return "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:8]


def _make_pro(db, wallet, days=25):
    now = datetime.now(timezone.utc)
    db.upsert_subscription(wallet, stripe_customer_id="c", stripe_subscription_id="s", status="active", tier="pro",
                           subscription_credits=0, current_period_end=(now + timedelta(days=days)).isoformat())


def test_the_defaults_match_the_credit_rules():
    assert settings.EMAIL_SIGNUP_BONUS_CREDITS == 250 and settings.CHAT_ANSWER_CREDITS == 1
    for name in ("BASIC", "PLUS", "ADVANCED"):
        assert getattr(settings, f"AI_MESSAGES_PER_DAY_{name}") == 0
        assert getattr(settings, f"AI_MESSAGES_PER_DAY_PAID_{name}") == 0
    for plan in ("free", "pro", "max"):
        assert [ai_models.messages_per_day(settings, t, plan) for t in ai_models.TIERS] == [None] * 4


def test_a_new_google_account_gets_250_credits_once(tmp_db, monkeypatch):
    db = tmp_db
    FREE = _free()
    assert db.get_spendable_credits(FREE) == 0
    assert db.grant_signup_bonus(FREE) is True and db.grant_signup_bonus(FREE) is False
    assert db.get_spendable_credits(FREE) == 250 and db.get_credit_balance(FREE) == 250
    other = _free()
    monkeypatch.setattr(db.settings, "EMAIL_SIGNUP_BONUS_CREDITS", 40)      # the env variable changes it with no deploy
    assert db.grant_signup_bonus(other) is True and db.get_spendable_credits(other) == 40


def test_an_answer_costs_one_credit_and_a_refund_gives_it_back(tmp_db):
    db = tmp_db
    FREE = _free()
    db.grant_signup_bonus(FREE)
    pool = db.charge_credits(FREE, settings.CHAT_ANSWER_CREDITS, ai_tier="basic")
    assert pool and db.get_spendable_credits(FREE, "basic") == 249
    db.refund_credits(FREE, pool, 1)
    assert db.get_spendable_credits(FREE, "basic") == 250


def test_out_of_credits_cannot_pay_for_an_answer(tmp_db):
    db = tmp_db
    FREE = _free()
    db.grant_signup_bonus(FREE)
    assert db.charge_credits(FREE, 250, ai_tier="basic")
    assert db.get_spendable_credits(FREE, "basic") == 0
    assert db.charge_credits(FREE, 1, ai_tier="basic") is None


def test_pro_weekly_credits_count_toward_the_answer_gate(tmp_db):
    """A subscriber with no top-up is not out of credits while the weekly allowance lasts."""
    db = tmp_db
    PRO = _pro_wallet()
    _make_pro(db, PRO)
    assert db.get_credit_balance(PRO) == 0                      # the purchased side alone is empty
    assert db.get_spendable_credits(PRO, "plus") == 1800        # but the weekly allowance can pay
    assert db.charge_credits(PRO, 1, ai_tier="plus")
    assert db.get_spendable_credits(PRO, "plus") == 1799


def test_standard_fallback_credits_pay_for_a_standard_answer_only(tmp_db):
    db = tmp_db
    PRO = _pro_wallet()
    _make_pro(db, PRO)
    fallback = settings.subscription_tiers["pro"]["fallback_daily_credits"]
    assert db.charge_credits(PRO, 1800)                         # weekly allowance gone
    assert db.get_spendable_credits(PRO, "plus") == 0           # Expert cannot use the fallback
    assert db.get_spendable_credits(PRO, "basic") == fallback   # Standard can
    assert db.charge_credits(PRO, 1, ai_tier="plus") is None
    assert db.charge_credits(PRO, 1, ai_tier="basic")
    assert db.get_spendable_credits(PRO, "basic") == fallback - 1


def test_there_is_no_daily_generation_cap_on_pro_or_max(tmp_db):
    for plan in ("pro", "max"):
        assert "daily_generation_cap" not in settings.subscription_tiers[plan]
