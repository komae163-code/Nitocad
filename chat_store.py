"""
Server-side storage for CAD Studio chats.

Three tables, all keyed to the signed-in account (a lower-cased wallet address, or
the "email:..." id Google sign-in produces - whatever wallet_auth hands back):

  chats          one row per design conversation (soft-deleted, never hard-removed
                 from under a part whose job might still be exportable)
  chat_messages  the transcript, ordered by a per-chat `seq`
  chat_parts     every version of the part a chat has produced. Each row keeps the
                 full structured spec the CAD engine was driven with, so an edit
                 starts from exactly what was built rather than from re-read prose.

Deliberately stdlib-only and independent of db.py: web_app.py hands it db.get_conn
through configure(), which keeps this module importable (and testable) on its own.
Ownership is enforced here, in every query, so no route can forget it.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable

DEFAULT_TITLE = "New design"
MAX_TITLE_CHARS = 80

_get_conn: Callable[[], Any] | None = None


class ChatLimitError(Exception):
    """A per-user or per-chat cap was hit. The message is safe to show the user."""


def configure(get_conn: Callable[[], Any]) -> None:
    """Pass db.get_conn - a context manager yielding a sqlite3 connection that
    commits on success and rolls back on error."""
    global _get_conn
    _get_conn = get_conn


def _conn():
    if _get_conn is None:
        raise RuntimeError("chat_store.configure(get_conn) was never called")
    return _get_conn()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return secrets.token_urlsafe(9)


def _dump(v: Any) -> str:
    return json.dumps(v, separators=(",", ":"), sort_keys=True, default=str)


def _load(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def init_tables() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, deleted_at, updated_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES chats(id),
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                part_id TEXT,
                meta TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, seq)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_parts (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES chats(id),
                version INTEGER NOT NULL,
                job_id TEXT,
                title TEXT NOT NULL,
                part_type TEXT NOT NULL,
                spec TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, version)
            )
            """
        )
        # facts (measured size, mass, thinnest wall) were added after the first release
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(chat_parts)").fetchall()}
        if "facts" not in cols:
            conn.execute("ALTER TABLE chat_parts ADD COLUMN facts TEXT")
        # One manufacturability report per part version and process. The row is also the receipt:
        # a version is charged for a process once, and reopening it is free.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_part_checks (
                part_id TEXT NOT NULL REFERENCES chat_parts(id),
                process TEXT NOT NULL,
                report TEXT NOT NULL,
                charged INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                PRIMARY KEY (part_id, process)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_requirements (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL REFERENCES chats(id),
                kind TEXT NOT NULL,
                op TEXT NOT NULL,
                value TEXT NOT NULL,
                text TEXT,
                created_at TEXT NOT NULL,
                removed_at TEXT
            )
            """
        )


# ------------------------------------------------------------------ chats ---

def _clean_title(title: str | None) -> str:
    t = " ".join((title or "").split())[:MAX_TITLE_CHARS].strip()
    return t or DEFAULT_TITLE


def create_chat(user_id: str, title: str | None = None, *, max_chats: int = 200) -> dict:
    with _conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM chats WHERE user_id = ? AND deleted_at IS NULL", (user_id,)
        ).fetchone()["n"]
        if n >= max_chats:
            raise ChatLimitError(f"You have {n} chats, the maximum. Delete some to start a new one.")
        cid, now = _id(), _now()
        conn.execute(
            "INSERT INTO chats (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (cid, user_id, _clean_title(title), now, now),
        )
    return {"id": cid, "title": _clean_title(title), "created_at": now, "updated_at": now}


def list_chats(user_id: str, limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM chat_parts p WHERE p.chat_id = c.id) AS part_count,
                   (SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = c.id) AS message_count
            FROM chats c
            WHERE c.user_id = ? AND c.deleted_at IS NULL
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _owned(conn, user_id: str, chat_id: str):
    return conn.execute(
        "SELECT * FROM chats WHERE id = ? AND user_id = ? AND deleted_at IS NULL", (chat_id, user_id)
    ).fetchone()


def get_chat(user_id: str, chat_id: str) -> dict | None:
    """Full chat with messages and parts, or None if it does not exist or is not yours."""
    with _conn() as conn:
        c = _owned(conn, user_id, chat_id)
        if c is None:
            return None
        msgs = conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY seq", (chat_id,)).fetchall()
        parts = conn.execute("SELECT * FROM chat_parts WHERE chat_id = ? ORDER BY version", (chat_id,)).fetchall()
    return {
        "id": c["id"], "title": c["title"], "created_at": c["created_at"], "updated_at": c["updated_at"],
        "messages": [_msg(r) for r in msgs],
        "parts": [_part(r) for r in parts],
    }


def rename_chat(user_id: str, chat_id: str, title: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (_clean_title(title), _now(), chat_id, user_id),
        )
        return cur.rowcount > 0


def delete_chat(user_id: str, chat_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE chats SET deleted_at = ? WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (_now(), chat_id, user_id),
        )
        return cur.rowcount > 0


def set_title_if_default(user_id: str, chat_id: str, title: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE chats SET title = ? WHERE id = ? AND user_id = ? AND title = ?",
            (_clean_title(title), chat_id, user_id, DEFAULT_TITLE),
        )


# --------------------------------------------------------------- messages ---

def _msg(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "seq": r["seq"], "role": r["role"], "content": r["content"],
        "part_id": r["part_id"], "meta": _load(r["meta"], {}), "created_at": r["created_at"],
    }


def add_message(
    user_id: str, chat_id: str, role: str, content: str,
    part_id: str | None = None, meta: dict | None = None, *, max_messages: int = 400,
) -> dict:
    if role not in ("user", "assistant"):
        raise ValueError("role must be 'user' or 'assistant'")
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            raise LookupError("chat not found")
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m, COUNT(*) AS n FROM chat_messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row["n"] >= max_messages:
            raise ChatLimitError("This chat is full. Start a new design chat to keep going.")
        mid, now, seq = _id(), _now(), row["m"] + 1
        conn.execute(
            "INSERT INTO chat_messages (id, chat_id, seq, role, content, part_id, meta, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, chat_id, seq, role, content, part_id, _dump(meta) if meta else None, now),
        )
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
    return {"id": mid, "seq": seq, "role": role, "content": content, "part_id": part_id,
            "meta": meta or {}, "created_at": now}


def recent_messages(user_id: str, chat_id: str, limit: int) -> list[dict]:
    """The last `limit` messages, oldest first - the conversation the planner reads."""
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            raise LookupError("chat not found")
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY seq DESC LIMIT ?) ORDER BY seq",
            (chat_id, limit),
        ).fetchall()
    return [_msg(r) for r in rows]


# ------------------------------------------------------------------ parts ---

def _part(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "version": r["version"], "job_id": r["job_id"], "title": r["title"],
        "part_type": r["part_type"], "spec": _load(r["spec"], {}), "notes": _load(r["notes"], []),
        "facts": _load(r["facts"], {}) if "facts" in r.keys() else {},
        "created_at": r["created_at"],
    }


def add_part(
    user_id: str, chat_id: str, *, job_id: str | None, title: str, part_type: str,
    spec: dict, notes: list[str] | None = None, facts: dict | None = None,
) -> dict:
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            raise LookupError("chat not found")
        v = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM chat_parts WHERE chat_id = ?", (chat_id,)).fetchone()["v"]
        pid, now = _id(), _now()
        conn.execute(
            "INSERT INTO chat_parts (id, chat_id, version, job_id, title, part_type, spec, notes, facts, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, chat_id, v, job_id, title, part_type, _dump(spec), _dump(notes or []), _dump(facts or {}), now),
        )
    return {"id": pid, "version": v, "job_id": job_id, "title": title, "part_type": part_type,
            "spec": spec, "notes": notes or [], "facts": facts or {}, "created_at": now}


def latest_part(user_id: str, chat_id: str) -> dict | None:
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            raise LookupError("chat not found")
        r = conn.execute("SELECT * FROM chat_parts WHERE chat_id = ? ORDER BY version DESC LIMIT 1", (chat_id,)).fetchone()
    return _part(r) if r else None


# ----------------------------------------------------------------- checks ---

def get_part(user_id: str, chat_id: str, part_id: str) -> dict | None:
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            return None
        r = conn.execute("SELECT * FROM chat_parts WHERE id = ? AND chat_id = ?", (part_id, chat_id)).fetchone()
    return _part(r) if r else None


def get_checks(user_id: str, chat_id: str) -> dict[str, dict[str, dict]]:
    """{part_id: {process: report}} for every saved check in a chat."""
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            return {}
        rows = conn.execute(
            "SELECT k.part_id, k.process, k.report FROM chat_part_checks k "
            "JOIN chat_parts p ON p.id = k.part_id WHERE p.chat_id = ?", (chat_id,)).fetchall()
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        rep = _load(r["report"], None)
        if rep:
            out.setdefault(r["part_id"], {})[r["process"]] = rep
    return out


def get_check(user_id: str, chat_id: str, part_id: str, process: str) -> dict | None:
    return get_checks(user_id, chat_id).get(part_id, {}).get(process)


def save_check(user_id: str, chat_id: str, part_id: str, process: str, report: dict, charged: int) -> bool:
    """Store a report. False when this part and process already has one (someone else got there first),
    in which case nothing was written."""
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            raise LookupError("chat not found")
        if conn.execute("SELECT 1 FROM chat_parts WHERE id = ? AND chat_id = ?", (part_id, chat_id)).fetchone() is None:
            raise LookupError("part not found")
        cur = conn.execute(
            "INSERT OR IGNORE INTO chat_part_checks (part_id, process, report, charged, created_at) VALUES (?, ?, ?, ?, ?)",
            (part_id, process, _dump(report), int(charged), _now()))
        return cur.rowcount > 0


# ----------------------------------------------------------- requirements ---

MAX_REQUIREMENTS = 30


def _req(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "kind": r["kind"], "op": r["op"], "value": _load(r["value"], None), "text": r["text"] or "",
            "created_at": r["created_at"]}


def list_requirements(user_id: str, chat_id: str) -> list[dict]:
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            raise LookupError("chat not found")
        rows = conn.execute(
            "SELECT * FROM chat_requirements WHERE chat_id = ? AND removed_at IS NULL ORDER BY created_at, id", (chat_id,)).fetchall()
    return [_req(r) for r in rows]


def add_requirements(user_id: str, chat_id: str, reqs: list[dict]) -> list[dict]:
    """Store new requirements, skipping any identical to one already active. Returns only the
    ones actually added."""
    added: list[dict] = []
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            raise LookupError("chat not found")
        existing = {(r["kind"], r["value"]) for r in conn.execute(
            "SELECT kind, value FROM chat_requirements WHERE chat_id = ? AND removed_at IS NULL", (chat_id,)).fetchall()}
        count = len(existing)
        for r in reqs:
            key = (r["kind"], _dump(r["value"]))
            if key in existing:
                continue
            if count >= MAX_REQUIREMENTS:
                raise ChatLimitError(f"A chat can hold {MAX_REQUIREMENTS} requirements. Remove some first.")
            rid, now = _id(), _now()
            conn.execute(
                "INSERT INTO chat_requirements (id, chat_id, kind, op, value, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rid, chat_id, r["kind"], r.get("op", "<="), _dump(r["value"]), (r.get("text") or "")[:200], now))
            existing.add(key)
            count += 1
            added.append({"id": rid, "kind": r["kind"], "op": r.get("op", "<="), "value": r["value"], "text": r.get("text") or "", "created_at": now})
    return added


def remove_requirement(user_id: str, chat_id: str, requirement_id: str) -> bool:
    with _conn() as conn:
        if _owned(conn, user_id, chat_id) is None:
            return False
        cur = conn.execute("UPDATE chat_requirements SET removed_at = ? WHERE id = ? AND chat_id = ? AND removed_at IS NULL",
                           (_now(), requirement_id, chat_id))
        return cur.rowcount > 0


# ----------------------------------------------------------------- import ---

def import_chat(user_id: str, chat: dict, *, owns_job: Callable[[str], bool], max_chats: int = 200) -> str:
    """Bring in one chat from the browser-only version of CAD Studio.

    Messages are copied as text. A part is only kept if `owns_job(job_id)` confirms the
    job really belongs to this account - an imported chat can never be used to claim
    someone else's job_id. Parts that fail that check are dropped and the assistant
    message that pointed at them is kept without the part."""
    created = create_chat(user_id, chat.get("title"), max_chats=max_chats)
    cid = created["id"]
    kept: dict[str, str] = {}
    for old_id, p in (chat.get("parts") or {}).items():
        job_id = p.get("jobId") or p.get("job_id")
        if not job_id or not owns_job(str(job_id)):
            continue
        spec = {"part_type": p.get("partType") or p.get("part_type") or "part",
                "parameters": p.get("params") or p.get("parameters") or {}, "material": p.get("material") or None}
        new = add_part(user_id, cid, job_id=str(job_id), title=str(p.get("title") or "Part")[:80],
                       part_type=str(spec["part_type"]), spec=spec, notes=[str(n) for n in (p.get("notes") or [])][:10])
        kept[old_id] = new["id"]
    for m in (chat.get("messages") or [])[:400]:
        role = m.get("role")
        text = str(m.get("text") or m.get("content") or "")[:4000]
        if role not in ("user", "assistant") or not text:
            continue
        add_message(user_id, cid, role, text, part_id=kept.get(m.get("partId")), max_messages=400)
    return cid
