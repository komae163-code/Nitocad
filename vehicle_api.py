"""
HTTP routes for a vehicle part in the studio. See vehicle_chat.py (planning) and vehicle_views.py (what is shown).

  GET /chats/{chat_id}/parts/{part_id}/vehicle            sizes, groups, design checks   (free)
  GET /chats/{chat_id}/parts/{part_id}/vehicle/exploded   viewer JSON for exploded.html   (free, needs the CAD kernel)
  GET /chats/{chat_id}/parts/{part_id}/vehicle/package    the zipped package             (only after Generate)

Same sign-in as the chat routes. Nothing here spends credits: the package is served only for a part whose files
Generate has already unlocked. A built package is kept for a few minutes (package_cache.py), so pressing download
twice does not build it twice. The payment check always runs first; the cache never decides who may download.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

import chat_store
import db
import package_cache
import vehicle_build as VB
import vehicle_chat
import vehicle_views as VV
import wallet_auth
from config import settings
from logging_config import get_logger

logger = get_logger(__name__)
Wallet = Annotated[dict, Depends(wallet_auth.get_current_wallet)]

_FETCH_EXTERNAL = True            # the package includes parts fetched from outside libraries (licence-gated, see vehicle_build)
_package_cache: package_cache.PackageCache | None = None


def package_cache_instance() -> package_cache.PackageCache:
    """Built vehicle packages, kept briefly so a repeat download does not rebuild them (package_cache.py)."""
    global _package_cache
    if _package_cache is None:
        _package_cache = package_cache.PackageCache(settings.VEHICLE_PACKAGE_CACHE_TTL_S,
                                                    settings.VEHICLE_PACKAGE_CACHE_MAX_ITEMS)
    return _package_cache


def _uid(wallet: dict) -> str:
    return wallet["wallet_address"].lower()


def _vehicle_part(uid: str, chat_id: str, part_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    part = chat_store.get_part(uid, chat_id, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="Part not found")
    v = vehicle_chat.vehicle_of(part["spec"])
    if v is None:
        raise HTTPException(status_code=422, detail="This part is not a vehicle.")
    return part, v


def build_router(*, limiter: Any = None) -> APIRouter:
    router = APIRouter(tags=["vehicles"])

    @router.get("/chats/{chat_id}/parts/{part_id}/vehicle")
    async def vehicle_summary(chat_id: str, part_id: str, wallet: Wallet):
        _part, v = await run_in_threadpool(_vehicle_part, _uid(wallet), chat_id, part_id)
        out = await run_in_threadpool(VV.summary, v)
        if not out["ok"]:
            raise HTTPException(status_code=422, detail="This vehicle's plan could not be read: " + "; ".join(out["errors"][:2]))
        return out

    @router.get("/chats/{chat_id}/parts/{part_id}/vehicle/exploded")
    async def vehicle_exploded(chat_id: str, part_id: str, wallet: Wallet, group: str | None = None):
        part, v = await run_in_threadpool(_vehicle_part, _uid(wallet), chat_id, part_id)
        bbox = VB.kernel_bbox_fn()
        if bbox is None:
            raise HTTPException(status_code=503, detail="The exploded view needs the CAD kernel, which is not available on this server.")
        parts = (part["spec"].get("parameters") or {}).get("parts") or []
        out = await run_in_threadpool(VV.exploded, v, parts, bbox, group=group)
        if not out["ok"]:
            raise HTTPException(status_code=422, detail="; ".join(out["errors"][:2]))
        return out

    def _package(v: dict[str, Any]) -> tuple[Path, Path]:
        work = Path(tempfile.mkdtemp(prefix="nito_pkg_"))
        try:
            res = VV.build_package(v, work, bbox_fn=VB.kernel_bbox_fn(), export_fn=VB.kernel_export_fn(),
                                   fetch=_FETCH_EXTERNAL)
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise
        if not res["ok"]:
            shutil.rmtree(work, ignore_errors=True)
            raise HTTPException(status_code=422, detail="; ".join(res["errors"][:2]) or "The package could not be built.")
        return work, Path(res["zip"])

    def _zip_bytes(v: dict[str, Any]) -> tuple[bytes, str]:
        """Build the package, read the zip into memory and remove the scratch folder at once."""
        work, zpath = _package(v)
        try:
            return zpath.read_bytes(), zpath.name
        finally:
            shutil.rmtree(work, ignore_errors=True)

    async def _serve_package(chat_id: str, part_id: str, wallet: dict):
        uid = _uid(wallet)
        part, v = await run_in_threadpool(_vehicle_part, uid, chat_id, part_id)
        job = await run_in_threadpool(db.get_job, part.get("job_id")) if part.get("job_id") else None
        if not job or (job.get("user_id") or "").lower() != uid:
            raise HTTPException(status_code=404, detail="Part not found")
        if db.is_job_locked(job):
            raise HTTPException(status_code=402, detail="Press Generate to unlock the package for this version.")
        data, name, _from_cache = await run_in_threadpool(
            package_cache.get_package, package_cache_instance(), v, _FETCH_EXTERNAL, lambda: _zip_bytes(v))
        return Response(content=data, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    if limiter is not None:
        @router.get("/chats/{chat_id}/parts/{part_id}/vehicle/package")
        @limiter.limit(settings.RATE_LIMIT_GENERATE)
        async def vehicle_package(request: Request, chat_id: str, part_id: str, wallet: Wallet):
            return await _serve_package(chat_id, part_id, wallet)
    else:
        @router.get("/chats/{chat_id}/parts/{part_id}/vehicle/package")
        async def vehicle_package(chat_id: str, part_id: str, wallet: Wallet):
            return await _serve_package(chat_id, part_id, wallet)

    return router
