"""
Credit ledger for NitoCAD.

Design rules (from the business model review):
  * Credits live in grants. Each grant has its own amount, remaining balance
    and expiry. There is no single mutable balance.
  * Spending order is soonest-expiring first, non-expiring top-ups last.
  * Grants are idempotent per (user_id, source, period_key). A duplicate
    webhook or a repeated weekly job cannot grant twice.
  * Work is charged in two steps: reserve before the job, then settle (or
    refund) after it. Refunds go back to the grants the credits came from.
  * Every change also writes an append-only row to credit_ledger for audit.

It uses plain sqlite3 like db.py. Pass db.get_conn as `connect` in production.
"""

from __future__ import annotations

import math
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

# One credit is $0.001 of metered cost. Metered cost is the raw provider cost
# multiplied by a safety factor you calibrate from real usage data.
USD_PER_CREDIT = 0.001

SIGNUP_GRANT_CREDITS = 250
SIGNUP_GRANT_DAYS = 90
WEEKLY_ALLOWANCE = {"pro": 1800, "max": 9000}
WEEKLY_PERIOD_DAYS = 7

# Starting values. Calibrate per model once you have usage data.
SAFETY_MULTIPLIER = {"default": 4.0}


# Sources that belong to the "subscription" pool. Everything else (top-ups,
# signup grants, legacy balances, refunds without a bucket) is "purchased".
_SUB_SQL = "(source LIKE 'subscription%' OR source LIKE 'weekly\\_%' ESCAPE '\\')"


# Daily fallback credits can only be spent on the cheapest model, so they are
# kept out of every pool except "fallback".
_FALLBACK_SQL = "source LIKE 'fallback%'"


def _pool_sql(pool: str | None) -> str:
    if pool == "subscription":
        return f" AND {_SUB_SQL}"
    if pool == "purchased":
        return f" AND NOT {_SUB_SQL} AND NOT {_FALLBACK_SQL}"
    if pool == "fallback":
        return f" AND {_FALLBACK_SQL}"
    return f" AND NOT {_FALLBACK_SQL}"


class InsufficientCredits(Exception):
    def __init__(self, needed: int, available: int):
        super().__init__(f"need {needed} credits, have {available}")
        self.needed = needed
        self.available = available


def credits_for_cost(provider_cost_usd: float, model: str = "default") -> int:
    """Raw provider cost -> credits to charge. Rounds up, never below 1 for
    a real cost."""
    if provider_cost_usd <= 0:
        return 0
    mult = SAFETY_MULTIPLIER.get(model, SAFETY_MULTIPLIER["default"])
    return max(1, math.ceil(round(provider_cost_usd * mult / USD_PER_CREDIT, 6)))


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Billing:
    def __init__(self, connect):
        self._connect = connect

    @contextmanager
    def _tx(self):
        # BEGIN IMMEDIATE takes the write lock up front so two requests can't
        # both read the same balance and both spend it.
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_grants (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    period_key TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    remaining INTEGER NOT NULL,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (user_id, source, period_key)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    idem_key TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL,
                    reserved INTEGER NOT NULL,
                    charged INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reservation_parts (
                    reservation_id TEXT NOT NULL,
                    grant_id TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    PRIMARY KEY (reservation_id, grant_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS credit_ledger (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    delta INTEGER NOT NULL,
                    grant_id TEXT,
                    ref TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_grants_user ON credit_grants(user_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ledger_user ON credit_ledger(user_id)"
            )

    # ------------------------------------------------------------ helpers --

    @staticmethod
    def _log(conn, user_id, kind, delta, grant_id=None, ref=None, now=None):
        conn.execute(
            "INSERT INTO credit_ledger (id, user_id, kind, delta, grant_id, ref, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), user_id, kind, delta, grant_id, ref, _iso(now or _now())),
        )

    @staticmethod
    def _spendable(conn, user_id, now_iso, pool=None):
        # Soonest expiry first. NULL expiry (top-ups) sorts last.
        return conn.execute(
            """
            SELECT id, remaining FROM credit_grants
            WHERE user_id = ? AND remaining > 0
              AND (expires_at IS NULL OR expires_at > ?)
            """ + _pool_sql(pool) + """
            ORDER BY expires_at IS NULL, expires_at ASC, created_at ASC
            """,
            (user_id, now_iso),
        ).fetchall()

    def _take(self, conn, user_id, amount, now, allow_partial=False, pool=None):
        """Deduct from grants in spend order. Returns {grant_id: taken}."""
        rows = self._spendable(conn, user_id, _iso(now), pool)
        available = sum(r["remaining"] for r in rows)
        if available < amount and not allow_partial:
            raise InsufficientCredits(amount, available)
        left = min(amount, available)
        taken: dict[str, int] = {}
        for r in rows:
            if left <= 0:
                break
            t = min(left, r["remaining"])
            conn.execute(
                "UPDATE credit_grants SET remaining = remaining - ? WHERE id = ?",
                (t, r["id"]),
            )
            taken[r["id"]] = t
            left -= t
        return taken

    # -------------------------------------------------------------- grants --

    def grant(
        self,
        user_id: str,
        source: str,
        amount: int,
        period_key: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Create a grant once. Returns False if this (user, source, period_key)
        was already granted, so webhook retries are harmless."""
        now = now or _now()
        gid = str(uuid.uuid4())
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO credit_grants"
                " (id, user_id, source, period_key, amount, remaining, expires_at, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (gid, user_id, source, period_key, amount, amount,
                 _iso(expires_at) if expires_at else None, _iso(now)),
            )
            if cur.rowcount == 0:
                return False
            self._log(conn, user_id, "grant", amount, gid, f"{source}:{period_key}", now)
            return True

    def grant_signup(self, user_id: str, now: datetime | None = None) -> bool:
        now = now or _now()
        return self.grant(
            user_id, "signup", SIGNUP_GRANT_CREDITS, "signup",
            now + timedelta(days=SIGNUP_GRANT_DAYS), now,
        )

    def grant_weekly(
        self, user_id: str, plan: str, period_start: datetime, now: datetime | None = None
    ) -> bool:
        """period_start must come from the subscription's billing cycle, not
        from the time the job runs, so the key is stable across retries."""
        return self.grant(
            user_id, f"weekly_{plan}", WEEKLY_ALLOWANCE[plan],
            period_start.date().isoformat(),
            period_start + timedelta(days=WEEKLY_PERIOD_DAYS), now,
        )

    def grant_topup(self, user_id: str, credits: int, payment_id: str,
                    now: datetime | None = None) -> bool:
        """Non-expiring. payment_id makes webhook replays idempotent."""
        return self.grant(user_id, "topup", credits, payment_id, None, now)

    def balance(self, user_id: str, now: datetime | None = None, pool: str | None = None) -> dict:
        now = now or _now()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source, SUM(remaining) AS credits, MIN(expires_at) AS next_expiry
                FROM credit_grants
                WHERE user_id = ? AND remaining > 0
                  AND (expires_at IS NULL OR expires_at > ?)
                """ + _pool_sql(pool) + """
                GROUP BY source
                """,
                (user_id, _iso(now)),
            ).fetchall()
        by_source = {r["source"]: r["credits"] for r in rows}
        return {"total": sum(by_source.values()), "by_source": by_source}

    # ------------------------------------------------------ reserve/settle --

    def reserve(self, user_id: str, amount: int, idem_key: str,
                now: datetime | None = None, pool: str | None = None) -> str:
        """Hold credits before expensive work. Same idem_key returns the same
        reservation, so a retried request never double-charges."""
        now = now or _now()
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT id FROM reservations WHERE idem_key = ?", (idem_key,)
            ).fetchone()
            if existing:
                return existing["id"]
            taken = self._take(conn, user_id, amount, now, pool=pool)
            rid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO reservations (id, user_id, idem_key, status, reserved, charged, created_at)"
                " VALUES (?, ?, ?, 'reserved', ?, ?, ?)",
                (rid, user_id, idem_key, amount, amount, _iso(now)),
            )
            for gid, t in taken.items():
                conn.execute(
                    "INSERT INTO reservation_parts (reservation_id, grant_id, amount) VALUES (?, ?, ?)",
                    (rid, gid, t),
                )
                self._log(conn, user_id, "reserve", -t, gid, rid, now)
            return rid

    def settle(self, reservation_id: str, actual_credits: int,
               now: datetime | None = None) -> int:
        """Charge the real cost. Surplus goes back to the grants it came from.
        If the job cost more than reserved, take the extra when available and
        otherwise absorb it. Returns credits actually charged."""
        now = now or _now()
        with self._tx() as conn:
            res = conn.execute(
                "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            if res is None:
                raise KeyError(reservation_id)
            if res["status"] != "reserved":
                return res["charged"]
            user_id, held = res["user_id"], res["reserved"]

            if actual_credits < held:
                surplus = held - actual_credits
                parts = conn.execute(
                    "SELECT grant_id, amount FROM reservation_parts WHERE reservation_id = ?"
                    " ORDER BY rowid DESC",
                    (reservation_id,),
                ).fetchall()
                for p in parts:  # give back to non-expiring grants first
                    if surplus <= 0:
                        break
                    back = min(surplus, p["amount"])
                    conn.execute(
                        "UPDATE credit_grants SET remaining = remaining + ? WHERE id = ?",
                        (back, p["grant_id"]),
                    )
                    conn.execute(
                        "UPDATE reservation_parts SET amount = amount - ?"
                        " WHERE reservation_id = ? AND grant_id = ?",
                        (back, reservation_id, p["grant_id"]),
                    )
                    self._log(conn, user_id, "settle_return", back, p["grant_id"], reservation_id, now)
                    surplus -= back
                charged = actual_credits
            elif actual_credits > held:
                extra = self._take(conn, user_id, actual_credits - held, now, allow_partial=True)
                for gid, t in extra.items():
                    conn.execute(
                        "INSERT INTO reservation_parts (reservation_id, grant_id, amount)"
                        " VALUES (?, ?, ?)"
                        " ON CONFLICT(reservation_id, grant_id) DO UPDATE SET amount = amount + ?",
                        (reservation_id, gid, t, t),
                    )
                    self._log(conn, user_id, "settle_extra", -t, gid, reservation_id, now)
                charged = held + sum(extra.values())
            else:
                charged = held

            conn.execute(
                "UPDATE reservations SET status = 'settled', charged = ? WHERE id = ?",
                (charged, reservation_id),
            )
            return charged

    def usage(self, user_id: str, source_prefixes: tuple[str, ...], now: datetime | None = None) -> dict:
        """How much of the live grants with these source prefixes is used. amount is what
        was granted, remaining what is left, resets_at the soonest expiry."""
        now = now or _now()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT source, amount, remaining, expires_at FROM credit_grants "
                "WHERE user_id = ? AND (expires_at IS NULL OR expires_at > ?)",
                (user_id, _iso(now)),
            ).fetchall()
        rows = [r for r in rows if r["source"].startswith(source_prefixes)]
        amount = sum(r["amount"] for r in rows)
        remaining = sum(r["remaining"] for r in rows)
        expiries = [r["expires_at"] for r in rows if r["expires_at"]]
        return {"amount": amount, "remaining": remaining, "resets_at": min(expiries) if expiries else None}

    def revoke(self, user_id: str, source_prefixes: tuple[str, ...], now: datetime | None = None) -> int:
        """Zero the remaining credits of grants whose source starts with one of the
        prefixes, and record it. Used when a subscription ends for good, so the rest of
        a paid week cannot be kept after a cancel or a refund. Returns credits removed."""
        now = now or _now()
        removed = 0
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, source, remaining FROM credit_grants WHERE user_id = ? AND remaining > 0",
                (user_id,),
            ).fetchall()
            for r in rows:
                if not r["source"].startswith(source_prefixes):
                    continue
                conn.execute("UPDATE credit_grants SET remaining = 0 WHERE id = ?", (r["id"],))
                self._log(conn, user_id, "revoke", -r["remaining"], r["id"], None, now)
                removed += r["remaining"]
        return removed

    def refund(self, reservation_id: str, now: datetime | None = None) -> int:
        """Full refund for a platform failure. Credits return to the original
        grants. If a grant has expired since, its credits stay expired."""
        now = now or _now()
        with self._tx() as conn:
            res = conn.execute(
                "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            if res is None:
                raise KeyError(reservation_id)
            if res["status"] == "refunded":
                return 0
            parts = conn.execute(
                "SELECT grant_id, amount FROM reservation_parts WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchall()
            total = 0
            for p in parts:
                if p["amount"] <= 0:
                    continue
                conn.execute(
                    "UPDATE credit_grants SET remaining = remaining + ? WHERE id = ?",
                    (p["amount"], p["grant_id"]),
                )
                self._log(conn, res["user_id"], "refund", p["amount"], p["grant_id"], reservation_id, now)
                total += p["amount"]
            conn.execute(
                "UPDATE reservations SET status = 'refunded', charged = 0 WHERE id = ?",
                (reservation_id,),
            )
            return total
