"""
HTTP routes for CAD Studio chats. Included from web_app.py with

    app.include_router(chat_api.build_router(generator=generator, limiter=limiter))

Everything is behind the wallet/Google session (wallet_auth.get_current_wallet) and every
query is scoped to that account in chat_store, so one account can never read, write or
delete another's chat.

  GET    /chat-config                    what things cost (the UI shows this)
  GET    /chats                          your chats, newest first
  POST   /chats                          start an empty chat
  GET    /chats/{id}                     the full chat: messages, part versions, anchor state
  PATCH  /chats/{id}                     rename
  DELETE /chats/{id}                     delete (soft)
  POST   /chats/{id}/messages            send a message: plan -> free preview -> reply (never charges)
  POST   /chats/{id}/turns               the same turn as a saved background job (see build_jobs.py)
  GET    /jobs/{id}  POST /jobs/{id}/cancel   follow or stop a background build
  POST   /chats/{id}/parts/{part}/generate   pay once for that version and unlock its full files
  POST   /chats/{id}/parts/{part}/check      manufacturability check for one process (paid once per version and process)
  GET    /templates                          the template picker's catalogue
  POST   /chats/{id}/templates/{template}    start a free preview from a template
  POST   /chats/import                   one-time upload of chats saved in the browser

/generate itself is unchanged and stays for API and MCP callers that send one finished
description. The studio talks to POST /chats/{id}/messages instead.
"""

import asyncio
import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import ai_models
import build_jobs
import chat_service
import chat_store
import vehicle_chat
import conversation
import db
import requirements as reqmod
import studio_templates
import wallet_auth
from config import settings
from logging_config import get_logger

logger = get_logger(__name__)

Wallet = Annotated[dict, Depends(wallet_auth.get_current_wallet)]


class CreateChatBody(BaseModel):
    title: str | None = Field(default=None, max_length=chat_store.MAX_TITLE_CHARS)


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=chat_store.MAX_TITLE_CHARS)


class MessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=settings.CHAT_MAX_MESSAGE_CHARS)
    # Which AI plans this message, for example "plus:gpt". Missing means Basic.
    model: str | None = Field(default=None, max_length=40)


class RequirementBody(BaseModel):
    text: str = Field(min_length=3, max_length=300)


class CheckBody(BaseModel):
    process: str = Field(min_length=1, max_length=40)


class ImportBody(BaseModel):
    chats: list[dict[str, Any]] = Field(max_length=50)


def _uid(wallet: dict) -> str:
    return wallet["wallet_address"].lower()


def _with_anchor_state(chat: dict, user_id: str) -> dict:
    """Parts remember their job; whether that job is anchored, and whether its full files are
    unlocked (paid), lives on the job row."""
    for p in chat["parts"]:
        p["anchor_tx"] = None
        p["paid"] = True
        if p.get("job_id"):
            job = db.get_job(p["job_id"])
            if job and (job.get("user_id") or "").lower() == user_id:
                p["anchor_tx"] = job.get("anchor_tx")
                p["paid"] = not db.is_job_locked(job)
                p["ai_tier"] = job.get("ai_tier") or "basic"
                p["generate_credits"] = ai_models.generate_credits(settings, job.get("ai_tier"))
    return chat


def _with_requirements(chat: dict, user_id: str) -> dict:
    """Every active requirement, and how each version of the part stands against them."""
    reqs = chat_store.list_requirements(user_id, chat["id"])
    chat["requirements"] = reqs
    for p in chat["parts"]:
        p["requirement_results"] = reqmod.evaluate(reqs, p.get("facts"))
    return chat


def _with_checks(chat: dict, user_id: str) -> dict:
    """Each part carries the manufacturability reports already paid for, {process: report}."""
    saved = chat_store.get_checks(user_id, chat["id"])
    for p in chat["parts"]:
        p["checks"] = saved.get(p["id"], {})
    return chat


def _codegen_bridge(model, loop):
    """Sync callables for chat_service.Deps over the code builder, or None when it is not mounted."""
    try:
        import codegen_api
        import codegen_chat
    except Exception:  # noqa: BLE001 - the chat works without the code builder
        return None
    service = codegen_api.get_service()
    return codegen_chat.Bridge(service, getattr(model, "id", None), loop) if service else None


def _part_mesh_from(generator):
    """(job_id) -> (vertices, triangles) read from the job's STL, or None. Rebuilds the STL if the
    instance no longer has it."""
    def read(job_id: str):
        import mesh_formats
        path, _ = generator.export_format_for_job(job_id, "stl", anchor=False)
        return mesh_formats.read_stl(path.read_bytes())
    return read


def build_router(*, generator, limiter) -> APIRouter:
    router = APIRouter(tags=["chats"])

    def make_deps(model: ai_models.ModelSpec | None = None, loop: Any = None) -> chat_service.Deps:
        spec_model = model or ai_models.find(settings, None)
        tier = spec_model.tier

        def plan(history, spec, facts=None):
            llm = ai_models.make_llm(spec_model, settings)
            try:
                from smart_parser import parse_description as _parse
            except Exception:  # noqa: BLE001
                _parse = None

            def regex_parse(text):
                parsed = _parse(text)
                if getattr(parsed, "part_type", None) == "assembly":
                    # Read the parts out of the description. Fewer than two parts is a PlanError,
                    # which the planner shows as a plain reply.
                    import assembly as assembly_mod
                    try:
                        parts = assembly_mod.parse_assembly_description(text)
                    except ValueError as exc:
                        raise conversation.PlanError(str(exc)) from exc
                    parsed.parameters = {"parts": [dict(p, count=1) for p in parts], "layout": "row"}
                return parsed
            if _parse is None:
                regex_parse = None
            import vehicle_chat
            with vehicle_chat.vision_scope(ai_models.make_vision(spec_model, settings)):
                return conversation.plan_turn(history, spec, llm=llm, regex_parse=regex_parse, facts=facts)

        def generate(description, spec, user_id, source):
            from smart_parser import ParsedParameters

            parsed = ParsedParameters(
                part_type=spec["part_type"], parameters=spec["parameters"], material=spec.get("material"),
                operations=spec.get("operations") or [], confidence=1.0,
                parser=spec_model.provider if source == "llm" else "regex_fallback",
                model=ai_models.model_name(spec_model, settings) if source == "llm" else None,
                prompt_version="chat-v1", parse_hash=chat_service.spec_hash(spec),
            )
            # draft=True: a free preview. Its full files stay locked until the owner pays.
            # ai_tier remembers what planned it, which sets what Generate costs. If the chosen AI
            # failed and the built-in rules took over, it is priced as Engine.
            return generator.generate_from_text(
                description, user_id=user_id, formats=["stl"], endpoint="paid", preparsed=parsed,
                draft=True, ai_tier=ai_models.pricing_tier(spec_model, source),
            )

        def ai_allowed(user_id):
            limit = ai_models.messages_per_day(settings, tier, db.get_plan(user_id))
            return limit is None or db.count_ai_usage_last_24h(user_id, tier) < limit

        def ai_used(user_id):
            db.log_ai_usage(user_id, tier, spec_model.id)

        def job_cost(job_id):
            job = db.get_job(job_id)
            return ai_models.generate_credits(settings, (job or {}).get("ai_tier"))

        def charge_job(job_id, user_id):
            job = db.get_job(job_id) or {}
            return db.charge_job(job_id, user_id, cost=job_cost(job_id), ai_tier=job.get("ai_tier"))

        def build_files(job_id):
            generator.export_format_for_job(job_id, "step", anchor=False)

        bridge = _codegen_bridge(spec_model, loop) if tier != "engine" else None
        return chat_service.Deps(
            plan=plan,
            generate=generate,
            balance=lambda uid: db.get_spendable_credits(uid, tier),
            ai_allowed=ai_allowed,
            ai_used=ai_used,
            answer_cost=int(getattr(settings, "CHAT_ANSWER_CREDITS", 1)),
            uses_ai=tier != "engine",
            job_cost=job_cost,
            charge_job=charge_job,
            refund_job=db.refund_job_charge,
            build_files=build_files,
            part_mesh=_part_mesh_from(generator),
            codegen_start=bridge.start if bridge else None,
            codegen_revise=bridge.revise if bridge else None,
            codegen_mesh=bridge.mesh if bridge else None,
            check_cost=int(settings.CHECK_CREDITS),
            charge_credits=lambda uid, amount: db.charge_credits(uid, amount, ai_tier=tier),
            refund_credits=lambda uid, pool, amount: db.refund_credits(uid, pool, amount),
            history_limit=settings.CHAT_MAX_HISTORY_MESSAGES,
            max_chats=settings.CHAT_MAX_CHATS_PER_USER,
            max_messages=settings.CHAT_MAX_MESSAGES_PER_CHAT,
        )

    @router.get("/chat-config")
    async def chat_config():
        # Previews and edits are free. An AI answer to a question costs answer_credits. Pressing Generate costs
        # generate_credits, which depends on the AI tier. There is no daily message or generation limit.
        return {
            "background_builds": bool(settings.BACKGROUND_BUILDS),"preview_credits": 0, "build_credits": 0, "edit_credits": 0,
                "generate_credits": {t: ai_models.generate_credits(settings, t) for t in ai_models.TIERS},
                "check_credits": int(settings.CHECK_CREDITS),
                "max_message_chars": settings.CHAT_MAX_MESSAGE_CHARS}

    @router.get("/ai/models")
    async def ai_model_list(wallet: Wallet):
        """The AI choices for the picker, with this account's remaining messages on each tier."""
        uid = _uid(wallet)
        plan = await run_in_threadpool(db.get_plan, uid)
        tiers = []
        for t in ai_models.TIERS:
            limit = ai_models.messages_per_day(settings, t, plan)
            used = 0 if limit is None else await run_in_threadpool(db.count_ai_usage_last_24h, uid, t)
            tiers.append({
                "id": t, "label": ai_models.tier_label(t), "messages_per_day": limit,
                "messages_left": None if limit is None else max(0, limit - used),
                "generate_credits": ai_models.generate_credits(settings, t),
                "models": [{"id": m.id, "label": m.label, "available": m.available,
                            "class": ai_models.model_class(m),
                            "class_label": ai_models.CLASS_LABELS[ai_models.model_class(m)],
                            "locked": not ai_models.allowed_for_plan(plan, m),
                            "requires_plan": ai_models.required_plan(m)}
                           for m in ai_models.catalogue(settings) if m.tier == t],
            })
        return {"plan": plan, "default": ai_models.DEFAULT_MODEL_ID, "tiers": tiers, "check_credits": int(settings.CHECK_CREDITS)}

    @router.get("/chats")
    async def list_chats(wallet: Wallet):
        return {"chats": await run_in_threadpool(chat_store.list_chats, _uid(wallet))}

    def chat_limit(uid: str) -> int:
        """Subscribers get no practical chat limit. Free accounts keep the normal one."""
        paid = db.get_plan(uid) != "free"
        return settings.CHAT_MAX_CHATS_PAID if paid else settings.CHAT_MAX_CHATS_PER_USER

    @router.post("/chats", status_code=201)
    async def create_chat(body: CreateChatBody, wallet: Wallet):
        uid = _uid(wallet)
        try:
            limit = await run_in_threadpool(chat_limit, uid)
            return await run_in_threadpool(chat_store.create_chat, uid, body.title, max_chats=limit)
        except chat_store.ChatLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

    @router.post("/chats/import")
    async def import_chats(body: ImportBody, wallet: Wallet):
        uid = _uid(wallet)

        def owns_job(job_id: str) -> bool:
            job = db.get_job(job_id)
            return bool(job) and (job.get("user_id") or "").lower() == uid

        limit = await run_in_threadpool(chat_limit, uid)

        def run() -> list[str]:
            ids = []
            for c in body.chats:
                try:
                    ids.append(chat_store.import_chat(uid, c, owns_job=owns_job, max_chats=limit))
                except chat_store.ChatLimitError:
                    break
            return ids

        ids = await run_in_threadpool(run)
        return {"imported": len(ids), "chat_ids": ids}

    @router.get("/chats/{chat_id}")
    async def get_chat(chat_id: str, wallet: Wallet):
        uid = _uid(wallet)
        chat = await run_in_threadpool(chat_store.get_chat, uid, chat_id)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        chat = await run_in_threadpool(_with_anchor_state, chat, uid)
        chat = await run_in_threadpool(_with_requirements, chat, uid)
        chat = await run_in_threadpool(_with_checks, chat, uid)
        active = await run_in_threadpool(build_jobs.active_for_chat, uid, chat_id)
        chat["active_job"] = build_jobs.public(active) if active else None
        return chat

    @router.post("/chats/{chat_id}/requirements", status_code=201)
    async def add_requirement(chat_id: str, body: RequirementBody, wallet: Wallet):
        """Add requirements by plain sentence, for example "must fit in 100 x 80 x 60 mm"."""
        uid = _uid(wallet)
        parsed = reqmod.parse_requirements(body.text)
        if not parsed:
            raise HTTPException(status_code=422, detail=(
                "I could not read a requirement in that. Try: must fit in 100 x 80 x 60 mm, keep it under 200 g, "
                "walls at least 2 mm, no larger than 120 mm, or made of 6061 aluminum."))
        try:
            added = await run_in_threadpool(chat_store.add_requirements, uid, chat_id, parsed)
        except LookupError:
            raise HTTPException(status_code=404, detail="Chat not found")
        except chat_store.ChatLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        part = await run_in_threadpool(chat_store.latest_part, uid, chat_id)
        results = reqmod.evaluate(added, part["facts"]) if part else []
        return {"added": added, "results": results}

    @router.delete("/chats/{chat_id}/requirements/{requirement_id}")
    async def remove_requirement(chat_id: str, requirement_id: str, wallet: Wallet):
        if not await run_in_threadpool(chat_store.remove_requirement, _uid(wallet), chat_id, requirement_id):
            raise HTTPException(status_code=404, detail="Requirement not found")
        return {"success": True}

    @router.patch("/chats/{chat_id}")
    async def rename_chat(chat_id: str, body: RenameBody, wallet: Wallet):
        if not await run_in_threadpool(chat_store.rename_chat, _uid(wallet), chat_id, body.title):
            raise HTTPException(status_code=404, detail="Chat not found")
        return {"success": True}

    @router.delete("/chats/{chat_id}")
    async def delete_chat(chat_id: str, wallet: Wallet):
        if not await run_in_threadpool(chat_store.delete_chat, _uid(wallet), chat_id):
            raise HTTPException(status_code=404, detail="Chat not found")
        return {"success": True}

    async def _validated_model(uid: str, body: MessageBody):
        """The AI the person picked, or the HTTP error that says why they cannot use it."""
        model = ai_models.find(settings, body.model)
        if model is None or not model.available:
            raise HTTPException(status_code=400, detail="That AI is not available yet. Pick another one.")
        plan = await run_in_threadpool(db.get_plan, uid)
        if not ai_models.allowed_for_plan(plan, model):
            raise HTTPException(
                status_code=403,
                detail=f"{model.label} is part of the {ai_models.required_plan(model).title()} plan. "
                       "Pick DeepSeek or upgrade to use it.",
            )
        return model

    def _finalise_out(out: dict, model) -> dict:
        if out.get("part"):
            out["part"]["anchor_tx"] = None
            # A template message builds a free preview. A code part was paid for when it was written.
            out["part"]["paid"] = out["part"].get("part_type") == "codegen"
            part_tier = ai_models.pricing_tier(model, "llm" if out.get("plan_source") == "llm" else "rules")
            out["part"]["ai_tier"] = part_tier
            out["part"]["generate_credits"] = ai_models.generate_credits(settings, part_tier)
        return out

    @router.post("/chats/{chat_id}/messages")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def send_message(request: Request, chat_id: str, body: MessageBody, wallet: Wallet):
        uid = _uid(wallet)
        text = body.content.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Message must not be empty")
        model = await _validated_model(uid, body)
        try:
            out = await run_in_threadpool(chat_service.run_turn, make_deps(model, asyncio.get_running_loop()), uid, chat_id, text)
        except chat_service.ChatNotFound:
            raise HTTPException(status_code=404, detail="Chat not found")
        except chat_service.ChatBusy:
            raise HTTPException(status_code=409, detail="This chat is still working on your last message.")
        except chat_store.ChatLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return _finalise_out(out, model)

    # ------------------------------------------------------------ background builds
    # POST /chats/{id}/turns   the same turn as /messages, but saved as a job. A quick answer comes back at once, exactly
    #                          like /messages; a long one answers 202 with the job, and the browser follows it.
    # GET  /jobs/{id}          where it is: status, progress lines and, when done, the same fields /messages returns.
    # POST /jobs/{id}/cancel   stop it at its next step.
    # Leaving the page does not stop a job. Coming back, GET /chats/{id} carries `active_job` so the browser rejoins it.
    runner = build_jobs.Runner(settings.BACKGROUND_MAX_WORKERS)

    def _job_payload(job: dict) -> dict:
        pub = build_jobs.public(job)
        return {**(job.get("result") or {}), "job": pub} if job["status"] == "done" else {"job": pub}

    def _submit_job(job: dict, model, loop) -> None:
        def work(attempt: int) -> dict:
            try:
                out = chat_service.run_turn(make_deps(model, loop), job["user_id"], job["chat_id"], job["text"], resume=attempt > 1)
            except chat_service.ChatBusy:
                raise RuntimeError("This chat was busy with another message. Send it again in a moment.")
            except chat_service.ChatNotFound:
                raise RuntimeError("This chat no longer exists.")
            return _finalise_out(out, model)

        def run() -> None:
            build_jobs.execute(job["id"], work, plan_budget_s=settings.BACKGROUND_PLAN_BUDGET_S,
                               max_runtime_s=settings.BACKGROUND_JOB_MAX_S)
        runner.submit(job["id"], run)

    def _resubmit(loop, jobs) -> None:
        for job in jobs:
            model = ai_models.find(settings, job.get("model_id"))
            if model is None or not model.available:
                model = ai_models.find(settings, None)
            _submit_job(job, model, loop)

    def resume_jobs(loop) -> int:
        """At startup: delete old finished jobs and start a sweeper that runs again any build whose process died. The
        sweeper waits for a job's heartbeat to go quiet (build_jobs.STALE_S), so a build another process is still
        running is left alone."""
        try:
            build_jobs.purge_old()
        except Exception:  # noqa: BLE001 - jobs are an extra; the app must start without them
            import logging
            logging.getLogger(__name__).exception("could not purge old background builds")

        def sweep() -> None:
            import logging
            import time as _t
            while True:
                _t.sleep(30)
                try:
                    _resubmit(loop, build_jobs.requeue_stale(skip_ids=runner.live_ids()))
                except Exception:  # noqa: BLE001
                    logging.getLogger(__name__).exception("background build sweep failed")
        threading.Thread(target=sweep, name="build-sweeper", daemon=True).start()
        return 0
    router.resume_jobs = resume_jobs

    @router.post("/chats/{chat_id}/turns")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def start_turn(request: Request, chat_id: str, body: MessageBody, wallet: Wallet):
        uid = _uid(wallet)
        text = body.content.strip()
        if not text:
            raise HTTPException(status_code=422, detail="Message must not be empty")
        if not settings.BACKGROUND_BUILDS:
            return await send_message(request, chat_id, body, wallet)
        model = await _validated_model(uid, body)
        if await run_in_threadpool(chat_store.get_chat, uid, chat_id) is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        try:
            job = await run_in_threadpool(build_jobs.create, uid, chat_id, text, getattr(model, "id", None),
                                          max_active_per_user=settings.BACKGROUND_MAX_ACTIVE_PER_USER)
        except build_jobs.JobActive as busy:
            return JSONResponse(status_code=409, content={"detail": "This chat is still working on your last message.", "job": busy.job})
        except build_jobs.JobLimit as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        _submit_job(job, model, asyncio.get_running_loop())
        await run_in_threadpool(runner.wait, job["id"], settings.BACKGROUND_INLINE_WAIT_S)
        latest = await run_in_threadpool(build_jobs.get, uid, job["id"]) or job
        return JSONResponse(status_code=200 if latest["status"] in build_jobs.DONE else 202, content=jsonable_encoder(_job_payload(latest)))

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, wallet: Wallet):
        job = await run_in_threadpool(build_jobs.get, _uid(wallet), job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return JSONResponse(content=jsonable_encoder(_job_payload(job)))

    @router.post("/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, wallet: Wallet):
        job = await run_in_threadpool(build_jobs.request_cancel, _uid(wallet), job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return JSONResponse(content=jsonable_encoder(_job_payload(job)))

    @router.post("/chats/{chat_id}/imports/{import_id}/part")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def start_import(request: Request, chat_id: str, import_id: str, wallet: Wallet):
        """Open an attached file (already read by POST /files/analyze) as an editable part."""
        uid = _uid(wallet)
        try:
            out = await run_in_threadpool(chat_service.start_import_part, make_deps(), uid, chat_id, import_id)
        except chat_service.ChatNotFound:
            raise HTTPException(status_code=404, detail="Chat not found")
        except chat_service.ImportNotFound:
            raise HTTPException(status_code=404, detail="That file is no longer stored. Attach it again.")
        except chat_service.ChatBusy:
            raise HTTPException(status_code=409, detail="This chat is still working on your last message.")
        except chat_store.ChatLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        if out.get("part"):
            out["part"]["anchor_tx"] = None
            out["part"]["paid"] = False
            tier = "engine"
            out["part"]["ai_tier"] = tier
            out["part"]["generate_credits"] = ai_models.generate_credits(settings, tier)
        return out

    @router.get("/templates")
    async def template_list():
        """The catalogue for the template picker. Public: it holds no personal data."""
        return studio_templates.listing()

    @router.post("/chats/{chat_id}/templates/{template_id}")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def start_template(request: Request, chat_id: str, template_id: str, wallet: Wallet):
        """Start a part or assembly from a template. A free preview, built with no AI."""
        uid = _uid(wallet)
        try:
            out = await run_in_threadpool(chat_service.start_template_part, make_deps(ai_models.find(settings, "engine:rules")), uid, chat_id, template_id)
        except chat_service.ChatNotFound:
            raise HTTPException(status_code=404, detail="Chat not found")
        except chat_service.TemplateNotFound:
            raise HTTPException(status_code=404, detail="That template does not exist.")
        except chat_service.ChatBusy:
            raise HTTPException(status_code=409, detail="This chat is still working on your last message.")
        except chat_store.ChatLimitError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        if out.get("part"):
            out["part"]["anchor_tx"] = None
            out["part"]["paid"] = False
            out["part"]["ai_tier"] = "engine"
            out["part"]["generate_credits"] = ai_models.generate_credits(settings, "engine")
        return out

    @router.post("/chats/{chat_id}/parts/{part_id}/check")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def check_part(request: Request, chat_id: str, part_id: str, body: CheckBody, wallet: Wallet):
        """Manufacturability check for one process. Costs CHECK_CREDITS once per version and process,
        then the saved report reopens free. Runs no AI."""
        uid = _uid(wallet)
        try:
            return await run_in_threadpool(chat_service.run_check, make_deps(None, asyncio.get_running_loop()), uid, chat_id, part_id, body.process.strip())
        except (chat_service.ChatNotFound, chat_service.PartNotFound):
            raise HTTPException(status_code=404, detail="Part not found")
        except chat_service.CheckRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except chat_service.ChatBusy:
            raise HTTPException(status_code=409, detail="This check is already running.")
        except chat_service.NeedCredits as exc:
            raise HTTPException(
                status_code=402,
                detail="You have used your AI allowance for now. Subscribe or add a boost, then run the check again.",
            ) from exc
        except chat_service.CheckFailed as exc:
            logger.warning("check failed, not charged: %s", exc)
            raise HTTPException(status_code=500, detail=f"I could not run the check, so you were not charged. {exc}") from exc

    @router.post("/chats/{chat_id}/parts/{part_id}/generate")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def generate_part_files(request: Request, chat_id: str, part_id: str, wallet: Wallet,
                                  allow_partial: bool = False):
        """The one place a studio chat spends credits. Pays for this version once and unlocks
        its STEP, IGES, DXF and PDF files. Pressing it again for a paid version is free."""
        uid = _uid(wallet)
        try:
            out = await run_in_threadpool(chat_service.unlock_part, make_deps(), uid, chat_id, part_id, allow_partial)
            # Generating is the clearest sign someone liked a vehicle version: let the library learn from it.
            saved = await run_in_threadpool(chat_store.get_part, uid, chat_id, part_id)
            if saved:
                await run_in_threadpool(vehicle_chat.on_generated, saved.get("spec"))
            return out
        except (chat_service.ChatNotFound, chat_service.PartNotFound):
            raise HTTPException(status_code=404, detail="Part not found")
        except chat_service.StepsRemaining as exc:
            raise HTTPException(
                status_code=409,
                detail=(f"This is step {exc.shown} of {exc.of}, so the files would hold only part of the design. "
                        "Say finish in the chat to add everything, then press Generate. You were not charged."),
            ) from exc
        except chat_service.ChatBusy:
            raise HTTPException(status_code=409, detail="This part is already being generated.")
        except chat_service.NeedCredits as exc:
            raise HTTPException(
                status_code=402,
                detail="You have used your AI allowance for now. Subscribe or add a boost, then press Generate again.",
            ) from exc
        except chat_service.FilesFailed as exc:
            logger.warning("part files failed after charge, refunded: %s", exc)
            raise HTTPException(
                status_code=500, detail="I could not build the files, so you were not charged. Please try again."
            ) from exc

    return router
