import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

import billing
from billing import Billing, InsufficientCredits, credits_for_cost

T0 = datetime(2026, 11, 1, tzinfo=timezone.utc)


@pytest.fixture
def b(tmp_path):
    path = str(tmp_path / "t.db")

    @contextmanager
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    bill = Billing(connect)
    bill.init_schema()
    return bill


def test_credits_for_cost_matches_review_example():
    assert credits_for_cost(0.0005) == 2
    assert credits_for_cost(0) == 0
    assert credits_for_cost(0.00001) == 1


def test_grants_are_idempotent(b):
    assert b.grant_signup("u", T0) is True
    assert b.grant_signup("u", T0) is False
    assert b.balance("u", T0)["total"] == billing.SIGNUP_GRANT_CREDITS == 250
    assert b.grant_topup("u", 500, "pay_1", T0) is True
    assert b.grant_topup("u", 500, "pay_1", T0) is False
    assert b.balance("u", T0)["total"] == 750


def test_spend_order_weekly_then_signup_then_topup(b):
    b.grant_topup("u", 1000, "p1", T0)
    b.grant_signup("u", T0)
    b.grant_weekly("u", "pro", T0, T0)
    b.reserve("u", 2000, "k1", T0)
    left = b.balance("u", T0)["by_source"]
    # weekly (1800) expires first, then signup (200 of 250), top-up untouched
    assert "weekly_pro" not in left
    assert left["signup"] == 50
    assert left["topup"] == 1000


def test_expired_grants_are_not_spendable(b):
    b.grant_weekly("u", "pro", T0, T0)
    later = T0 + timedelta(days=8)
    assert b.balance("u", later)["total"] == 0
    with pytest.raises(InsufficientCredits):
        b.reserve("u", 1, "k", later)


def test_reserve_is_idempotent(b):
    b.grant_topup("u", 100, "p", T0)
    r1 = b.reserve("u", 40, "same", T0)
    r2 = b.reserve("u", 40, "same", T0)
    assert r1 == r2
    assert b.balance("u", T0)["total"] == 60


def test_insufficient_leaves_balance_untouched(b):
    b.grant_topup("u", 10, "p", T0)
    with pytest.raises(InsufficientCredits):
        b.reserve("u", 11, "k", T0)
    assert b.balance("u", T0)["total"] == 10


def test_settle_returns_surplus_to_nonexpiring_first(b):
    b.grant_weekly("u", "pro", T0, T0)  # 1800
    b.grant_topup("u", 1000, "p", T0)
    rid = b.reserve("u", 2000, "k", T0)  # 1800 weekly + 200 topup
    charged = b.settle(rid, 1850, T0)  # surplus 150 back to topup
    assert charged == 1850
    left = b.balance("u", T0)["by_source"]
    assert left["topup"] == 950
    assert "weekly_pro" not in left


def test_settle_overage_takes_extra_when_available(b):
    b.grant_topup("u", 100, "p", T0)
    rid = b.reserve("u", 30, "k", T0)
    assert b.settle(rid, 50, T0) == 50
    assert b.balance("u", T0)["total"] == 50


def test_settle_overage_is_capped_by_balance(b):
    b.grant_topup("u", 40, "p", T0)
    rid = b.reserve("u", 30, "k", T0)
    assert b.settle(rid, 100, T0) == 40
    assert b.balance("u", T0)["total"] == 0


def test_settle_twice_does_not_charge_twice(b):
    b.grant_topup("u", 100, "p", T0)
    rid = b.reserve("u", 30, "k", T0)
    b.settle(rid, 30, T0)
    b.settle(rid, 30, T0)
    assert b.balance("u", T0)["total"] == 70


def test_refund_restores_original_buckets(b):
    b.grant_weekly("u", "pro", T0, T0)
    b.grant_topup("u", 500, "p", T0)
    rid = b.reserve("u", 1900, "k", T0)
    assert b.refund(rid, T0) == 1900
    left = b.balance("u", T0)["by_source"]
    assert left["weekly_pro"] == 1800
    assert left["topup"] == 500
    assert b.refund(rid, T0) == 0  # second refund is a no-op


def test_refund_does_not_revive_expired_allowance(b):
    b.grant_weekly("u", "pro", T0, T0)
    rid = b.reserve("u", 100, "k", T0)
    later = T0 + timedelta(days=8)
    b.refund(rid, later)
    assert b.balance("u", later)["total"] == 0


def test_fallback_credits_are_only_spendable_from_the_fallback_pool(b):
    b.grant("u", "fallback_pro", 50, "2026-11-01", T0 + timedelta(days=1), T0)
    b.grant_topup("u", 10, "p", T0)
    assert b.balance("u", T0)["total"] == 10                    # default total excludes fallback
    with pytest.raises(InsufficientCredits):
        b.reserve("u", 11, "k1", T0)                             # waterfall cannot touch it
    rid = b.reserve("u", 30, "k2", T0, pool="fallback")
    assert b.balance("u", T0, pool="fallback")["total"] == 20
    b.refund(rid, T0)
    assert b.balance("u", T0, pool="fallback")["total"] == 50


def test_revoke_removes_only_matching_sources(b):
    b.grant("u", "weekly_pro", 100, "w1", T0 + timedelta(days=3), T0)
    b.grant("u", "fallback_pro", 50, "d1", T0 + timedelta(days=1), T0)
    b.grant_topup("u", 70, "p", T0)
    assert b.revoke("u", ("weekly_", "fallback_"), T0) == 150
    assert b.balance("u", T0)["total"] == 70          # top-up untouched
    assert b.revoke("u", ("weekly_", "fallback_"), T0) == 0
