"""codegen_store: saved models survive, stay private to their owner, and respect the per-user cap. Uses a real sqlite file."""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codegen_store as CS


def make_store(tmp_path):
    path = tmp_path / "t.db"

    @contextmanager
    def get_conn():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    CS.configure(get_conn)
    CS.init_tables()
    return path, get_conn


def test_put_get_roundtrip_and_survives_a_restart(tmp_path):
    path, _ = make_store(tmp_path)
    lib = [{"name": "bearing", "sha256": "ab" * 32, "credit_resolved": True}]
    mid = CS.put("0xabc", code="x = 1\n", prompt="a bin", summary="A bin.", model="plus:gpt", library=lib, charged=25)
    got = CS.get("0xabc", mid)
    assert got["code"] == "x = 1\n" and got["library"] == lib and got["charged"] == 25 and got["parent_id"] is None
    make_store(tmp_path)                                  # a new process opens the same file: nothing is lost, init is idempotent
    assert CS.get("0xabc", mid)["prompt"] == "a bin"


def test_other_accounts_cannot_read_change_or_delete(tmp_path):
    make_store(tmp_path)
    mid = CS.put("0xabc", code="x = 1\n", prompt="p")
    assert CS.get("0xdef", mid) is None
    assert CS.delete("0xdef", mid) is False and CS.update_library("0xdef", mid, [{"name": "x"}]) is False
    assert CS.get("0xabc", mid)["library"] == []
    assert CS.list_for_user("0xdef") == []


def test_versions_link_to_their_parent_and_a_foreign_parent_is_ignored(tmp_path):
    make_store(tmp_path)
    a = CS.put("0xabc", code="a\n", prompt="first")
    b = CS.put("0xabc", code="b\n", prompt="taller", parent_id=a)
    foreign = CS.put("0xdef", code="c\n", prompt="other")
    c = CS.put("0xabc", code="d\n", prompt="sneaky", parent_id=foreign)
    assert CS.get("0xabc", b)["parent_id"] == a and CS.get("0xabc", c)["parent_id"] is None
    assert [r["prompt"] for r in CS.list_for_user("0xabc")] == ["sneaky", "taller", "first"]      # newest first
    assert all("code" not in r for r in CS.list_for_user("0xabc"))                                 # the list stays light
    CS.delete("0xabc", a)
    assert CS.get("0xabc", b)["parent_id"] is None                                                 # deleting a parent does not orphan-break


def test_cap_per_user_is_enforced_and_counted(tmp_path):
    make_store(tmp_path)
    old = CS.MAX_MODELS_PER_USER
    CS.MAX_MODELS_PER_USER = 3
    try:
        for i in range(3):
            CS.put("0xabc", code=f"{i}\n", prompt="p")
        assert CS.count("0xabc") == 3 and CS.count("0xdef") == 0
        try:
            CS.put("0xabc", code="x\n", prompt="p")
            raise AssertionError("cap not enforced")
        except CS.CodegenLimitError as e:
            assert "3 saved models" in str(e)
        CS.put("0xdef", code="x\n", prompt="p")                                                    # the cap is per user
    finally:
        CS.MAX_MODELS_PER_USER = old


def test_library_can_be_updated_in_place(tmp_path):
    make_store(tmp_path)
    mid = CS.put("0xabc", code="x\n", prompt="p", library=[{"name": "s", "credit_resolved": False}])
    assert CS.update_library("0xabc", mid, [{"name": "s", "credit_resolved": True}])
    assert CS.get("0xabc", mid)["library"][0]["credit_resolved"] is True
