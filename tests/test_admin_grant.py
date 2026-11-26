"""Owner credit gifts: auth, email lookup, and that a repeated ref never grants twice."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

TOKEN = "gift-test-token-0123456789abcdef"
HDR = {"X-Admin-Token": TOKEN}


def _enable(monkeypatch):
    import config
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    config.get_settings.cache_clear()


def _account(db, account_id="g1", email="Wise@Example.com"):
    with db.get_conn() as c:
        c.execute(
            "INSERT INTO email_accounts (account_id, google_sub, email, created_at) VALUES (?,?,?,?)",
            (account_id, "sub-" + account_id, email, datetime.now(timezone.utc).isoformat()),
        )


def test_off_without_token(client, tmp_db):
    assert client.post("/admin/grant-credits", json={"who": "a@b.com", "amount": 5}).status_code == 404


def test_rejects_bad_token(client, tmp_db, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/admin/grant-credits", json={"who": "a@b.com", "amount": 5}, headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_gifts_by_email_case_insensitive(client, tmp_db, monkeypatch):
    _enable(monkeypatch)
    _account(tmp_db)
    r = client.post("/admin/grant-credits", json={"who": "wise@example.com", "amount": 1000}, headers=HDR)
    assert r.status_code == 200
    d = r.json()
    assert d["granted"] is True and d["balance_after"] - d["balance_before"] == 1000
    assert tmp_db.get_credit_balance("g1") == d["balance_after"]


def test_same_ref_grants_once(client, tmp_db, monkeypatch):
    _enable(monkeypatch)
    _account(tmp_db)
    body = {"who": "wise@example.com", "amount": 100, "ref": "tap-1"}
    first = client.post("/admin/grant-credits", json=body, headers=HDR).json()
    second = client.post("/admin/grant-credits", json=body, headers=HDR).json()
    assert first["granted"] is True and second["granted"] is False
    assert second["balance_after"] == first["balance_after"]


def test_unknown_email_and_bad_amounts(client, tmp_db, monkeypatch):
    _enable(monkeypatch)
    assert client.post("/admin/grant-credits", json={"who": "nobody@x.com", "amount": 5}, headers=HDR).status_code == 404
    assert client.post("/admin/grant-credits", json={"who": "nobody@x.com", "amount": 0}, headers=HDR).status_code == 422
    assert client.post("/admin/grant-credits", json={"who": "nobody@x.com", "amount": 10001}, headers=HDR).status_code == 422
    assert client.post("/admin/grant-credits", json={"who": "not-an-address", "amount": 5}, headers=HDR).status_code == 422
