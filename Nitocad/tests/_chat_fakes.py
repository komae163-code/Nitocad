"""Helpers shared by the chat tests. No FastAPI, no CadQuery, no network."""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chat_store  # noqa: E402


def fresh_store() -> str:
    """Point chat_store at a brand-new sqlite file and create its tables."""
    path = str(Path(tempfile.mkdtemp()) / "chat_test.db")

    @contextlib.contextmanager
    def get_conn():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    chat_store.configure(get_conn)
    chat_store.init_tables()
    return path


BRACKET = {"part_type": "l_bracket", "material": None, "operations": [],
           "parameters": {"width_mm": 50, "height_mm": 60, "depth_mm": 40, "thickness_mm": 3,
                          "hole_count": 4, "hole_diameter_mm": 5, "fillet_radius_mm": 5}}
