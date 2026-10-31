"""Preview first, pay when you generate: the database side (draft jobs, charge once, refund, caps)."""
from __future__ import annotations

def _charged(result):
    status, pool = result
    return status == "charged" and pool.startswith("ledger:")


WALLET = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20


def _draft(db, user=WALLET, success=True):
    return db.record_job(description="bracket", success=success, user_id=user, part_type="l_bracket",
                         parameters={}, draft=True)


def test_a_draft_is_locked_until_it_is_paid_for(tmp_db):
    job = tmp_db.get_job(_draft(tmp_db))
    assert tmp_db.is_job_locked(job) is True


def test_older_and_api_jobs_are_never_locked(tmp_db):
    job_id = tmp_db.record_job(description="x", success=True, user_id=WALLET, part_type="shaft", parameters={})
    assert tmp_db.is_job_locked(tmp_db.get_job(job_id)) is False
    assert tmp_db.charge_job(job_id, WALLET) == ("already_paid", None)


def test_generate_charges_exactly_once(tmp_db):
    tmp_db.add_credits(WALLET, 5)
    job_id = _draft(tmp_db)
    assert _charged(tmp_db.charge_job(job_id, WALLET))
    assert tmp_db.get_credit_balance(WALLET) == 4
    assert tmp_db.is_job_locked(tmp_db.get_job(job_id)) is False
    assert tmp_db.charge_job(job_id, WALLET) == ("already_paid", None)
    assert tmp_db.get_credit_balance(WALLET) == 4


def test_no_credits_leaves_the_preview_locked_and_free_to_retry(tmp_db):
    job_id = _draft(tmp_db)
    assert tmp_db.charge_job(job_id, WALLET) == ("insufficient", None)
    assert tmp_db.is_job_locked(tmp_db.get_job(job_id)) is True
    tmp_db.add_credits(WALLET, 1)
    assert _charged(tmp_db.charge_job(job_id, WALLET))
    assert tmp_db.get_credit_balance(WALLET) == 0


def test_refund_returns_the_credit_and_locks_again_once(tmp_db):
    tmp_db.add_credits(WALLET, 3)
    job_id = _draft(tmp_db)
    tmp_db.charge_job(job_id, WALLET)
    assert tmp_db.refund_job_charge(job_id, WALLET) is True
    assert tmp_db.get_credit_balance(WALLET) == 3
    assert tmp_db.is_job_locked(tmp_db.get_job(job_id)) is True
    assert tmp_db.refund_job_charge(job_id, WALLET) is False
    assert tmp_db.get_credit_balance(WALLET) == 3


def test_only_the_owner_can_pay_and_failed_builds_cannot_be_paid(tmp_db):
    tmp_db.add_credits(OTHER, 5)
    job_id = _draft(tmp_db)
    assert tmp_db.charge_job(job_id, OTHER) == ("not_found", None)
    assert tmp_db.get_credit_balance(OTHER) == 5
    failed = _draft(tmp_db, success=False)
    tmp_db.add_credits(WALLET, 5)
    assert tmp_db.charge_job(failed, WALLET) == ("not_found", None)
    assert tmp_db.get_credit_balance(WALLET) == 5


def test_free_previews_do_not_count_toward_the_daily_cap_until_paid(tmp_db):
    tmp_db.add_credits(WALLET, 2)
    job_id = _draft(tmp_db)
    _draft(tmp_db)
    assert tmp_db.count_generations_last_24h(WALLET) == 0
    assert tmp_db.count_drafts_last_24h(WALLET) == 2
    tmp_db.charge_job(job_id, WALLET)
    assert tmp_db.count_generations_last_24h(WALLET) == 1


def test_draft_count_ignores_other_wallets_and_normal_jobs(tmp_db):
    _draft(tmp_db, user=OTHER)
    tmp_db.record_job(description="x", success=True, user_id=WALLET, part_type="shaft", parameters={})
    assert tmp_db.count_drafts_last_24h(WALLET) == 0


def test_a_higher_price_takes_that_many_credits_and_a_refund_returns_them(tmp_db):
    tmp_db.add_credits(WALLET, 60)
    job_id = _draft(tmp_db)
    assert _charged(tmp_db.charge_job(job_id, WALLET, cost=25))
    assert tmp_db.get_credit_balance(WALLET) == 35
    assert tmp_db.charge_job(job_id, WALLET, cost=25) == ("already_paid", None)
    assert tmp_db.get_credit_balance(WALLET) == 35
    assert tmp_db.refund_job_charge(job_id, WALLET) is True
    assert tmp_db.get_credit_balance(WALLET) == 60
    assert tmp_db.refund_job_charge(job_id, WALLET) is False
    assert tmp_db.get_credit_balance(WALLET) == 60


def test_not_enough_credits_for_the_price_takes_nothing_and_stays_locked(tmp_db):
    tmp_db.add_credits(WALLET, 24)
    job_id = _draft(tmp_db)
    assert tmp_db.charge_job(job_id, WALLET, cost=25) == ("insufficient", None)
    assert tmp_db.get_credit_balance(WALLET) == 24
    assert tmp_db.is_job_locked(tmp_db.get_job(job_id)) is True
    tmp_db.add_credits(WALLET, 1)
    assert _charged(tmp_db.charge_job(job_id, WALLET, cost=25))
    assert tmp_db.get_credit_balance(WALLET) == 0


def test_the_ai_tier_is_kept_on_the_job(tmp_db):
    job_id = tmp_db.record_job(description="x", success=True, user_id=WALLET, part_type="shaft",
                               parameters={}, draft=True, ai_tier="advanced")
    assert tmp_db.get_job(job_id)["ai_tier"] == "advanced"
    plain = tmp_db.record_job(description="x", success=True, user_id=WALLET, part_type="shaft", parameters={})
    assert tmp_db.get_job(plain)["ai_tier"] is None


def test_ai_usage_is_counted_per_wallet_and_tier(tmp_db):
    for _ in range(3):
        tmp_db.log_ai_usage(WALLET, "plus", "plus:gpt")
    tmp_db.log_ai_usage(WALLET, "plus", "plus:qwen")
    tmp_db.log_ai_usage(WALLET, "advanced", "advanced:claude")
    tmp_db.log_ai_usage(OTHER, "plus", "plus:gpt")
    assert tmp_db.count_ai_usage_last_24h(WALLET, "plus") == 4
    assert tmp_db.count_ai_usage_last_24h(WALLET, "advanced") == 1
    assert tmp_db.count_ai_usage_last_24h(WALLET, "basic") == 0
    assert tmp_db.count_ai_usage_last_24h(OTHER, "plus") == 1
