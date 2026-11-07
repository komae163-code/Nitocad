"""HTTP routes for the code-first CAD feature: the AI writes CadQuery code once, sliders re-run it for free.

  POST   /codegen/generate        {prompt, model_id?}        AI writes the code, it is built; you get sliders and a preview link
  POST   /codegen/{id}/revise     {instruction, model_id?}   AI edits the current code (saved as a new version)
  POST   /codegen/{id}/rebuild    {params}                   re-run the SAME code with new slider values: no AI, no charge
  POST   /codegen/{id}/export     {fmt, params}              step | stl | brep | package (zip: STEP, STL, code, credits)
  GET    /codegen/mesh/{token}                               the binary STL a build just produced (short-lived, owner only)
  GET    /codegen                                            your saved models, newest first
  GET    /codegen/{id}/code                                  the code, as plain text
  DELETE /codegen/{id}                                       delete a saved model

Money. Generate and revise cost what a normal generation costs on the chosen model's tier (ai_models.generate_credits), reserved
before the AI runs and refunded in full when no model results (AI failure, build failure, busy kernel, storage failure). Rebuild,
export and reading are free. The model list respects the person's plan, like the chat does.

Safety. People never post code. They post a prompt (to the AI) or numbers (to the sliders); only AI code that passed the validator
is saved or run, and it runs only in the sandbox (codegen_run.py). Slider values are range-checked inside the sandbox.

Library parts. When the AI's code asks for standard parts (LIBRARY), codegen_parts.py fetches them, licence-checked and hash-pinned.
A model with library parts shows its credits, and files only leave the app once every author credit has been looked up.
"""
from __future__ import annotations

import asyncio
import io
import secrets
import threading
import time
import zipfile
from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import ai_models
import codegen_check as CC
import codegen_llm as CL
import codegen_parts as CP
import codegen_run as CR
import codegen_store
import wallet_auth
from config import settings
from logging_config import get_logger

logger = get_logger(__name__)
Wallet = Annotated[dict, Depends(wallet_auth.get_current_wallet)]

AI_RATE = "20/hour"           # generate + revise: real provider cost per call
BUILD_RATE = "60/minute"      # rebuild + export: CPU only
MODEL_PREFERENCE = ("advanced:claude", "plus:gpt", "plus:qwen", "basic:deepseek")   # code needs the strongest model the plan allows
MEDIA = {"stl": "model/stl", "step": "application/step", "brep": "application/octet-stream", "package": "application/zip"}
OUT_OF_ALLOWANCE = "You have used your AI allowance for now. Subscribe or add a boost, then try again."


class MeshCache:
    """The STL a build just made, kept briefly so the browser can fetch it as plain binary instead of base64 inside JSON."""

    def __init__(self, ttl_s: float = 180.0, max_items: int = 64, max_bytes: int = 256 * 1024 * 1024):
        self._ttl, self._max_items, self._max_bytes = ttl_s, max_items, max_bytes
        self._items: dict[str, tuple[float, str, bytes]] = {}
        self._lock = threading.Lock()

    def put(self, uid: str, data: bytes) -> str:
        token = secrets.token_urlsafe(16)
        now = time.time()
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if now - v[0] < self._ttl}
            while self._items and (len(self._items) >= self._max_items or sum(len(v[2]) for v in self._items.values()) + len(data) > self._max_bytes):
                self._items.pop(min(self._items, key=lambda k: self._items[k][0]))
            self._items[token] = (now, uid, data)
        return token

    def get(self, uid: str, token: str) -> bytes | None:
        with self._lock:
            it = self._items.get(token)
            if it is None or it[1] != uid or time.time() - it[0] >= self._ttl:
                return None
            return it[2]


class GenerateBody(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    model_id: str | None = Field(default=None, max_length=40)


class ReviseBody(BaseModel):
    instruction: str = Field(min_length=3, max_length=2000)
    model_id: str | None = Field(default=None, max_length=40)


class RebuildBody(BaseModel):
    params: dict[str, float] = Field(default_factory=dict, max_length=CC.MAX_PARAMS)


class ExportBody(BaseModel):
    fmt: Literal["step", "stl", "brep", "package"] = "step"
    params: dict[str, float] = Field(default_factory=dict, max_length=CC.MAX_PARAMS)


def _uid(wallet: dict) -> str:
    return wallet["wallet_address"].lower()


def _public_library(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": e["name"], "part_name": e.get("part_name") or e["name"], "provider": e.get("provider"), "licence": e.get("licence"),
             "attribution": e.get("attribution", ""), "credit_resolved": bool(e.get("credit_resolved")), "url": e.get("url", "")}
            for e in entries]


def _fail(out: dict[str, Any]) -> HTTPException:
    detail = "; ".join(out.get("errors") or ["The model could not be built."])
    return HTTPException(status_code=503 if out.get("infra") else 422, detail=detail[:900])


def _zip_package(code: str, step: bytes | None, stl: bytes | None, library: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if step:
            z.writestr("model.step", step)
        if stl:
            z.writestr("model.stl", stl)
        z.writestr("model.py", code)
        text = CP.notices(library)
        if text:
            z.writestr("NOTICES.txt", text)
    return buf.getvalue()


_SERVICE: Any = None


def get_service() -> Any:
    """The service of the router built last, for other routers that call the code builder in this process."""
    return _SERVICE


def build_router(*, limiter: Any = None, charge: Callable | None = None, refund: Callable | None = None, store: Any = None,
                 resolver: Any = None, runner: Callable | None = None, plan_of: Callable[[str], str] | None = None,
                 mesh_cache: MeshCache | None = None) -> APIRouter:
    """Everything with a side effect can be replaced, so the routes are testable without a database, a model or a kernel."""
    if charge is None or refund is None or plan_of is None:
        import db
        charge = charge or db.charge_credits
        refund = refund or db.refund_credits
        plan_of = plan_of or db.get_plan
    store = store or codegen_store
    resolver = resolver or CP.PartResolver()
    runner = runner or CR.run
    meshes = mesh_cache or MeshCache()
    router = APIRouter(tags=["codegen"])

    def rate(limit: str):
        return limiter.limit(limit) if limiter is not None else (lambda f: f)

    # ------------------------------------------------------------------ helpers
    def pick_model(uid: str, model_id: str | None):
        plan = plan_of(uid)
        if model_id:
            spec = ai_models.find(settings, model_id)
            if spec is None or not spec.available:
                raise HTTPException(status_code=422, detail="That AI model is not available.")
            if not ai_models.allowed_for_plan(plan, spec):
                raise HTTPException(status_code=403, detail=f"That model needs the {ai_models.required_plan(spec).title()} plan.")
        else:
            specs = (ai_models.find(settings, m) for m in MODEL_PREFERENCE)
            spec = next((s for s in specs if s is not None and s.available and ai_models.allowed_for_plan(plan, s)), None)
            if spec is None:
                raise HTTPException(status_code=503, detail="No AI model is available for code generation on your plan.")
        llm = ai_models.make_llm(spec, settings)
        if llm is None:
            raise HTTPException(status_code=422, detail="Engine-only mode cannot write code. Pick an AI model.")
        return llm, spec

    def owned(uid: str, cid: str) -> dict[str, Any]:
        entry = store.get(uid, cid)
        if entry is None:
            raise HTTPException(status_code=404, detail="Model not found.")
        return entry

    def cost_of(spec) -> int:
        return ai_models.generate_credits(settings, spec.tier)

    async def paid(uid: str, spec, work: Callable) -> tuple[dict[str, Any], int]:
        """Reserve the cost, run `work`, and give everything back unless it produced a saved model."""
        cost = cost_of(spec)
        pool = "free"
        if cost > 0:
            pool = await asyncio.to_thread(charge, uid, cost, ai_tier=spec.tier)
            if pool is None:
                raise HTTPException(status_code=402, detail=OUT_OF_ALLOWANCE)
        kept = False
        try:
            out = await work()
            kept = bool(out.get("ok"))
            return out, cost
        finally:
            if not kept and pool not in (None, "free"):
                try:
                    await asyncio.to_thread(refund, uid, pool, cost)
                except Exception:                                    # noqa: BLE001  never mask the real error with a refund problem
                    logger.exception("codegen refund failed")

    def payload(uid: str, entry: dict[str, Any], result: dict[str, Any], library: list[dict[str, Any]], charged: int | None = None) -> dict[str, Any]:
        params = CC.parse_params(entry["code"])
        token = meshes.put(uid, result["stl"])
        out = {"id": entry["id"], "parent_id": entry.get("parent_id"), "prompt": entry.get("prompt", ""), "summary": entry.get("summary", ""),
               "params": [{"name": k, **v} for k, v in params.items()], "values": result.get("values", {}), "stats": result.get("stats", {}),
               "library": _public_library(library), "mesh_url": f"/codegen/mesh/{token}"}
        if charged is not None:
            out["charged"] = charged
        return out

    async def library_for(uid: str, entry: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """(parts for the runner, entries to show). Re-checks pinned hashes; saves a credit line that has resolved since."""
        entries = entry.get("library") or []
        if not entries:
            return {}, []
        res = await resolver.restore(entries)
        if not res.get("ok"):
            raise HTTPException(status_code=422, detail=str(res.get("error"))[:600])
        if res["entries"] != entries:
            await asyncio.to_thread(store.update_library, uid, entry["id"], res["entries"])
        return res["parts"], res["entries"]

    async def generate_and_save(uid: str, prompt: str, model_id: str | None, previous: dict[str, Any] | None) -> dict[str, Any]:
        if await asyncio.to_thread(store.count, uid) >= codegen_store.MAX_MODELS_PER_USER:
            raise HTTPException(status_code=409, detail=f"You have {codegen_store.MAX_MODELS_PER_USER} saved models. Delete some to make a new one.")
        llm, spec = pick_model(uid, model_id)

        async def work() -> dict[str, Any]:
            with ai_models.capture_usage() as usage:
                out = await CL.generate(prompt, llm, runner, resolver=resolver, previous_code=previous["code"] if previous else None)
            logger.info("codegen ok=%s attempts=%s model=%s tokens=%s", out["ok"], out.get("attempts"), spec.id, usage)
            if not out["ok"]:
                return out
            try:
                out["id"] = await asyncio.to_thread(
                    store.put, uid, code=out["code"], prompt=prompt.strip(), summary=out["summary"], model=spec.id,
                    library=out["library"], parent_id=previous["id"] if previous else None, charged=cost_of(spec))
            except codegen_store.CodegenLimitError as e:
                return {"ok": False, "errors": [str(e)]}
            except Exception:                                        # noqa: BLE001
                logger.exception("codegen save failed")
                return {"ok": False, "errors": ["The model could not be saved, so you were not charged."], "infra": True}
            return out

        out, cost = await paid(uid, spec, work)
        if not out["ok"]:
            raise _fail(out)
        entry = await asyncio.to_thread(store.get, uid, out["id"])
        return payload(uid, entry, out["result"], out["library"], charged=cost)

    async def build_current(uid: str, cid: str, params: dict[str, float], exports: tuple[str, ...]):
        entry = owned(uid, cid)
        parts, library = await library_for(uid, entry)
        if exports and CP.credit_blockers(library):
            names = ", ".join(CP.credit_blockers(library))
            raise HTTPException(status_code=422, detail=f"The author credit for {names} could not be looked up right now, so files cannot be "
                                                        "exported yet. Try again in a few minutes.")
        res = await runner(entry["code"], params, exports, parts)
        if not res["ok"]:
            raise HTTPException(status_code=422, detail=res["error"])
        return entry, res, library

    # ------------------------------------------------------------------ routes
    @router.post("/codegen/generate")
    @rate(AI_RATE)
    async def codegen_generate(request: Request, body: GenerateBody, wallet: Wallet):
        return await generate_and_save(_uid(wallet), body.prompt, body.model_id, None)

    @router.post("/codegen/{cid}/revise")
    @rate(AI_RATE)
    async def codegen_revise(request: Request, cid: str, body: ReviseBody, wallet: Wallet):
        uid = _uid(wallet)
        return await generate_and_save(uid, body.instruction, body.model_id, owned(uid, cid))

    @router.post("/codegen/{cid}/rebuild")
    @rate(BUILD_RATE)
    async def codegen_rebuild(request: Request, cid: str, body: RebuildBody, wallet: Wallet):
        uid = _uid(wallet)
        entry, res, library = await build_current(uid, cid, body.params, ())
        return payload(uid, entry, res, library)

    @router.post("/codegen/{cid}/export")
    @rate(BUILD_RATE)
    async def codegen_export(request: Request, cid: str, body: ExportBody, wallet: Wallet):
        uid = _uid(wallet)
        want = ("step", "stl") if body.fmt == "package" else (body.fmt,)
        entry, res, library = await build_current(uid, cid, body.params, want)
        if body.fmt == "package":
            data = _zip_package(entry["code"], res.get("step"), res.get("stl"), library)
        else:
            data = res.get(body.fmt)
        if not data:
            raise HTTPException(status_code=422, detail="That format could not be produced for this model.")
        ext = "zip" if body.fmt == "package" else body.fmt
        return Response(content=data, media_type=MEDIA[body.fmt], headers={"Content-Disposition": f'attachment; filename="model.{ext}"'})

    @router.get("/codegen/mesh/{token}")
    async def codegen_mesh(token: str, wallet: Wallet):
        data = meshes.get(_uid(wallet), token)
        if data is None:
            raise HTTPException(status_code=404, detail="That preview expired. Rebuild the model.")
        return Response(content=data, media_type="model/stl", headers={"Cache-Control": "private, max-age=120"})

    @router.get("/codegen")
    async def codegen_list(wallet: Wallet):
        rows = await asyncio.to_thread(store.list_for_user, _uid(wallet), 50)
        return {"models": [{"id": r["id"], "parent_id": r["parent_id"], "prompt": r["prompt"], "summary": r["summary"],
                            "created_at": r["created_at"], "has_library": bool(r["library"])} for r in rows]}

    @router.get("/codegen/{cid}/code")
    async def codegen_code(cid: str, wallet: Wallet):
        return Response(content=owned(_uid(wallet), cid)["code"], media_type="text/plain; charset=utf-8")

    @router.delete("/codegen/{cid}", status_code=204)
    async def codegen_delete(cid: str, wallet: Wallet):
        if not await asyncio.to_thread(store.delete, _uid(wallet), cid):
            raise HTTPException(status_code=404, detail="Model not found.")
        return Response(status_code=204)

    from types import SimpleNamespace
    global _SERVICE
    _SERVICE = SimpleNamespace(generate=generate_and_save, owned=owned, build=build_current)
    return router
