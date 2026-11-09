"""The library never shows the model a design that is too coarse to teach by example."""
import json
from pathlib import Path

import spec_library as SL
import vehicle_planner as VP

ROOT = Path(__file__).resolve().parents[1]
SIDE = [[-2100, 150], [2100, 150], [2100, 670], [1720, 780], [-1840, 780], [-2100, 710]]


def coarse_car():
    return {"name": "family_car", "components": [
        {"id": "body", "type": "feature_tree", "assembly": "main", "position": [0, 0, 0], "parameters": {"features": [
            {"id": "lower", "op": "extrude", "plane": "XZ", "symmetric": True, "distance": 1780,
             "profile": {"type": "polygon", "points": SIDE}}]}},
        {"id": "exhaust", "type": "tube", "assembly": "main", "position": [0, 300, 100],
         "parameters": {"outer_diameter_mm": 50, "wall_thickness_mm": 2, "length_mm": 3000}}]}


def seeded():
    lib = SL.SpecLibrary(SL.MemoryStore())
    lib.seed_from_dir(ROOT / "vehicles" / "examples")
    return lib


def test_every_shipped_example_passes_the_quality_gate():
    for rec in seeded().all(("seed",)):
        assert rec["coarse"] == [], (rec["name"], rec["coarse"])


def test_a_coarse_spec_that_an_older_version_marked_accepted_is_never_returned():
    lib = seeded()
    lib.add(coarse_car(), "", status="accepted")
    assert [r["name"] for r in lib.search("family car", k=5)].count("family_car") == 0
    assert lib.search("family car", k=1)[0]["name"] == "hatchback_car"


def test_a_record_stored_without_a_verdict_is_judged_when_it_is_searched():
    lib = SL.SpecLibrary(SL.MemoryStore())
    lib.add(coarse_car(), "", status="accepted")
    rid = lib.all()[0]["id"]
    rec = json.loads(lib.store.get(f"specs/{rid}"))
    rec.pop("coarse")
    lib.store.put(f"specs/{rid}", json.dumps(rec))
    assert lib.search("family car") == []


def test_a_thumbs_up_cannot_promote_a_coarse_design():
    lib = SL.SpecLibrary(SL.MemoryStore())
    rid = lib.add(coarse_car(), "")["id"]
    r = lib.feedback(rid, True)
    assert not r["ok"] and "too coarse" in r["errors"][0]
    assert lib.get(rid)["status"] == "candidate"
    assert lib.feedback(rid, False)["ok"]                                 # a thumbs-down is still allowed


def test_an_operator_approval_refuses_it_too():
    lib = SL.SpecLibrary(SL.MemoryStore(), review_required=True)
    rid = lib.add(coarse_car(), "")["id"]
    assert not lib.approve(rid)["ok"] and "split into parts" in lib.approve(rid)["errors"][0]
    assert lib.approve(rid, allow_failing_checks=True)["ok"]               # the operator can still decide otherwise


def test_a_good_design_is_still_promoted():
    lib = SL.SpecLibrary(SL.MemoryStore())
    spec = json.loads((ROOT / "vehicles" / "examples" / "bicycle.json").read_text())
    spec["name"] = "my_bicycle"
    rid = lib.add(spec, "")["id"]
    assert lib.feedback(rid, True)["ok"] and lib.get(rid)["status"] == "accepted"


def test_the_prompt_never_contains_a_coarse_example():
    lib = seeded()
    lib.add(coarse_car(), "", status="accepted")
    messages, used = VP.build_messages("a family car", lib, VP.VS._default_catalog(), None)
    system = messages[0]["content"]
    assert "hatchback_car" in system and '"name":"family_car"' not in system
