"""Owner stats: auth gating and that the counts follow the paid-generation rule."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seed(db):
    now = _now()
    with db.get_conn() as c:
        c.execute("INSERT INTO email_accounts (account_id, google_sub, email, created_at) VALUES ('g1','s1','a@x.com',?)", (now,))
        c.execute("INSERT INTO email_accounts (account_id, google_sub, email, created_at) VALUES ('g2','s2','b@x.com',?)", (now,))
        c.execute("INSERT INTO sessions (session_id, wallet_address, created_at, expires_at) VALUES ('t1','0xAbC',?,?)", (now, now))
        c.execute("INSERT INTO sessions (session_id, wallet_address, created_at, expires_at) VALUES ('t2','g1',?,?)", (now, now))  # google user's session, not a new wallet
        rows = [
            # id, user, success, state, draft, charged_at, charged_pool
            ("j1", "g1", 1, "settled", 0, None, None),        # paid
            ("j2", "g1", 1, "settled", 1, None, None),        # free preview, never paid
            ("j3", "g2", 1, "settled", 1, now, "ledger:x"),   # preview later paid
            ("j4", "0xabc", 0, "failed", 0, None, None),      # failed
            ("j5", "anonymous", 1, "settled", 1, None, None), # anonymous preview
        ]
        for jid, uid, ok, st, dr, ca, cp in rows:
            c.execute(
                "INSERT INTO jobs (id, user_id, description, success, state, draft, charged_at, charged_pool, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (jid, uid, "d", ok, st, dr, ca, cp, now),
            )
        c.execute("INSERT INTO ai_usage (wallet_address, tier, model, created_at) VALUES ('g1','basic','m',?)", (now,))


def test_off_without_token(client, tmp_db):
    assert client.get("/admin/stats").status_code == 404
    assert client.get("/admin/dashboard").status_code == 404


def test_rejects_wrong_token_and_url_token(client, tmp_db, monkeypatch):
    import config
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    config.get_settings.cache_clear()
    assert client.get("/admin/stats").status_code == 401
    assert client.get("/admin/stats", headers={"X-Admin-Token": "nope"}).status_code == 401
    assert client.get("/admin/stats?token=secret-token").status_code == 401


def test_counts(client, tmp_db, monkeypatch):
    import config
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")
    config.get_settings.cache_clear()
    _seed(tmp_db)
    r = client.get("/admin/stats?days=7", headers={"Authorization": "Bearer secret-token"})
    assert r.status_code == 200
    d = r.json()
    assert d["users"]["total"] == 3 and d["users"]["google"] == 2 and d["users"]["wallet"] == 1
    assert d["users"]["ever_built_something"] == 3
    g = d["generations"]
    assert g["builds_total"] == 4            # anonymous excluded
    assert g["paid_total"] == 2              # j1 and j3 only
    assert g["builds_failed"] == 1
    assert g["free_previews"] == 2
    assert g["anonymous_previews"] == 1
    assert d["ai_messages"]["total"] == 1
    assert len(d["daily"]) == 7 and d["daily"][-1]["paid_generations"] == 2 and d["daily"][-1]["signups"] == 3
    assert client.get("/admin/dashboard").status_code == 200
