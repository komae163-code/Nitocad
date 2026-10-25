"""
Where attached files that the person chose to edit are kept.

Reading a file for information (see file_analysis.py) stores nothing. A file only lands here
when it can be edited, because an edit is applied to the original every time the part is
rebuilt, previewed or exported, so the original has to still be there.

Per account, newest kept, with a cap. A file that a chat part or a job still refers to is never
deleted to make room. If every file is in use, a new one is refused with a plain message.

The stored bytes are compressed and are:
  STEP / IGES / BREP  -> the shape as OCCT BREP text
  DXF / SVG / KiCad / Gerber -> the flat shapes as JSON (see profiles.py)
  STL / OBJ           -> a binary STL
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import time
import uuid
import zlib
from dataclasses import dataclass
from typing import Any, Iterator

import db
from config import settings

_owner: contextvars.ContextVar[str | None] = contextvars.ContextVar("nitocad_import_owner", default=None)


class ImportLimitError(ValueError):
    """The account is at its limit and every stored file is in use. Safe to show."""


@contextlib.contextmanager
def owner(user_id: str | None) -> Iterator[None]:
    """Say whose files an edit may read while a part is built. Set by the generator."""
    token = _owner.set(user_id)
    try:
        yield
    finally:
        _owner.reset(token)


def current_owner() -> str | None:
    return _owner.get()


@dataclass
class ImportRecord:
    id: str
    kind: str
    filename: str
    meta: dict[str, Any]
    data: bytes


def _ensure(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS imports (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at REAL NOT NULL,
            meta TEXT NOT NULL,
            data BLOB NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imports_user ON imports(user_id, created_at)")


def _in_use(conn, import_id: str) -> bool:
    needle = f'%"import_id": "{import_id}"%'
    for table, col in (("jobs", "parameters"), ("chat_parts", "spec")):
        try:
            if conn.execute(f"SELECT 1 FROM {table} WHERE {col} LIKE ? LIMIT 1", (needle,)).fetchone():
                return True
        except Exception:                                  # noqa: BLE001 - table not created yet
            continue
    return False


def save(user_id: str, kind: str, filename: str, data: bytes, meta: dict[str, Any]) -> str:
    packed = zlib.compress(data, 6)
    if len(packed) > settings.FILE_MAX_IMPORT_BYTES:
        raise ImportLimitError("That file is too large to keep for editing")
    new_id = uuid.uuid4().hex
    with db.get_conn() as conn:
        _ensure(conn)
        rows = conn.execute("SELECT id FROM imports WHERE user_id = ? ORDER BY created_at ASC", (user_id,)).fetchall()
        over = len(rows) - settings.FILE_MAX_IMPORTS_PER_ACCOUNT + 1
        for r in rows:
            if over <= 0:
                break
            if not _in_use(conn, r["id"]):
                conn.execute("DELETE FROM imports WHERE id = ?", (r["id"],))
                over -= 1
        if over > 0:
            raise ImportLimitError(
                f"You have {settings.FILE_MAX_IMPORTS_PER_ACCOUNT} files in use by your designs. "
                "Delete a design that uses one, then try again"
            )
        conn.execute(
            "INSERT INTO imports (id, user_id, kind, filename, size_bytes, created_at, meta, data) VALUES (?,?,?,?,?,?,?,?)",
            (new_id, user_id, kind, filename[:120], len(data), time.time(), json.dumps(meta), packed),
        )
    return new_id


def get(user_id: str, import_id: str) -> ImportRecord | None:
    with db.get_conn() as conn:
        _ensure(conn)
        row = conn.execute("SELECT * FROM imports WHERE id = ? AND user_id = ?", (import_id, user_id)).fetchone()
    if row is None:
        return None
    return ImportRecord(row["id"], row["kind"], row["filename"], json.loads(row["meta"]), zlib.decompress(row["data"]))


def delete(user_id: str, import_id: str) -> bool:
    with db.get_conn() as conn:
        _ensure(conn)
        return conn.execute("DELETE FROM imports WHERE id = ? AND user_id = ?", (import_id, user_id)).rowcount > 0
