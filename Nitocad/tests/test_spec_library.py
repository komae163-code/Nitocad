"""Pure-Python tests for spec_library (memory and file stores)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import spec_library as SL


def lib(store=None):
    return SL.SpecLibrary(store or SL.MemoryStore())


def seeded(store=None):
    l = lib(store)
    r = l.seed_from_dir(ROOT / "vehicles" / "examples")
    assert not r["failed"], r["failed"]
    return l


def tiny(name="t", extra=None):
    s = {"name": name, "components": [{"id": "plate", "type": "flat_plate",
         "parameters": {"length_mm": 50, "width_mm": 30, "thickness_mm": 4}}]}
    s.update(extra or {})
    return s


def test_seed_loads_every_shipped_example_and_search_finds_the_right_one():
    l = seeded()
    names = {r["name"] for r in l.all(("seed",))}
    assert {"wheelbarrow", "bicycle", "rc_helicopter", "mini_loco_0_4_0_core", "two_axle_cart"} <= names
    assert l.search("garden barrow with a wheel")[0]["name"] == "wheelbarrow"
    assert l.search("bike")[0]["name"] == "bicycle"                      # synonym
    assert l.search("remote control chopper")[0]["name"] == "rc_helicopter"
    assert l.search("zzzz nothing matches") == []


def test_invalid_specs_are_never_stored():
    l = lib()
    r = l.add({"name": "bad", "components": [{"id": "a", "type": "warp_drive"}]}, "x")
    assert not r["ok"] and r["errors"] and l.all() == []


def test_candidates_are_not_used_as_examples_until_accepted():
    l = seeded()
    added = l.add(tiny("lawn_roller"), "a lawn roller")
    assert added["ok"] and added["status"] == "candidate"
    assert all(r["name"] != "lawn_roller" for r in l.search("lawn roller"))
    assert l.feedback(added["id"], True)["status"] == "accepted"
    assert l.search("lawn roller")[0]["name"] == "lawn_roller"
    l.feedback(added["id"], False)
    assert all(r["name"] != "lawn_roller" for r in l.search("lawn roller"))          # rejected again: gone


def test_rejection_lowers_rank_and_seed_status_is_kept():
    l = seeded()
    rec = l.search("wheelbarrow")[0]
    l.feedback(rec["id"], False, "tray too small")
    again = l.get(rec["id"])
    assert again["status"] == "seed" and again["rejected"] == 1 and again["last_note"] == "tray too small"


def test_readding_the_same_spec_keeps_its_status():
    l = lib()
    a = l.add(tiny(), "x")
    l.feedback(a["id"], True)
    b = l.add(tiny(), "x again")
    assert b["id"] == a["id"] and b["status"] == "accepted"


def test_lessons_count_patterns_not_exact_messages():
    l = lib()
    l.record_failure("a", ["component 'axle': type 'warp_drive' is not in the part catalog"])
    l.record_failure("b", ["component 'wheel': type 'flux' is not in the part catalog"])
    l.record_failure("c", ["derived 'x' must be an expression starting with '='"])
    out = l.lessons(min_count=2)
    assert len(out) == 1 and "(seen 2x)" in out[0] and "not in the part catalog" in out[0]


def test_file_store_roundtrip_and_key_safety(tmp_path):
    st = SL.FileStore(tmp_path)
    l = seeded(st)
    assert any(tmp_path.rglob("*.json"))
    l2 = lib(SL.FileStore(tmp_path))                                      # a fresh process sees the same library
    assert l2.search("bicycle")[0]["name"] == "bicycle"
    for bad in ("../evil", "a/../b", "/abs", "UP", "a b", ""):
        try:
            st.put(bad, "x")
        except ValueError:
            continue
        raise AssertionError(bad)
    assert l.feedback("../../etc", True)["ok"] is False and l.get("../x") is None


def test_value_size_cap():
    try:
        SL.MemoryStore().put("k", "x" * (SL.MAX_VALUE_BYTES + 1))
    except ValueError:
        return
    raise AssertionError("must reject")


# ------------------------------------------------------------------------------------------ review queue
def test_with_review_required_a_thumbs_up_queues_instead_of_promoting():
    l = SL.SpecLibrary(SL.MemoryStore(), review_required=True)
    a = l.add(tiny("lawn_roller"), "a lawn roller")
    r = l.feedback(a["id"], True)
    assert r["ok"] and r["status"] == "candidate" and r["pending_review"] is True
    assert l.search("lawn roller") == []                           # still not used as an example
    assert [x["id"] for x in l.pending_review()] == [a["id"]]
    assert l.approve(a["id"])["status"] == "accepted"
    assert l.search("lawn roller")[0]["name"] == "lawn_roller" and l.pending_review() == []


def test_decline_removes_it_from_the_queue_and_a_thumbs_down_still_demotes_at_once():
    l = SL.SpecLibrary(SL.MemoryStore(), review_required=True)
    a, b = l.add(tiny("one_thing"), "one"), l.add(tiny("two_thing"), "two")
    l.feedback(a["id"], True)
    assert l.decline(a["id"], "tray too small")["status"] == "rejected" and l.pending_review() == []
    assert l.feedback(b["id"], False)["status"] == "rejected"


def test_approve_refuses_unknown_ids_and_designs_whose_checks_fail():
    l = SL.SpecLibrary(SL.MemoryStore(), review_required=True)
    assert l.approve("nope")["ok"] is False
    bad = tiny("bad_thing") | {"checks": [{"name": "never", "expr": "=1 > 2", "message": "x"}]}
    a = l.add(bad, "bad")
    l.feedback(a["id"], True)
    r = l.approve(a["id"])
    assert r["ok"] is False and "never" in r["errors"][0]
    assert l.approve(a["id"], allow_failing_checks=True)["status"] == "accepted"


def test_default_library_still_promotes_on_the_first_thumbs_up():
    l = SL.SpecLibrary(SL.MemoryStore())
    a = l.add(tiny("quick_thing"), "q")
    assert l.feedback(a["id"], True)["status"] == "accepted"


def test_a_design_waiting_for_review_is_never_pruned_to_make_room():
    l = SL.SpecLibrary(SL.MemoryStore(), review_required=True)
    liked = l.add(tiny("liked_thing"), "")
    l.feedback(liked["id"], True)
    for i in range(4):
        l.add(tiny(f"filler_{i}"), "")
    l._prune_candidates(keep=2)
    assert l.get(liked["id"]) is not None and [x["id"] for x in l.pending_review()] == [liked["id"]]
    assert len(l.all(("candidate",))) == 2             # the liked one plus the newest unvoted one



def test_the_review_tool_says_so_when_the_library_is_only_in_memory(tmp_path):
    import os, subprocess
    (tmp_path / "afile").write_text("x")                                  # a file where a folder is needed: not writable
    env = {**os.environ, "VEHICLE_LIBRARY_DIR": str(tmp_path / "afile" / "lib")}
    r = subprocess.run([sys.executable, str(ROOT / "spec_library.py"), "pending"], capture_output=True, text=True, env=env, cwd=str(ROOT))
    assert r.returncode == 1 and "memory only" in r.stdout


def test_the_review_tool_lists_approves_and_declines_in_the_real_library_folder(tmp_path):
    import os, subprocess
    folder = tmp_path / "lib"
    env = {**os.environ, "VEHICLE_LIBRARY_DIR": str(folder)}
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "spec_library.py"), *a], capture_output=True, text=True, env=env, cwd=str(ROOT))
    l = SL.SpecLibrary(SL.FileStore(folder), review_required=True)
    a, b = l.add(tiny("tool_one"), ""), l.add(tiny("tool_two"), "")
    l.feedback(a["id"], True)
    l.feedback(b["id"], True)
    assert a["id"] in run("pending").stdout and b["id"] in run("pending").stdout
    assert run("approve", a["id"]).returncode == 0 and run("decline", b["id"]).returncode == 0
    assert run("pending").stdout.strip() == ""
    assert run("approve", "nope").returncode == 1
