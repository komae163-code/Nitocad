"""Saved code-first models (codegen_api.py), in the app's own SQLite database.

Same pattern as chat_store.py: stdlib only, `configure(db.get_conn)` once at startup, then `init_tables()`. The database
lives on the persistent volume the rest of the app already uses, so models survive restarts and redeploys.

One row is one version: a revision creates a NEW row whose parent_id points at the one it came from, so older versions
stay openable. Ownership is checked in every query (user_id is the signed-in account), so no route can forget it.
Only code that passed codegen_check.validate is ever stored; the code is data here and is never executed by this module.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Callable

MAX_MODELS_PER_USER = 300
MAX_TEXT = 2000

_get_conn: Callable[[], Any] | None = None


class CodegenLimitError(Exception):
    """A per-user cap was hit. The message is safe to show the user."""


def configure(get_conn: Callable[[], Any]) -> None:
    """Pass db.get_conn: a context manager yielding a sqlite3 connection that commits on success."""
    global _get_conn
    _get_conn = get_conn


def _conn():
    if _get_conn is None:
        raise RuntimeError("codegen_store.configure(get_conn) was never called")
    return _get_conn()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_tables() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS codegen_models (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                parent_id TEXT,
                prompt TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                code TEXT NOT NULL,
                model TEXT,
                library TEXT NOT NULL DEFAULT '[]',
                charged INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_codegen_user ON codegen_models(user_id, created_at)")


def _row(r: Any, with_code: bool) -> dict[str, Any]:
    out = {"id": r["id"], "parent_id": r["parent_id"], "prompt": r["prompt"], "summary": r["summary"], "model": r["model"],
           "charged": r["charged"], "created_at": r["created_at"], "library": _loads(r["library"])}
    if with_code:
        out["code"] = r["code"]
    return out


def _loads(s: str | None) -> list:
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except (TypeError, ValueError):
        return []


def put(user_id: str, *, code: str, prompt: str, summary: str = "", model: str | None = None, library: list | None = None,
        parent_id: str | None = None, charged: int = 0) -> str:
    """Save a new version and return its id."""
    with _conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM codegen_models WHERE user_id = ?", (user_id,)).fetchone()[0]
        if n >= MAX_MODELS_PER_USER:
            raise CodegenLimitError(f"You have {MAX_MODELS_PER_USER} saved models. Delete some to make a new one.")
        if parent_id is not None:
            owned = conn.execute("SELECT 1 FROM codegen_models WHERE id = ? AND user_id = ?", (parent_id, user_id)).fetchone()
            if owned is None:
                parent_id = None
        mid = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO codegen_models (id, user_id, parent_id, prompt, summary, code, model, library, charged, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, user_id, parent_id, (prompt or "")[:MAX_TEXT], (summary or "")[:MAX_TEXT], code, model,
             json.dumps(library or [], separators=(",", ":")), int(charged), _now()))
    return mid


def count(user_id: str) -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM codegen_models WHERE user_id = ?", (user_id,)).fetchone()[0]


def get(user_id: str, model_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM codegen_models WHERE id = ? AND user_id = ?", (model_id, user_id)).fetchone()
    return _row(r, True) if r else None


def list_for_user(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Newest first, without the code (it can be 20 KB each)."""
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM codegen_models WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                            (user_id, max(1, min(int(limit), 200)))).fetchall()
    return [_row(r, False) for r in rows]


def update_library(user_id: str, model_id: str, library: list) -> bool:
    """Replace the resolved library entries (for example after a credit line is looked up again)."""
    with _conn() as conn:
        cur = conn.execute("UPDATE codegen_models SET library = ? WHERE id = ? AND user_id = ?",
                           (json.dumps(library, separators=(",", ":")), model_id, user_id))
    return cur.rowcount > 0


def delete(user_id: str, model_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM codegen_models WHERE id = ? AND user_id = ?", (model_id, user_id))
        conn.execute("UPDATE codegen_models SET parent_id = NULL WHERE parent_id = ? AND user_id = ?", (model_id, user_id))
    return cur.rowcount > 0
