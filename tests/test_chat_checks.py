"""Manufacturability check and template start through chat_service: pricing, caching, refunds, races."""
import copy

import numpy as np
import pytest

import chat_service as svc
import chat_store as cs
import studio_templates
from _chat_fakes import BRACKET, fresh_store

U = "0xuser"
CUBE_TRIS = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
             [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]


def cube_mesh(s=20.0):
    v = np.array([[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0], [0, 0, s], [s, 0, s], [s, s, s], [0, s, s]], float)
    t = np.array(CUBE_TRIS)
    c = v.mean(axis=0)
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    flip = np.einsum("ij,ij->i", n, v[t].mean(axis=1) - c) < 0
    t[flip] = t[flip][:, ::-1]
    return v, t


class World:
    def __init__(self, credits=20, cost=5, mesh=True, save_fails=False):
        self.credits, self.cost, self.mesh = credits, cost, mesh
        self.charged, self.refunded, self.generated = [], [], []

    def deps(self):
        def charge(u, amount):
            if self.credits < amount:
                return None
            self.credits -= amount
            self.charged.append(amount)
            return "purchased"

        def refund(u, pool, amount):
            self.credits += amount
            self.refunded.append((pool, amount))

        def part_mesh(job_id):
            if self.mesh is False:
                raise RuntimeError("no stl")
            return cube_mesh()

        def generate(desc, spec, u, source):
            self.generated.append((desc, copy.deepcopy(spec), source))
            return {"success": True, "job_id": f"job{len(self.generated)}", "parameters": {"part_type": spec["part_type"], "parameters": spec["parameters"], "material": spec.get("material")},
                    "validation": {"warnings": [], "corrections": {}}}

        return svc.Deps(plan=lambda h, s: None, generate=generate, balance=lambda u: self.credits,
                        part_mesh=part_mesh, check_cost=self.cost, charge_credits=charge, refund_credits=refund)


def add(part_type="flat_plate", params=None, material="6061", job="j1"):
    cid = cs.create_chat(U)["id"]
    spec = {"part_type": part_type, "parameters": params or {"length_mm": 50, "width_mm": 50, "thickness_mm": 5}, "material": material, "operations": []}
    part = cs.add_part(U, cid, job_id=job, title="Plate", part_type=part_type, spec=spec)
    return cid, part["id"]


def setup(**kw):
    fresh_store()
    return World(**kw)


def test_check_charges_once_per_version_and_process_then_reopens_free():
    w = setup()
    cid, pid = add()
    a = svc.run_check(w.deps(), U, cid, pid, "cnc")
    assert a["charged"] == 5 and w.credits == 15 and a["balance"] == 15
    assert a["report"]["process"] == "cnc" and {"checks", "headline", "disclaimer"} <= set(a["report"])
    b = svc.run_check(w.deps(), U, cid, pid, "cnc")
    assert b["charged"] == 0 and w.credits == 15 and b["report"] == a["report"]
    c = svc.run_check(w.deps(), U, cid, pid, "fdm")                    # another process is a new check
    assert c["charged"] == 5 and w.credits == 10 and w.charged == [5, 5]


def test_each_version_is_charged_separately():
    w = setup()
    cid, p1 = add()
    spec = cs.get_part(U, cid, p1)["spec"]
    p2 = cs.add_part(U, cid, job_id="j2", title="Plate", part_type="flat_plate", spec=spec)["id"]
    svc.run_check(w.deps(), U, cid, p1, "cnc")
    svc.run_check(w.deps(), U, cid, p2, "cnc")
    assert w.charged == [5, 5]


def test_saved_reports_come_back_with_the_chat():
    w = setup()
    cid, pid = add()
    svc.run_check(w.deps(), U, cid, pid, "cnc")
    svc.run_check(w.deps(), U, cid, pid, "resin")
    assert set(cs.get_checks(U, cid)[pid]) == {"cnc", "resin"}


def test_not_enough_credits_charges_nothing_and_saves_nothing():
    w = setup(credits=3)
    cid, pid = add()
    with pytest.raises(svc.NeedCredits) as e:
        svc.run_check(w.deps(), U, cid, pid, "cnc")
    assert e.value.cost == 5 and e.value.balance == 3
    assert w.credits == 3 and w.charged == [] and cs.get_checks(U, cid) == {}


def test_a_saved_report_opens_even_with_no_credits_left():
    w = setup()
    cid, pid = add()
    svc.run_check(w.deps(), U, cid, pid, "cnc")
    w.credits = 0
    assert svc.run_check(w.deps(), U, cid, pid, "cnc")["charged"] == 0


def test_charge_that_loses_the_race_for_credits_is_not_saved():
    w = setup()
    cid, pid = add()
    d = w.deps()
    d.charge_credits = lambda u, amount: None            # balance looked fine, then another request spent it
    with pytest.raises(svc.NeedCredits):
        svc.run_check(d, U, cid, pid, "cnc")
    assert cs.get_checks(U, cid) == {}


def test_a_check_that_cannot_run_is_not_charged(monkeypatch=None):
    import manufacturability
    w = setup()
    cid, pid = add()
    original = manufacturability.run
    manufacturability.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        with pytest.raises(svc.CheckFailed):
            svc.run_check(w.deps(), U, cid, pid, "cnc")
    finally:
        manufacturability.run = original
    assert w.charged == [] and w.credits == 20


def test_credits_come_back_if_the_report_cannot_be_saved():
    w = setup()
    cid, pid = add()
    original = cs.save_check
    cs.save_check = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
    try:
        with pytest.raises(svc.CheckFailed):
            svc.run_check(w.deps(), U, cid, pid, "cnc")
    finally:
        cs.save_check = original
    assert w.credits == 20 and w.refunded == [("purchased", 5)]


def test_two_requests_for_the_same_check_charge_once():
    w = setup()
    cid, pid = add()
    d = w.deps()
    inner = d.charge_credits

    def charge_then_race(u, amount):
        pool = inner(u, amount)
        # another tab finished and saved first, between our charge and our save
        cs.save_check(U, cid, pid, "cnc", {"process": "cnc", "checks": [], "headline": "x"}, 5)
        return pool

    d.charge_credits = charge_then_race
    out = svc.run_check(d, U, cid, pid, "cnc")
    assert out["charged"] == 0 and w.refunded == [("purchased", 5)] and w.credits == 20
    assert out["report"]["headline"] == "x"


def test_busy_lock_is_reported():
    w = setup()
    cid, pid = add()
    lock = svc._lock_for(f"check:{pid}:cnc")
    lock.acquire()
    try:
        with pytest.raises(svc.ChatBusy):
            svc.run_check(w.deps(), U, cid, pid, "cnc")
    finally:
        lock.release()


def test_refusals_cost_nothing():
    w = setup()
    cid, pid = add()
    with pytest.raises(svc.CheckRefused):
        svc.run_check(w.deps(), U, cid, pid, "laser")
    acid, apid = add("assembly", {"parts": [], "layout": "custom"}, None)
    with pytest.raises(svc.CheckRefused) as e:
        svc.run_check(w.deps(), U, acid, apid, "cnc")
    assert "single parts" in str(e.value)
    with pytest.raises(svc.PartNotFound):
        svc.run_check(w.deps(), U, cid, "nope", "cnc")
    assert w.charged == []


def test_another_account_cannot_check_or_read_your_part():
    w = setup()
    cid, pid = add()
    with pytest.raises(svc.PartNotFound):
        svc.run_check(w.deps(), "0xother", cid, pid, "cnc")
    svc.run_check(w.deps(), U, cid, pid, "cnc")
    assert cs.get_checks("0xother", cid) == {}


def test_missing_mesh_falls_back_to_dimensions_and_says_so():
    w = setup(mesh=False)
    cid, pid = add()
    out = svc.run_check(w.deps(), U, cid, pid, "fdm")
    assert out["charged"] == 5
    assert any("mesh could not be read" in a for a in out["report"]["assumptions"])


def test_mesh_is_used_when_available():
    w = setup()
    cid, pid = add()
    out = svc.run_check(w.deps(), U, cid, pid, "fdm")
    ids = {c["id"] for c in out["report"]["checks"]}
    assert {"closed", "mesh_walls", "overhang"} <= ids


# ------------------------------------------------------------------ templates ---

def test_template_starts_a_free_preview_with_no_ai_and_is_stored():
    w = setup()
    cid = cs.create_chat(U)["id"]
    out = svc.start_template_part(w.deps(), U, cid, "gear-20")
    assert out["charged"] == 0 and w.charged == []
    assert out["part"]["part_type"] == "gear" and out["part"]["version"] == 1
    assert out["user_message"]["content"] == "Start from the template: Spur gear, 20 teeth"
    assert "free preview" in out["assistant_message"]["content"]
    assert w.generated[0][2] == "rules"
    chat = cs.get_chat(U, cid)
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"] and chat["title"] == "Spur gear, 20 teeth"


def test_template_assembly_keeps_its_parts_and_positions():
    w = setup()
    cid = cs.create_chat(U)["id"]
    out = svc.start_template_part(w.deps(), U, cid, "asm-gear-shaft")
    assert out["part"]["part_type"] == "assembly"
    parts = w.generated[0][1]["parameters"]["parts"]
    assert [p["name"] for p in parts] == ["shaft", "gear"] and parts[1]["position"] == [0.0, 0.0, 20.0]


def test_template_builds_from_a_copy_so_the_catalogue_never_changes():
    w = setup()
    cid = cs.create_chat(U)["id"]
    d = w.deps()
    inner = d.generate

    def mutate(desc, spec, u, source):
        spec["parameters"]["teeth"] = 999
        return inner(desc, spec, u, source)

    d.generate = mutate
    svc.start_template_part(d, U, cid, "gear-20")
    assert studio_templates.get("gear-20").spec["parameters"]["teeth"] == 20


def test_unknown_template_and_unknown_chat():
    w = setup()
    cid = cs.create_chat(U)["id"]
    with pytest.raises(svc.TemplateNotFound):
        svc.start_template_part(w.deps(), U, cid, "nope")
    with pytest.raises(svc.ChatNotFound):
        svc.start_template_part(w.deps(), U, "missing", "gear-20")
    assert len(cs.get_chat(U, cid)["messages"]) == 0


def test_failed_template_build_is_reported_not_charged():
    w = setup()
    cid = cs.create_chat(U)["id"]
    d = w.deps()
    d.generate = lambda *a: {"success": False, "error": "The part could not be built."}
    out = svc.start_template_part(d, U, cid, "gear-20")
    assert out["part"] is None and "not charged" in out["assistant_message"]["content"].lower()
    assert out["assistant_message"]["meta"] == {"error": True}


def test_template_starts_a_new_version_after_existing_work():
    w = setup()
    cid, _ = add()
    out = svc.start_template_part(w.deps(), U, cid, "bearing-608")
    assert out["part"]["version"] == 2
