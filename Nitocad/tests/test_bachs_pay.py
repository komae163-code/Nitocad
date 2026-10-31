"""Bachs: signature check, subscription lifecycle, top-ups, replays and ordering.
Event shapes follow https://docs.bachs.io (webhooks and event pages)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

import bachs_pay
from exceptions import PaymentError

SECRET = "whsec_test"
WALLET = "0x" + "ab" * 20
PRO, MAX = "prod_pro", "prod_max"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sign(body: bytes, secret=SECRET, ts=None, extra=()):
    ts = int(time.time()) if ts is None else ts
    digest = lambda s: hmac.new(s.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    sigs = ",".join(f"v1={digest(s)}" for s in (secret, *extra))
    return {"X-Bachs-Timestamp": str(ts), "X-Bachs-Signature": digest(secret), "X-Bachs-Signature-V2": f"t={ts},{sigs}"}


_counter = [0]


def _send(event_type, data, *, event_id=None, created_at=None):
    _counter[0] += 1
    event = {
        "id": event_id or f"evt_{_counter[0]}",
        "type": event_type,
        "created_at": created_at or _iso(datetime.now(timezone.utc)),
        "organization_id": "acct_x",
        "data": data,
    }
    body = json.dumps(event).encode()
    return bachs_pay.handle_webhook(body, _sign(body))


def _sub(status="active", product=PRO, days=30, cancel=False, sub_id="sub_1", wallet=WALLET):
    now = datetime.now(timezone.utc)
    return {
        "subscription_id": sub_id,
        "customer": {"customer_id": "cust_1", "email": "jane@example.com"},
        "product_id": product,
        "status": status,
        "current_period_start": _iso(now),
        "current_period_end": _iso(now + timedelta(days=days)),
        "cancel_at_period_end": cancel,
        "metadata": {"wallet_address": wallet} if wallet else {},
    }


@pytest.fixture
def bachs(tmp_db, monkeypatch):
    s = bachs_pay.settings
    monkeypatch.setattr(s, "BACHS_WEBHOOK_SECRET", SECRET, raising=False)
    monkeypatch.setattr(s, "BACHS_API_KEY", "sk_sandbox_x", raising=False)
    monkeypatch.setattr(s, "BACHS_PRODUCT_ID_PRO", PRO, raising=False)
    monkeypatch.setattr(s, "BACHS_PRODUCT_ID_MAX", MAX, raising=False)
    return tmp_db


# ---------------------------------------------------------------- signatures --

def test_signature_accepts_valid_and_rejects_tampering():
    body = b'{"id":"evt_1"}'
    assert bachs_pay.verify_signature(body, _sign(body), SECRET)
    assert not bachs_pay.verify_signature(body + b" ", _sign(body), SECRET)
    assert not bachs_pay.verify_signature(body, _sign(body, secret="other"), SECRET)
    assert not bachs_pay.verify_signature(body, {}, SECRET)


def test_signature_rejects_stale_timestamp():
    body = b"{}"
    assert not bachs_pay.verify_signature(body, _sign(body, ts=int(time.time()) - 3600), SECRET)


def test_signature_survives_secret_rotation_and_legacy_headers():
    body = b"{}"
    rotated = _sign(body, secret="new_secret", extra=(SECRET,))  # signed with both
    assert bachs_pay.verify_signature(body, rotated, SECRET)
    legacy = {k: v for k, v in _sign(body).items() if k != "X-Bachs-Signature-V2"}
    assert bachs_pay.verify_signature(body, legacy, SECRET)
    lower = {k.lower(): v for k, v in _sign(body).items()}
    assert bachs_pay.verify_signature(body, lower, SECRET)


def test_bad_signature_is_refused_by_the_handler(bachs):
    with pytest.raises(PaymentError):
        bachs_pay.handle_webhook(b"{}", {"X-Bachs-Signature-V2": "t=1,v1=bad"})


# ------------------------------------------------------------- subscriptions --

def test_subscription_created_activates_the_plan(bachs):
    assert _send("customer.subscription.created", _sub()).startswith("subscription:")
    assert bachs.get_plan(WALLET) == "pro"
    assert bachs.get_subscription_credits(WALLET) == 1800  # weekly allowance, granted on first look


def test_duplicate_event_is_a_no_op(bachs):
    _send("customer.subscription.created", _sub(), event_id="evt_same")
    assert _send("customer.subscription.created", _sub(), event_id="evt_same").startswith("duplicate:")


def test_invoice_before_subscription_asks_for_a_retry(bachs):
    inv = {"invoice_id": "inv_1", "subscription": {"subscription_id": "sub_1"}, "status": "paid",
           "period_end": _iso(datetime.now(timezone.utc) + timedelta(days=60))}
    with pytest.raises(bachs_pay.BachsRetry):
        _send("invoice.paid", inv)
    _send("customer.subscription.created", _sub())
    assert _send("invoice.paid", inv).startswith("renewed:")  # the redelivery succeeds
    end = datetime.fromisoformat(bachs.get_subscription(WALLET)["current_period_end"])
    assert end > datetime.now(timezone.utc) + timedelta(days=50)


def test_older_invoice_does_not_shorten_the_period(bachs):
    _send("customer.subscription.created", _sub(days=30))
    inv = {"invoice_id": "inv_old", "subscription": {"subscription_id": "sub_1"},
           "period_end": _iso(datetime.now(timezone.utc) + timedelta(days=5))}
    assert _send("invoice.paid", inv).startswith("already_extended:")


def test_one_off_invoice_is_ignored(bachs):
    assert _send("invoice.paid", {"invoice_id": "inv_1", "subscription": None}).startswith("ignored:")


def test_cancel_at_period_end_keeps_access_until_the_period_ends(bachs):
    _send("customer.subscription.created", _sub())
    _send("customer.subscription.updated", _sub(cancel=True))
    assert bachs.get_subscription(WALLET)["status"] == "canceling"
    assert bachs.is_subscription_entitled(WALLET) is True
    _send("customer.subscription.deleted", _sub(status="canceled", days=10))
    assert bachs.is_subscription_entitled(WALLET) is True        # paid-through date still ahead


def test_canceled_after_the_period_has_ended_removes_access(bachs):
    _send("customer.subscription.created", _sub())
    _send("customer.subscription.deleted", _sub(status="canceled", days=-1))
    assert bachs.is_subscription_entitled(WALLET) is False
    assert bachs.get_plan(WALLET) == "free"


def test_payment_failure_status_removes_access(bachs):
    _send("customer.subscription.created", _sub())
    _send("customer.subscription.updated", _sub(status="past_due"))
    assert bachs.is_subscription_entitled(WALLET) is False


def test_stale_subscription_event_is_skipped(bachs):
    now = datetime.now(timezone.utc)
    _send("customer.subscription.updated", _sub(), created_at=_iso(now))
    old = _send("customer.subscription.updated", _sub(status="past_due"), created_at=_iso(now - timedelta(hours=1)))
    assert old.startswith("stale:")
    assert bachs.is_subscription_entitled(WALLET) is True


def test_plan_change_moves_the_tier(bachs):
    _send("customer.subscription.created", _sub(product=PRO))
    _send("customer.subscription.updated", _sub(product=MAX))
    assert bachs.get_plan(WALLET) == "max"


def test_unknown_product_is_ignored_and_missing_wallet_is_refused(bachs):
    assert _send("customer.subscription.created", _sub(product="prod_other")).startswith("ignored:")
    with pytest.raises(PaymentError):
        _send("customer.subscription.created", _sub(wallet=None, sub_id="sub_9"))


def test_lapsed_then_renewed_restarts_the_weekly_clock(bachs):
    _send("customer.subscription.created", _sub())
    bachs.get_subscription_credits(WALLET)
    _send("customer.subscription.deleted", _sub(status="canceled", days=-1))
    _send("customer.subscription.created", _sub(sub_id="sub_2"))
    assert bachs.get_subscription(WALLET)["allowance_anchor"] is None
    assert bachs.get_subscription_credits(WALLET) == 1800


# ----------------------------------------------------------------- checkouts --

def test_subscription_checkout_sends_wallet_metadata(bachs, monkeypatch):
    sent = {}

    def fake_post(path, payload, idempotency_key):
        sent.update(path=path, payload=payload, key=idempotency_key)
        return {"checkout_id": "chk_sub", "checkout_url": "https://checkout.bachs.io/c/x"}

    monkeypatch.setattr(bachs_pay, "_post", fake_post)
    url = bachs_pay.create_subscription_checkout(WALLET, "max", "jane@example.com", "https://a/ok", "https://a/no")
    assert url == "https://checkout.bachs.io/c/x"
    assert sent["path"] == "/v1/checkout-sessions"
    assert sent["payload"]["product_cart"] == [{"product_id": MAX, "quantity": 1}]
    assert sent["payload"]["metadata"]["wallet_address"] == WALLET
    assert sent["key"]
    assert bachs.get_bachs_checkout("chk_sub")["kind"] == "subscription"


def test_checkout_refuses_unknown_plan_or_missing_product(bachs, monkeypatch):
    monkeypatch.setattr(bachs_pay, "_post", lambda *a, **k: pytest.fail("must not call Bachs"))
    with pytest.raises(PaymentError):
        bachs_pay.create_subscription_checkout(WALLET, "gold", "a@b.co", "https://a", "https://b")
    monkeypatch.setattr(bachs_pay.settings, "BACHS_PRODUCT_ID_MAX", None, raising=False)
    with pytest.raises(PaymentError):
        bachs_pay.create_subscription_checkout(WALLET, "max", "a@b.co", "https://a", "https://b")


# ------------------------------------------------------------------- top-ups --

def _topup(bachs, monkeypatch, pack="starter", checkout_id="chk_top"):
    monkeypatch.setattr(
        bachs_pay, "_post",
        lambda path, payload, idempotency_key: {"checkout_id": checkout_id, "checkout_url": "https://c/x"},
    )
    bachs_pay.create_topup_checkout(WALLET, pack, "https://a/ok", "https://a/no")


def _paid(checkout_id="chk_top", amount="10.00", currency="USD", charge_id="ch_1"):
    return {"charge_id": charge_id, "checkout_id": checkout_id, "status": "SUCCEEDED",
            "amount": amount, "currency": currency}


def test_topup_grants_credits_once(bachs, monkeypatch):
    _topup(bachs, monkeypatch)
    assert _send("collection.succeeded", _paid()).endswith(":granted")
    assert bachs.get_credit_balance(WALLET) == 2500
    # same charge delivered again under a new event id: credits are not granted twice
    assert _send("collection.succeeded", _paid()).endswith(":replay")
    assert bachs.get_credit_balance(WALLET) == 2500


def test_topup_with_wrong_amount_or_currency_is_refused(bachs, monkeypatch):
    _topup(bachs, monkeypatch, pack="scale")
    with pytest.raises(PaymentError):
        _send("collection.succeeded", _paid(amount="1.00"))
    with pytest.raises(PaymentError):
        _send("collection.succeeded", _paid(amount="60.00", currency="NGN"))
    assert bachs.get_credit_balance(WALLET) == 0


def test_topup_for_unknown_checkout_and_subscription_charges_are_ignored(bachs, monkeypatch):
    assert _send("collection.succeeded", _paid(checkout_id="chk_unknown")).startswith("ignored:")
    bachs.record_bachs_checkout("chk_sub", WALLET, "subscription", plan="pro")
    assert _send("collection.succeeded", _paid(checkout_id="chk_sub")).startswith("ignored:")


def test_unknown_pack_is_refused(bachs, monkeypatch):
    monkeypatch.setattr(bachs_pay, "_post", lambda *a, **k: pytest.fail("must not call Bachs"))
    with pytest.raises(PaymentError):
        bachs_pay.create_topup_checkout(WALLET, "mega", "https://a", "https://b")
