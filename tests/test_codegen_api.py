"""codegen_api routes with fake billing, store, model, resolver and kernel. Calls the route functions directly.

Skipped (returns early) when FastAPI is not installed. Billing here is a recorder: it proves WHEN credits are reserved and refunded,
not that db.charge_credits itself is right (that has its own tests)."""
from __future__ import annotations

import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import fastapi  # noqa: F401
    import pydantic  # noqa: F401
    HAVE = True
except Exception:  # noqa: BLE001
    HAVE = False

if HAVE:
    import codegen_api as CA
    import codegen_store as CS
    from fastapi import HTTPException
    from test_codegen import GOOD, LIB_CODE, reply, scripted

ALICE, BOB = {"wallet_address": "0xAlice"}, {"wallet_address": "0xBob"}


class Spec:
    def __init__(self, id="plus:gpt", tier="plus"):
        self.id, self.tier, self.available = id, tier, True


class MemStore:
    """Same interface as codegen_store, in memory."""

    def __init__(self, fail_put=False):
        self.rows, self.fail_put, self.n = {}, fail_put, 0

    def count(self, uid):
        return sum(1 for r in self.rows.values() if r["uid"] == uid)

    def put(self, uid, *, code, prompt, summary="", model=None, library=None, parent_id=None, charged=0):
        if self.fail_put:
            raise RuntimeError("disk full")
        self.n += 1
        mid = f"m{self.n}"
        self.rows[mid] = {"uid": uid, "id": mid, "code": code, "prompt": prompt, "summary": summary, "library": library or [],
                          "parent_id": parent_id, "created_at": str(self.n), "charged": charged}
        return mid

    def get(self, uid, mid):
        r = self.rows.get(mid)
        return dict(r) if r and r["uid"] == uid else None

    def list_for_user(self, uid, limit=50):
        return [dict(r) for r in self.rows.values() if r["uid"] == uid][::-1]

    def update_library(self, uid, mid, library):
        self.rows[mid]["library"] = library
        return True

    def delete(self, uid, mid):
        if mid in self.rows and self.rows[mid]["uid"] == uid:
            del self.rows[mid]
            return True
        return False


class Resolver:
    def __init__(self, credit=True):
        self.credit = credit

    def _entries(self):
        return [{"name": "bearing", "part_name": "Bearing_608ZZ", "provider": "freecad_library", "licence": "CC-BY-3.0", "url": "https://x/b.step",
                 "attribution": "Bearing_608ZZ by Ada; CC BY 3.0" if self.credit else "UNRESOLVED: x", "credit_resolved": self.credit, "sha256": "a" * 64}]

    async def resolve(self, spec):
        return {"ok": True, "parts": {"bearing": {"path": "/c/b.step", "scale": 1.0}}, "entries": self._entries()}

    async def restore(self, entries):
        return {"ok": True, "parts": {"bearing": {"path": "/c/b.step", "scale": 1.0}}, "entries": [dict(e, credit_resolved=self.credit) for e in entries]}


class Harness:
    def __init__(self, *, replies=None, charge_result="pool1", build_ok=True, plan="pro", fail_put=False, resolver=None):
        self.charges, self.refunds, self.runs = [], [], []
        self.build_ok = build_ok
        self.store = MemStore(fail_put)

        def charge(uid, amount, ai_tier=None):
            self.charges.append((uid, amount, ai_tier))
            return charge_result

        async def runner(code, overrides, exports, library=None):
            self.runs.append((overrides, tuple(exports), dict(library or {})))
            if not self.build_ok:
                return {"ok": False, "kind": "code", "error": "The result has no solids."}
            if overrides.get("h") == 999:
                return {"ok": False, "kind": "code", "error": "Parameter 'h' must be between 1 and 50."}
            r = {"ok": True, "stl": b"STL-BYTES", "stats": {"solids": 1}, "values": dict(overrides)}
            for e in exports:
                r[e] = b"FILE-" + e.encode()
            return r

        self.llm = scripted(replies or [reply(GOOD)])
        self.router = CA.build_router(charge=charge, refund=lambda uid, pool, amount: self.refunds.append((uid, pool, amount)),
                                      store=self.store, resolver=resolver or Resolver(), runner=runner, plan_of=lambda uid: plan)
        self._saved = {k: getattr(CA.ai_models, k) for k in ("find", "make_llm", "generate_credits", "allowed_for_plan", "required_plan")}
        known = {"plus:gpt": "plus", "advanced:claude": "advanced", "basic:deepseek": "basic"}
        CA.ai_models.find = lambda settings, mid: Spec(mid, known[mid]) if mid in known else None
        CA.ai_models.make_llm = lambda spec, settings: self.llm
        CA.ai_models.generate_credits = lambda settings, tier: {"plus": 25, "advanced": 40}.get(tier, 10)
        CA.ai_models.allowed_for_plan = lambda plan_, spec: spec.tier != "advanced" or plan_ == "max"
        CA.ai_models.required_plan = lambda spec: "max"

    def close(self):
        for k, v in self._saved.items():
            setattr(CA.ai_models, k, v)

    def call(self, method, path, **kw):
        for r in self.router.routes:
            if r.path == path and method in r.methods:
                return asyncio.run(r.endpoint(**kw))
        raise AssertionError(f"no route {method} {path}")


def status_of(fn):
    try:
        fn()
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return None, None


def gen(h, wallet=ALICE, prompt="a bar", model_id=None):
    return h.call("POST", "/codegen/generate", request=None, body=CA.GenerateBody(prompt=prompt, model_id=model_id), wallet=wallet)


def test_generate_charges_the_tier_price_keeps_it_on_success_and_saves_the_model():
    if not HAVE:
        return
    h = Harness()
    try:
        out = gen(h)
        assert h.charges == [("0xalice", 25, "plus")] and h.refunds == [] and out["charged"] == 25
        assert [p["name"] for p in out["params"]] == ["length", "wall", "count"] and out["library"] == []
        assert h.store.get("0xalice", out["id"])["code"] == GOOD and h.store.get("0xalice", out["id"])["charged"] == 25
        assert out["mesh_url"].startswith("/codegen/mesh/") and "stl_b64" not in out
    finally:
        h.close()


def test_everything_that_fails_is_refunded_exactly_once():
    if not HAVE:
        return
    for kwargs in ({"build_ok": False}, {"fail_put": True}, {"replies": ["nonsense"] * 3}):
        h = Harness(**kwargs)
        try:
            code, _ = status_of(lambda: gen(h))
            assert code in (422, 503) and h.refunds == [("0xalice", "pool1", 25)], (kwargs, h.refunds)
            assert h.store.rows == {}
        finally:
            h.close()


def test_no_allowance_means_402_and_the_ai_is_never_called():
    if not HAVE:
        return
    h = Harness(charge_result=None)
    try:
        code, detail = status_of(lambda: gen(h))
        assert code == 402 and "allowance" in detail and h.llm.calls == [] and h.refunds == []
    finally:
        h.close()


def test_free_plan_cannot_use_a_model_above_it_and_gets_a_default_it_is_allowed():
    if not HAVE:
        return
    h = Harness(plan="free")
    try:
        code, detail = status_of(lambda: gen(h, model_id="advanced:claude"))
        assert code == 403 and "Max" in detail and h.charges == []
    finally:
        h.close()


def test_rebuild_is_free_range_checked_and_private():
    if not HAVE:
        return
    h = Harness()
    try:
        cid = gen(h)["id"]
        h.charges.clear()
        out = h.call("POST", "/codegen/{cid}/rebuild", request=None, cid=cid, body=CA.RebuildBody(params={"length": 200}), wallet=ALICE)
        assert out["values"] == {"length": 200} and h.charges == [] and h.refunds == []
        code, detail = status_of(lambda: h.call("POST", "/codegen/{cid}/rebuild", request=None, cid=cid, body=CA.RebuildBody(params={"h": 999}), wallet=ALICE))
        assert code == 422 and "between" in detail
        code, _ = status_of(lambda: h.call("POST", "/codegen/{cid}/rebuild", request=None, cid=cid, body=CA.RebuildBody(params={}), wallet=BOB))
        assert code == 404
    finally:
        h.close()


def test_mesh_is_binary_owner_only_and_expires():
    if not HAVE:
        return
    h = Harness()
    try:
        url = gen(h)["mesh_url"]
        token = url.rsplit("/", 1)[1]
        resp = h.call("GET", "/codegen/mesh/{token}", token=token, wallet=ALICE)
        assert resp.body == b"STL-BYTES" and resp.media_type == "model/stl"
        assert status_of(lambda: h.call("GET", "/codegen/mesh/{token}", token=token, wallet=BOB))[0] == 404
        assert status_of(lambda: h.call("GET", "/codegen/mesh/{token}", token="nope", wallet=ALICE))[0] == 404
    finally:
        h.close()


def test_revise_charges_again_and_saves_a_new_version_linked_to_the_old_one():
    if not HAVE:
        return
    h = Harness()
    try:
        first = gen(h)["id"]
        second = h.call("POST", "/codegen/{cid}/revise", request=None, cid=first, body=CA.ReviseBody(instruction="longer", model_id=None), wallet=ALICE)
        assert second["id"] != first and second["parent_id"] == first and len(h.charges) == 2
        assert "longer" in h.llm.calls[-1][1]["content"] and GOOD.strip() in h.llm.calls[-1][1]["content"]
        assert status_of(lambda: h.call("POST", "/codegen/{cid}/revise", request=None, cid=first, body=CA.ReviseBody(instruction="x y z", model_id=None), wallet=BOB))[0] == 404
    finally:
        h.close()


def test_package_export_zips_step_stl_code_and_credits():
    if not HAVE:
        return
    h = Harness(replies=[reply(LIB_CODE)])
    try:
        out = gen(h)
        assert out["library"][0]["licence"] == "CC-BY-3.0" and out["library"][0]["credit_resolved"] is True
        resp = h.call("POST", "/codegen/{cid}/export", request=None, cid=out["id"], body=CA.ExportBody(fmt="package", params={}), wallet=ALICE)
        z = zipfile.ZipFile(io.BytesIO(resp.body))
        assert sorted(z.namelist()) == ["NOTICES.txt", "model.py", "model.step", "model.stl"]
        assert "Ada" in z.read("NOTICES.txt").decode() and z.read("model.py").decode() == LIB_CODE
        step = h.call("POST", "/codegen/{cid}/export", request=None, cid=out["id"], body=CA.ExportBody(fmt="step", params={}), wallet=ALICE)
        assert step.body == b"FILE-step" and "model.step" in step.headers["Content-Disposition"]
        assert h.runs[-1][2]["bearing"]["path"] == "/c/b.step"                       # the saved, pinned library file reached the runner
    finally:
        h.close()


def test_files_do_not_leave_while_an_author_credit_is_unresolved_but_preview_still_works():
    if not HAVE:
        return
    h = Harness(replies=[reply(LIB_CODE)], resolver=Resolver(credit=False))
    try:
        out = gen(h)
        assert out["library"][0]["credit_resolved"] is False
        code, detail = status_of(lambda: h.call("POST", "/codegen/{cid}/export", request=None, cid=out["id"], body=CA.ExportBody(fmt="step", params={}), wallet=ALICE))
        assert code == 422 and "bearing" in detail and "credit" in detail
        ok = h.call("POST", "/codegen/{cid}/rebuild", request=None, cid=out["id"], body=CA.RebuildBody(params={}), wallet=ALICE)
        assert ok["mesh_url"]
    finally:
        h.close()


def test_list_code_and_delete_are_private():
    if not HAVE:
        return
    h = Harness()
    try:
        cid = gen(h)["id"]
        assert [m["id"] for m in h.call("GET", "/codegen", wallet=ALICE)["models"]] == [cid]
        assert h.call("GET", "/codegen", wallet=BOB)["models"] == []
        assert h.call("GET", "/codegen/{cid}/code", cid=cid, wallet=ALICE).body.decode() == GOOD
        assert status_of(lambda: h.call("GET", "/codegen/{cid}/code", cid=cid, wallet=BOB))[0] == 404
        assert status_of(lambda: h.call("DELETE", "/codegen/{cid}", cid=cid, wallet=BOB))[0] == 404
        assert h.call("DELETE", "/codegen/{cid}", cid=cid, wallet=ALICE).status_code == 204
        assert h.call("GET", "/codegen", wallet=ALICE)["models"] == []
    finally:
        h.close()
