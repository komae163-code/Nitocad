"""
Bachs payments: Pro and Max subscriptions, plus one-time credit top-ups.

Everything here follows https://docs.bachs.io:

  * A subscription is created by completing a checkout for a recurring
    product. There is no create-subscription call. Checkout `metadata` is
    copied onto the subscription, which is how a webhook finds its wallet.
  * Webhooks are the source of truth. Redirects grant nothing.
  * Events can arrive more than once and out of order. Each event id is
    recorded after it is handled, grants are keyed by period or charge id,
    and a subscription event older than the stored state is skipped.
  * Return 400 only for a delivery we reject for good (bad signature, bad
    payload). Return 5xx when a retry could succeed, for example an
    invoice.paid that beats its customer.subscription.created.

Wallet linkage: create_subscription_checkout puts {"wallet_address": ...}
in the checkout metadata. Top-ups are linked by checkout_id, which we store
in bachs_checkouts before the customer is sent to pay.

Credits: plan allowances come from db.ensure_plan_credits once the
subscription row is active. Top-ups grant non-expiring credits keyed by the
Bachs charge id, so a replayed collection.succeeded cannot grant twice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

import httpx

import db
from config import settings
from exceptions import PaymentError
from logging_config import get_logger

logger = get_logger(__name__)

SIGNATURE_TOLERANCE_SECONDS = 300
_PAID_STATUSES = ("active", "trialing")


class BachsRetry(Exception):
    """Not an error in the event. Something it depends on has not arrived yet,
    so the route answers 503 and Bachs delivers it again."""


# ------------------------------------------------------------- api calls --

def _require_configured() -> None:
    if not settings.BACHS_API_KEY:
        raise PaymentError("Bachs isn't configured yet (BACHS_API_KEY missing).")


def _base_url() -> str:
    key = settings.BACHS_API_KEY or ""
    return "https://api.bachs.io" if key.startswith("sk_live_") else "https://sandbox-api.bachs.io"


def _post(path: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
    """One POST to the Bachs API. Kept as a single function so tests can replace it."""
    _require_configured()
    try:
        resp = httpx.post(
            _base_url() + path,
            json=payload,
            headers={
                "Authorization": f"Bearer {settings.BACHS_API_KEY}",
                "Idempotency-Key": idempotency_key,
            },
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise PaymentError(f"Could not reach Bachs: {exc}") from exc
    if resp.status_code >= 400:
        logger.error("bachs api error %s on %s: %s", resp.status_code, path, resp.text[:300])
        raise PaymentError("Bachs rejected the checkout request.")
    return resp.json()


def create_subscription_checkout(wallet_address: str, plan: str, email: str,
                                 success_url: str, cancel_url: str) -> str:
    """Hosted checkout URL for a NEW Pro or Max subscription. The email must be
    real: Bachs needs a customer for recurring billing and emails the receipts."""
    cfg = settings.subscription_tiers.get(plan)
    if plan not in ("pro", "max") or not cfg or not cfg.get("bachs_product_id"):
        raise PaymentError(f"Plan {plan!r} isn't available (missing BACHS_PRODUCT_ID_{plan.upper()}).")
    wallet = wallet_address.lower()
    resp = _post(
        "/v1/checkout-sessions",
        {
            "product_cart": [{"product_id": cfg["bachs_product_id"], "quantity": 1}],
            "customer": {"email": email},
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {"wallet_address": wallet, "plan": plan},
        },
        idempotency_key=f"sub-{uuid.uuid4()}",
    )
    db.record_bachs_checkout(resp["checkout_id"], wallet, "subscription", plan=plan)
    return resp["checkout_url"]


def create_topup_checkout(wallet_address: str, pack: str, success_url: str, cancel_url: str,
                          email: str | None = None) -> str:
    """Hosted checkout URL for a one-time credit pack."""
    packs = settings.topup_packs
    if pack not in packs:
        raise PaymentError(f"Unknown pack {pack!r}. Choose one of {sorted(packs)}.")
    spec = packs[pack]
    wallet = wallet_address.lower()
    payload: dict[str, Any] = {
        "pricing": {"currency": "USD", "amount": spec["usd"]},
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": {"wallet_address": wallet, "pack": pack, "credits": spec["credits"]},
    }
    if email:
        payload["customer"] = {"email": email}
    resp = _post("/v1/checkout-sessions", payload, idempotency_key=f"topup-{uuid.uuid4()}")
    db.record_bachs_checkout(
        resp["checkout_id"], wallet, "topup", credits=spec["credits"], amount=spec["usd"], currency="USD"
    )
    return resp["checkout_url"]


# ----------------------------------------------------------- webhook auth --

def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def verify_signature(raw_body: bytes, headers: Mapping[str, str], secret: str,
                     *, now: float | None = None, tolerance: int = SIGNATURE_TOLERANCE_SECONDS) -> bool:
    """Checks X-Bachs-Signature-V2 (t=...,v1=...,v1=...) against the raw body,
    accepting any v1 value so a secret rotation does not break deliveries. Falls
    back to the original X-Bachs-Timestamp and X-Bachs-Signature pair."""
    now = time.time() if now is None else now
    v2 = _header(headers, "X-Bachs-Signature-V2")
    if v2:
        parts = [p.split("=", 1) for p in v2.split(",") if "=" in p]
        stamps = [v for k, v in parts if k == "t"]
        signatures = [v for k, v in parts if k == "v1"]
        if not stamps or not signatures:
            return False
        timestamp_raw = stamps[0]
    else:
        timestamp_raw = _header(headers, "X-Bachs-Timestamp")
        legacy = _header(headers, "X-Bachs-Signature")
        signatures = [legacy] if legacy else []
        if not timestamp_raw or not signatures:
            return False
    try:
        timestamp = int(timestamp_raw)
    except ValueError:
        return False
    if abs(now - timestamp) > tolerance:
        return False
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in signatures)


# ----------------------------------------------------------- webhook flow --

def handle_webhook(raw_body: bytes, headers: Mapping[str, str]) -> str:
    """Verify, dedupe, dispatch. Returns a short result string. Raises PaymentError
    for a delivery to refuse for good, BachsRetry when a retry should be asked for."""
    if not settings.BACHS_WEBHOOK_SECRET:
        raise PaymentError("Bachs webhooks aren't configured (BACHS_WEBHOOK_SECRET missing).")
    if not verify_signature(raw_body, headers, settings.BACHS_WEBHOOK_SECRET):
        raise PaymentError("Invalid Bachs webhook signature.")
    try:
        event = json.loads(raw_body)
        event_id, event_type, data = event["id"], event["type"], event.get("data") or {}
    except (ValueError, KeyError, TypeError) as exc:
        raise PaymentError("Malformed Bachs webhook payload.") from exc
    event_at = _parse_time(event.get("created_at"))

    if db.bachs_event_seen(event_id):
        return f"duplicate:{event_id}"

    if event_type == "customer.subscription.created":
        result = _on_subscription_event(data, event_at)
    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        result = _on_subscription_event(data, event_at)
    elif event_type == "invoice.paid":
        result = _on_invoice_paid(data)
    elif event_type == "collection.succeeded":
        result = _on_collection_succeeded(data)
    else:
        result = f"ignored:{event_type}"
    db.mark_bachs_event_processed(event_id)
    return result


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _tier_for_product(product_id: str | None) -> str | None:
    for name in ("pro", "max"):
        cfg = settings.subscription_tiers.get(name) or {}
        if product_id and cfg.get("bachs_product_id") == product_id:
            return name
    return None


def _local_status(data: Mapping[str, Any]) -> str:
    """Bachs status -> ours. A subscription that is canceled or will not renew
    keeps "canceling" until its paid period ends, so a cancellation never takes
    away time the customer already paid for."""
    status = data.get("status")
    period_end = _parse_time(data.get("current_period_end"))
    future = bool(period_end and period_end > datetime.now(timezone.utc))
    if status in _PAID_STATUSES:
        return "canceling" if data.get("cancel_at_period_end") else status
    if status == "canceled":
        return "canceling" if future else "canceled"
    return status or "inactive"


def _on_subscription_event(sub: Mapping[str, Any], event_at: datetime | None) -> str:
    sub_id = sub.get("subscription_id")
    if not sub_id:
        raise PaymentError("Subscription event has no subscription_id.")
    tier = _tier_for_product(sub.get("product_id"))
    existing = db.get_subscription_by_bachs_subscription_id(sub_id)
    if tier is None and existing is None:
        return f"ignored:unknown_product:{sub.get('product_id')}"
    tier = tier or existing["tier"]

    wallet = (sub.get("metadata") or {}).get("wallet_address") or (existing or {}).get("wallet_address")
    if not wallet:
        logger.error("bachs subscription %s has no wallet_address in metadata", sub_id)
        raise PaymentError("Subscription has no wallet_address metadata.")

    last = _parse_time((existing or {}).get("last_event_at"))
    if existing and event_at and last and event_at < last:
        return f"stale:{sub_id}"

    period_end = _parse_time(sub.get("current_period_end"))
    customer = sub.get("customer") or {}
    db.upsert_bachs_subscription(
        wallet,
        bachs_customer_id=customer.get("customer_id") or (existing or {}).get("bachs_customer_id") or "",
        bachs_subscription_id=sub_id,
        status=_local_status(sub),
        tier=tier,
        current_period_end=period_end.isoformat() if period_end else None,
        email=customer.get("email"),
        event_at=(event_at or datetime.now(timezone.utc)).isoformat(),
    )
    logger.info("bachs subscription recorded", extra={"wallet_address": wallet, "tier": tier})
    return f"subscription:{wallet}:{tier}:{_local_status(sub)}"


def _on_invoice_paid(invoice: Mapping[str, Any]) -> str:
    """Extends the paid period. A one-off invoice has no subscription and is ignored."""
    sub_ref = invoice.get("subscription") or {}
    sub_id = sub_ref.get("subscription_id")
    if not sub_id:
        return "ignored:invoice_without_subscription"
    existing = db.get_subscription_by_bachs_subscription_id(sub_id)
    if existing is None:
        # invoice.paid beat customer.subscription.created. Ask for a redelivery.
        raise BachsRetry(f"subscription {sub_id} is not recorded yet")
    new_end = _parse_time(invoice.get("period_end"))
    old_end = _parse_time(existing.get("current_period_end"))
    if new_end is None:
        raise PaymentError("invoice.paid has no period_end.")
    if old_end and new_end <= old_end:
        return f"already_extended:{sub_id}"
    keep_canceling = existing["status"] == "canceling"
    db.upsert_bachs_subscription(
        existing["wallet_address"],
        bachs_customer_id=existing.get("bachs_customer_id") or "",
        bachs_subscription_id=sub_id,
        status="canceling" if keep_canceling else "active",
        tier=existing["tier"],
        current_period_end=new_end.isoformat(),
        email=existing.get("email"),
        event_at=existing.get("last_event_at") or datetime.now(timezone.utc).isoformat(),
    )
    return f"renewed:{existing['wallet_address']}:{existing['tier']}"


def _on_collection_succeeded(data: Mapping[str, Any]) -> str:
    checkout = db.get_bachs_checkout(data.get("checkout_id") or "")
    if checkout is None or checkout["kind"] != "topup":
        return "ignored:not_a_topup"
    if data.get("status") not in (None, "SUCCEEDED"):
        return f"ignored:status:{data.get('status')}"
    try:
        paid = Decimal(str(data.get("amount")))
        expected = Decimal(str(checkout["amount"]))
    except (InvalidOperation, TypeError) as exc:
        raise PaymentError("collection.succeeded has an unreadable amount.") from exc
    if data.get("currency") != checkout["currency"] or paid < expected:
        logger.error("bachs topup amount mismatch for %s: paid %s %s, expected %s %s",
                     checkout["checkout_id"], paid, data.get("currency"), expected, checkout["currency"])
        raise PaymentError("Payment does not match the checkout.")
    charge_id = data.get("charge_id") or checkout["checkout_id"]
    granted = db.grant_topup_credits(checkout["wallet_address"], int(checkout["credits"]), f"bachs:{charge_id}")
    db.set_bachs_checkout_status(checkout["checkout_id"], "paid")
    return f"topup:{checkout['wallet_address']}:{checkout['credits']}:{'granted' if granted else 'replay'}"
