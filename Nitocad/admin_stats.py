"""
Owner-only usage metrics. Included from web_app.py with

    app.include_router(admin_stats.build_router(limiter=limiter))

  GET /admin/stats?days=30   JSON: signups, generations, AI usage, daily series
  GET /admin/dashboard       a small page that asks for the token and shows the JSON

Both stay off until ADMIN_TOKEN is set. With no token configured every route
answers 404, so a fresh deploy never exposes numbers by accident. Send the
token in an X-Admin-Token header or as "Authorization: Bearer <token>". It is
never accepted in the URL, because URLs end up in logs and browser history.

Everything here is read-only SQL over tables that already exist (email_accounts,
sessions, jobs, ai_usage). Nothing new is stored.

What counts as what:
  signup              a Google account row in email_accounts, or a wallet that has
                      signed in at least once (first row in sessions). Wallet
                      users have no accounts table, so their signup date is their
                      first session.
  build               any row in jobs for a signed-in user, free previews included.
  paid generation     a build that succeeded and is not a locked free preview. This
                      is the same rule db.count_generations_last_24h uses for caps.
  anonymous preview   a free /preview job with user_id "anonymous".

Visitors are not measured here. The backend never sees someone who only browses
the static pages, so use the analytics script on the frontend for that.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

import config
import db

# A succeeded build that was either never a draft, or was a draft the owner paid for.
_PAID = "success = 1 AND (draft = 0 OR (charged_at IS NOT NULL AND COALESCE(charged_pool, '') != 'pending'))"
# When a paid generation "happened": the moment of payment for a draft, else its creation.
_PAID_AT = "COALESCE(charged_at, created_at)"
_REAL_USER = "user_id NOT IN ('anonymous', 'default')"


def _check_token(request: Request) -> None:
    expected = config.get_settings().ADMIN_TOKEN
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    supplied = request.headers.get("x-admin-token", "")
    if not supplied:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _one(conn, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _by_day(conn, sql: str, params: tuple) -> dict[str, int]:
    return {r[0]: int(r[1]) for r in conn.execute(sql, params).fetchall()}


def compute_stats(days: int = 30) -> dict[str, Any]:
    days = max(1, min(int(days), 365))
    now = datetime.now(timezone.utc)
    cut = lambda d: (now - timedelta(days=d)).isoformat()  # noqa: E731
    c_window, c7, c30 = cut(days), cut(7), cut(30)

    # Every distinct user once, with the moment we first saw them.
    users_cte = """
        WITH google AS (
            SELECT lower(account_id) AS uid, MIN(created_at) AS first_seen
            FROM email_accounts GROUP BY lower(account_id)
        ),
        wallets AS (
            SELECT lower(wallet_address) AS uid, MIN(created_at) AS first_seen
            FROM sessions
            WHERE lower(wallet_address) NOT IN (SELECT uid FROM google)
            GROUP BY lower(wallet_address)
        ),
        users AS (
            SELECT uid, first_seen, 'google' AS kind FROM google
            UNION ALL
            SELECT uid, first_seen, 'wallet' AS kind FROM wallets
        )
    """

    with db.get_conn() as conn:
        users_total = _one(conn, users_cte + "SELECT COUNT(*) FROM users")
        users_google = _one(conn, users_cte + "SELECT COUNT(*) FROM users WHERE kind = 'google'")
        new_window = _one(conn, users_cte + "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (c_window,))
        new_7 = _one(conn, users_cte + "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (c7,))
        new_30 = _one(conn, users_cte + "SELECT COUNT(*) FROM users WHERE first_seen >= ?", (c30,))
        signup_days = _by_day(
            conn,
            users_cte + "SELECT substr(first_seen, 1, 10), COUNT(*) FROM users WHERE first_seen >= ? GROUP BY 1",
            (c_window,),
        )

        active_7 = _one(conn, f"SELECT COUNT(DISTINCT user_id) FROM jobs WHERE {_REAL_USER} AND created_at >= ?", (c7,))
        active_30 = _one(conn, f"SELECT COUNT(DISTINCT user_id) FROM jobs WHERE {_REAL_USER} AND created_at >= ?", (c30,))
        built_ever = _one(conn, f"SELECT COUNT(DISTINCT lower(user_id)) FROM jobs WHERE {_REAL_USER}")
        paid_ever_users = _one(conn, f"SELECT COUNT(DISTINCT lower(user_id)) FROM jobs WHERE {_REAL_USER} AND {_PAID}")

        builds_total = _one(conn, f"SELECT COUNT(*) FROM jobs WHERE {_REAL_USER}")
        builds_ok = _one(conn, f"SELECT COUNT(*) FROM jobs WHERE {_REAL_USER} AND success = 1")
        builds_failed = _one(conn, f"SELECT COUNT(*) FROM jobs WHERE {_REAL_USER} AND success = 0 AND state IN ('failed', 'refunded')")
        previews = _one(conn, f"SELECT COUNT(*) FROM jobs WHERE {_REAL_USER} AND draft = 1")
        anon = _one(conn, "SELECT COUNT(*) FROM jobs WHERE user_id = 'anonymous'")
        paid_total = _one(conn, f"SELECT COUNT(*) FROM jobs WHERE {_REAL_USER} AND {_PAID}")
        paid_window = _one(conn, f"SELECT COUNT(*) FROM jobs WHERE {_REAL_USER} AND {_PAID} AND {_PAID_AT} >= ?", (c_window,))
        paid_7 = _one(conn, f"SELECT COUNT(*) FROM jobs WHERE {_REAL_USER} AND {_PAID} AND {_PAID_AT} >= ?", (c7,))

        by_state = {
            (r[0] or "unknown"): int(r[1])
            for r in conn.execute(f"SELECT state, COUNT(*) FROM jobs WHERE {_REAL_USER} GROUP BY state").fetchall()
        }
        by_tier = {
            (r[0] or "basic"): int(r[1])
            for r in conn.execute(f"SELECT ai_tier, COUNT(*) FROM jobs WHERE {_REAL_USER} AND {_PAID} GROUP BY ai_tier").fetchall()
        }
        top_parts = [
            {"part_type": r[0] or "unknown", "count": int(r[1])}
            for r in conn.execute(
                f"SELECT part_type, COUNT(*) c FROM jobs WHERE {_REAL_USER} AND created_at >= ? "
                "GROUP BY part_type ORDER BY c DESC LIMIT 10",
                (c_window,),
            ).fetchall()
        ]
        build_days = _by_day(
            conn,
            f"SELECT substr(created_at, 1, 10), COUNT(*) FROM jobs WHERE {_REAL_USER} AND created_at >= ? GROUP BY 1",
            (c_window,),
        )
        paid_days = _by_day(
            conn,
            f"SELECT substr({_PAID_AT}, 1, 10), COUNT(*) FROM jobs WHERE {_REAL_USER} AND {_PAID} "
            f"AND {_PAID_AT} >= ? GROUP BY 1",
            (c_window,),
        )

        ai_total = _one(conn, "SELECT COUNT(*) FROM ai_usage")
        ai_window = _one(conn, "SELECT COUNT(*) FROM ai_usage WHERE created_at >= ?", (c_window,))
        ai_by_tier = {
            r[0]: int(r[1])
            for r in conn.execute(
                "SELECT tier, COUNT(*) FROM ai_usage WHERE created_at >= ? GROUP BY tier", (c_window,)
            ).fetchall()
        }

    daily = []
    for i in range(days - 1, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        daily.append(
            {
                "date": d,
                "signups": signup_days.get(d, 0),
                "builds": build_days.get(d, 0),
                "paid_generations": paid_days.get(d, 0),
            }
        )

    def rate(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "users": {
            "total": users_total,
            "google": users_google,
            "wallet": users_total - users_google,
            "new_in_window": new_window,
            "new_last_7d": new_7,
            "new_last_30d": new_30,
            "active_last_7d": active_7,
            "active_last_30d": active_30,
            "ever_built_something": built_ever,
            "ever_paid_for_a_generation": paid_ever_users,
            "signup_to_first_build_rate": rate(built_ever, users_total),
        },
        "generations": {
            "paid_total": paid_total,
            "paid_in_window": paid_window,
            "paid_last_7d": paid_7,
            "builds_total": builds_total,
            "builds_succeeded": builds_ok,
            "builds_failed": builds_failed,
            "build_success_rate": rate(builds_ok, builds_ok + builds_failed),
            "free_previews": previews,
            "anonymous_previews": anon,
            "builds_by_state": by_state,
            "paid_by_ai_tier": by_tier,
            "top_part_types_in_window": top_parts,
        },
        "ai_messages": {"total": ai_total, "in_window": ai_window, "by_tier_in_window": ai_by_tier},
        "daily": daily,
        "visitors": "Not tracked by the backend. Use the analytics script on the frontend pages.",
    }


_DASHBOARD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Nitocad stats</title>
<style>
:root{color-scheme:light dark;font-family:system-ui,sans-serif}
body{margin:0;padding:16px;max-width:900px;margin-inline:auto}
h1{font-size:20px} h2{font-size:15px;margin:22px 0 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.card{border:1px solid #8884;border-radius:8px;padding:10px}
.card b{display:block;font-size:22px} .card span{font-size:12px;opacity:.7}
table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid #8883;padding:4px 6px;text-align:right} td:first-child,th:first-child{text-align:left}
input,button,select{font:inherit;padding:6px 8px}
#err{color:#c33}
</style></head><body>
<h1>Nitocad stats</h1>
<div id="login"><input id="tok" type="password" placeholder="Admin token" autocomplete="off">
<select id="days"><option>7</option><option selected>30</option><option>90</option></select>
<button id="go">Load</button> <span id="err"></span></div>
<div id="out"></div>
<script>
const $=id=>document.getElementById(id);
function el(t,txt,cls){const e=document.createElement(t);if(txt!==undefined)e.textContent=txt;if(cls)e.className=cls;return e}
function cards(obj){const g=el('div','','grid');for(const[k,v]of Object.entries(obj)){if(typeof v==='object'&&v!==null)continue;
const c=el('div','','card');c.append(el('b',v===null?'n/a':String(v)),el('span',k.replace(/_/g,' ')));g.append(c)}return g}
function table(rows,cols){const t=el('table');const h=el('tr');cols.forEach(c=>h.append(el('th',c)));t.append(h);
rows.forEach(r=>{const tr=el('tr');cols.forEach(c=>tr.append(el('td',String(r[c]))));t.append(tr)});return t}
async function load(){
  $('err').textContent='';
  const r=await fetch('/admin/stats?days='+$('days').value,{headers:{'X-Admin-Token':$('tok').value}});
  if(!r.ok){$('err').textContent=r.status===401?'Wrong token':'Error '+r.status;return}
  const d=await r.json();const o=$('out');o.replaceChildren();
  o.append(el('h2','Users'),cards(d.users),el('h2','Generations'),cards(d.generations),
   el('h2','AI messages'),cards(d.ai_messages),el('h2','Per day'),
   table(d.daily.slice().reverse(),['date','signups','builds','paid_generations']),
   el('h2','Top part types'),table(d.generations.top_part_types_in_window,['part_type','count']));
}
$('go').onclick=load;$('tok').addEventListener('keydown',e=>{if(e.key==='Enter')load()});
</script></body></html>"""


def build_router(*, limiter: Any = None) -> APIRouter:
    router = APIRouter(tags=["admin"])

    def _limit(fn):
        return limiter.limit("30/minute")(fn) if limiter is not None else fn

    @router.get("/admin/stats")
    @_limit
    async def admin_stats(request: Request, days: int = 30):
        _check_token(request)
        return await run_in_threadpool(compute_stats, days)

    @router.get("/admin/dashboard", response_class=HTMLResponse)
    @_limit
    async def admin_dashboard(request: Request):
        # The page itself holds no data. It only shows numbers after the
        # browser sends the token to /admin/stats. Hidden entirely until a
        # token is configured.
        if not config.get_settings().ADMIN_TOKEN:
            raise HTTPException(status_code=404, detail="Not found")
        return HTMLResponse(_DASHBOARD, headers={"Cache-Control": "no-store"})

    return router
