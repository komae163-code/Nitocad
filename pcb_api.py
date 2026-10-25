"""
PCB routes. Included from web_app.py with

    app.include_router(pcb_api.build_router(generator=generator, limiter=limiter,
                                            make_token=_make_dl_token, verify_token=_verify_dl_token))

  GET  /pcb/blocks                       the circuit blocks the planner can use (public)
  POST /pcb/preview                      free: offline planner + built-in checks, an SVG of the board
  POST /pcb/jobs/{job_id}/package        build the manufacturing package for a paid Studio PCB part
  POST /pcb/jobs/{job_id}/link           a short-lived signed URL for one file of that package
  GET  /pcb/jobs/{job_id}/file           the file itself

A Studio PCB part is a normal job (part_type "pcb_board"), so it is charged, locked and unlocked by the
existing Generate flow. The package routes only add the electronics files, and refuse a job that has not
been paid for, exactly like the STEP / IGES / DXF / PDF exports.
"""

import tempfile
from pathlib import Path
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

import db
import pcb_service
import wallet_auth
from config import settings
from logging_config import get_logger

logger = get_logger(__name__)
Wallet = Annotated[dict, Depends(wallet_auth.get_current_wallet)]


class PreviewRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=2000)


class LinkBody(BaseModel):
    kind: str


def _owned_pcb_job(job_id: str, wallet: dict) -> dict:
    job = db.get_job(job_id)
    owner = (job or {}).get("user_id") or ""
    if job is None or owner.lower() != wallet["wallet_address"].lower() or job.get("part_type") != "pcb_board":
        raise HTTPException(status_code=404, detail="PCB job not found")
    if db.is_job_locked(job):
        raise HTTPException(status_code=402, detail="Press Generate on this part first to unlock its files.")
    return job


def build_router(*, generator, limiter, make_token: Callable, verify_token: Callable) -> APIRouter:
    router = APIRouter(prefix="/pcb", tags=["pcb"])

    @router.get("/blocks")
    async def blocks():
        from pcb.blocks import BLOCKS
        return {"blocks": [
            {"type": b.type, "title": b.title, "description": b.description, "params": b.params,
             "param_help": b.param_help, "review_required": b.review_required,
             "ports": {n: {"role": p.role} for n, p in b.ports.items()}}
            for b in BLOCKS.values()]}

    @router.post("/preview")
    @limiter.limit(settings.RATE_LIMIT_PREVIEW)
    async def preview(request: Request, body: PreviewRequest):
        """Free and unauthenticated, so it uses the offline planner only: no AI cost, no files stored."""
        from pcb.compose import SpecError
        from pcb.pipeline import build_board
        from pcb.planner import PlanError, keyword_plan

        def work():
            plan = keyword_plan(body.description)
            with tempfile.TemporaryDirectory() as tmp:
                report = build_board(plan.spec, Path(tmp), run_kicad=False)
                svg = (Path(tmp) / "preview.svg").read_text(encoding="utf-8")
            return plan, report, svg

        try:
            plan, report, svg = await run_in_threadpool(work)
        except (PlanError, SpecError) as exc:
            raise HTTPException(status_code=422, detail=list(exc.errors)) from exc
        keep = ("title", "ok", "routed", "board_mm", "components", "nets", "warnings", "review_items", "notes", "disclaimer")
        out = {k: report[k] for k in keep}
        out["notes"] = list(plan.notes) + out["notes"]
        return {"report": out, "svg": svg, "planner": plan.source}

    @router.post("/jobs/{job_id}/package")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def package(request: Request, job_id: str, wallet: Wallet):
        job = _owned_pcb_job(job_id, wallet)
        try:
            report = await run_in_threadpool(pcb_service.ensure_package, job, generator.output_dir)
        except pcb_service.PackageUnavailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"report": report, "files": report["files"]}

    @router.post("/jobs/{job_id}/link")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def link(request: Request, job_id: str, body: LinkBody, wallet: Wallet):
        job = _owned_pcb_job(job_id, wallet)
        try:
            report = await run_in_threadpool(pcb_service.ensure_package, job, generator.output_dir)
        except pcb_service.PackageUnavailable as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        path = pcb_service.file_for(job, generator.output_dir, body.kind)
        if path is None or body.kind not in report["files"]:
            if body.kind in ("gerbers", "pos", "step") and not (report.get("stages", {}).get("kicad", {}).get("available")):
                raise HTTPException(status_code=422, detail=(
                    "Gerber, placement and 3D files are made by KiCad, which is not installed on this server yet. "
                    "The KiCad project and the parts list are ready to download."))
            if body.kind in ("gerbers", "pos", "step"):
                raise HTTPException(status_code=422, detail=(
                    "KiCad could not produce that file for this board. Open the KiCad project and check the "
                    "design-rule results in the report."))
            raise HTTPException(status_code=404, detail="That file is not part of this package")
        token = make_token(job_id, f"pcb-{body.kind}", job["user_id"])
        return {"url": f"/pcb/jobs/{job_id}/file?kind={body.kind}&t={token}", "filename": path.name}

    @router.get("/jobs/{job_id}/file")
    @limiter.limit(settings.RATE_LIMIT_GENERATE)
    async def file(request: Request, job_id: str, kind: str = "", t: str = ""):
        from pcb.pipeline import FILE_KINDS
        job = db.get_job(job_id)
        # 404 for every failure, like the other download routes, so job ids cannot be probed.
        if (job is None or job.get("part_type") != "pcb_board" or kind not in FILE_KINDS
                or not verify_token(job_id, f"pcb-{kind}", job["user_id"], t)):
            raise HTTPException(status_code=404, detail="Link expired or invalid")
        path = pcb_service.file_for(job, generator.output_dir, kind)
        if path is None:
            raise HTTPException(status_code=404, detail="Link expired or invalid")
        return FileResponse(path, media_type=FILE_KINDS[kind][1], filename=path.name,
                            headers={"Cache-Control": "private, no-store"})

    return router
