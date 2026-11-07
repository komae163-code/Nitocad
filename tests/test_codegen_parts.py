"""codegen_parts: library requests become pinned, credited STEP files. The network is faked; the real GitHub paths have not been run."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import codegen_parts as CP

STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
TREE = {"tree": [
    {"type": "blob", "path": "Mechanical Parts/Bearings/Bearing_608ZZ.step"},
    {"type": "blob", "path": "Mechanical Parts/Screws/Screw_M3x10.step"},
    {"type": "blob", "path": "Notes/readme.txt"},
    {"type": "blob", "path": "../evil/Bearing_evil.step"},
]}


def fake_net(state):
    state.setdefault("calls", [])
    state.setdefault("authors", ["Ada Lovelace", "Alan Turing"])
    state.setdefault("body", STEP)

    def get(url):
        state["calls"].append(url)
        if "/git/trees/" in url:
            return json.dumps(TREE).encode()
        if "/commits?" in url:
            if state["authors"] is None:
                raise OSError("rate limited")
            return json.dumps([{"commit": {"author": {"name": n}}} for n in state["authors"]]).encode()
        return state["body"]
    return get


def resolver(tmp_path, state):
    return CP.PartResolver(cache_dir=tmp_path / "cache", http_get=fake_net(state))


def run(coro):
    return asyncio.run(coro)


def test_resolve_downloads_credits_and_pins_a_step_part(tmp_path):
    st = {}
    r = run(resolver(tmp_path, st).resolve({"bearing": {"query": "608 bearing", "scale": 1.0}}))
    assert r["ok"], r
    e = r["entries"][0]
    assert e["name"] == "bearing" and e["part_name"] == "Bearing_608ZZ" and e["licence"] == "CC-BY-3.0"
    assert "Ada Lovelace" in e["attribution"] and e["credit_resolved"] is True and len(e["sha256"]) == 64
    part = r["parts"]["bearing"]
    assert Path(part["path"]).read_bytes() == STEP and part["scale"] == 1.0
    assert not [u for u in st["calls"] if "evil" in u]                              # index entries with .. never become records


def test_second_resolve_uses_the_cache_and_asks_the_network_for_nothing_but_the_index(tmp_path):
    st = {}
    rs = resolver(tmp_path, st)
    run(rs.resolve({"b": {"query": "608 bearing"}}))
    before = len(st["calls"])
    r = run(rs.resolve({"b": {"query": "608 bearing"}}))
    assert r["ok"] and len(st["calls"]) == before                                   # index cached too: no new calls at all


def test_nothing_found_and_unusable_files_give_messages_the_model_can_act_on(tmp_path):
    st = {}
    r = run(resolver(tmp_path, st).resolve({"gear": {"query": "helical gear"}}))
    assert not r["ok"] and "No library part matches 'helical gear'" in r["error"] and "Model it yourself" in r["error"]
    st2 = {"body": b"<html>not found</html>"}
    r = run(resolver(tmp_path / "x", st2).resolve({"b": {"query": "608 bearing"}}))
    assert not r["ok"] and "could not be downloaded" in r["error"]
    old = CP.MAX_PART_BYTES
    CP.MAX_PART_BYTES = 10
    try:
        r = run(resolver(tmp_path / "y", {}).resolve({"b": {"query": "608 bearing"}}))
        assert not r["ok"] and "larger than" in r["error"]
    finally:
        CP.MAX_PART_BYTES = old


def test_unresolved_credit_blocks_exports_until_it_can_be_looked_up(tmp_path):
    st = {"authors": None}
    rs = resolver(tmp_path, st)
    r = run(rs.resolve({"b": {"query": "608 bearing"}}))
    assert r["ok"] and r["entries"][0]["credit_resolved"] is False
    assert CP.credit_blockers(r["entries"]) == ["b"]
    st["authors"] = ["Grace Hopper"]                                                # GitHub is reachable again
    r2 = run(rs.restore(r["entries"]))
    assert r2["ok"] and r2["entries"][0]["credit_resolved"] is True and "Grace Hopper" in r2["entries"][0]["attribution"]
    assert CP.credit_blockers(r2["entries"]) == []


def test_restore_checks_the_recorded_hash_and_reads_the_cache(tmp_path):
    st = {}
    rs = resolver(tmp_path, st)
    entries = run(rs.resolve({"b": {"query": "608 bearing"}}))["entries"]
    n = len(st["calls"])
    ok = run(rs.restore(entries))
    assert ok["ok"] and len(st["calls"]) == n                                       # intact cache + resolved credit: no network
    for f in (tmp_path / "cache").glob("*.step"):                                  # cache corrupted: one clean download fixes it
        f.write_bytes(b"corrupt")
    assert run(rs.restore(entries))["ok"]
    st["body"] = STEP + b"; edited upstream"
    for f in (tmp_path / "cache").glob("*.step"):
        f.unlink()
    r = run(rs.restore(entries))
    assert not r["ok"] and "has changed" in r["error"]                              # upstream edits never silently alter a saved model


def test_notices_lists_every_part_with_its_licence_and_credit(tmp_path):
    r = run(resolver(tmp_path, {}).resolve({"bearing": {"query": "608 bearing"}, "screw": {"query": "M3 screw"}}))
    text = CP.notices(r["entries"])
    assert "Bearing_608ZZ" in text and "Screw_M3x10" in text and "CC-BY-3.0" in text and "Ada Lovelace" in text and "source: https://" in text
    assert CP.notices([]) == ""
