"""
Upload and analysis routes for CAD Studio. Included from web_app.py with

    app.include_router(file_api.build_router(limiter=limiter))

  GET  /files/capabilities   what this server can read (CAD, PDF text, images) and the size limit
  POST /files/analyze        multipart upload of one file -> what it contains

Signed-in accounts only, rate limited, and a per-account daily cap. No credit is
charged for analysis. Files are held in memory (or a temp file for the CAD reader),
read, and deleted. Nothing is stored.
"""

import base64
import importlib.util
import json
import os
import tempfile
import time
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

import chat_store
import file_analysis as fa
import image_import
import import_store
import meshes
import profiles
import raster_trace
import wallet_auth
from config import settings
from logging_config import get_logger

logger = get_logger(__name__)
Wallet = Annotated[dict, Depends(wallet_auth.get_current_wallet)]

_daily: dict[str, tuple[str, int]] = {}      # account -> (day, count). Resets on restart, which is fine for a soft cap.


def _size(n: int) -> str:
    return f"{max(1, round(n / 1024))} KB" if n < 1048576 else f"{n / 1048576:.1f} MB"


def _have(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _vision_ready() -> bool:
    return bool(settings.VISION_API_KEY and settings.VISION_MODEL)


def _count_use(account: str) -> None:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    d, n = _daily.get(account, (day, 0))
    if d != day:
        n = 0
    if n >= settings.FILE_DAILY_LIMIT_PER_ACCOUNT:
        raise HTTPException(status_code=429, detail="You have reached today's file analysis limit. It resets tomorrow (UTC).")
    _daily[account] = (day, n + 1)


def _vision(images: list[tuple[bytes, str]], context: str) -> dict:
    return fa.vision_extract(
        images, context, api_key=settings.VISION_API_KEY, base_url=settings.VISION_BASE_URL,
        model=settings.VISION_MODEL, timeout=settings.VISION_TIMEOUT_SECONDS)


def _keep(account: str, kind: str, filename: str, data: bytes, meta: dict, warnings: list[str]) -> str | None:
    """Store a file so it can be edited. A full account is a warning, not a failure: the file
    can still be read, it just cannot be edited until room is made."""
    try:
        return import_store.save(account, kind, filename, data, meta)
    except import_store.ImportLimitError as exc:
        warnings.append(str(exc) + ". The file was read, but it cannot be edited yet.")
        return None


def _analyze_cad(data: bytes, kind: str, filename: str, account: str) -> dict:
    suffix = {"step": ".step", "iges": ".igs", "brep": ".brep"}[kind]
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        result = fa.run_isolated(fa.analyze_cad_file, (path, kind), settings.FILE_ANALYSIS_TIMEOUT_SECONDS)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    stl = result.pop("stl", None)
    brep = result.pop("brep", None)
    warnings = ["Sizes are the part's axis-aligned bounding box in millimetres. A part drawn at an angle reads larger than its design size."]
    if stl is None:
        warnings.append("The model is too detailed to preview here.")
    import_id = None
    if brep:
        import_id = _keep(account, kind, filename, brep, {}, warnings)
    else:
        warnings.append("This file has surfaces but no solid, so it can be viewed but not edited.")
    return {
        "analysis": result,
        "description": fa.cad_description(result),
        "stl_base64": base64.b64encode(stl).decode() if stl else None,
        "import_id": import_id,
        "warnings": warnings,
    }


def _analyze_drawing(data: bytes, kind: str, filename: str, account: str) -> dict:
    """DXF, SVG, KiCad board, Gerber or a zip of Gerber and drill files: flat shapes that become
    a solid when extruded."""
    try:
        prof = profiles.parse_profile_file(kind, data, filename)
        stats = prof.stats()
    except profiles.ProfileError as exc:
        raise fa.FileRejected(str(exc)) from exc
    thickness = prof.suggested_thickness_mm
    warnings = list(prof.warnings)
    stored_kind = "gerber" if kind in ("gerber", "gerber_zip") else kind
    import_id = _keep(account, stored_kind, filename, json.dumps(prof.to_json()).encode(), {"suggested_thickness_mm": thickness}, warnings)
    return {
        "analysis": {**stats, "thickness_mm": thickness},
        "description": fa.drawing_description(stats, thickness),
        "stl_base64": None, "import_id": import_id, "warnings": warnings,
    }


def _analyze_mesh(data: bytes, kind: str, filename: str, account: str) -> dict:
    try:
        tris = meshes.parse_stl(data) if kind == "stl" else meshes.parse_obj(data)
        stats = meshes.stats(tris)
    except meshes.MeshError as exc:
        raise fa.FileRejected(str(exc)) from exc
    warnings = ["A mesh is a pile of triangles, so it can be scaled, rotated, moved and mirrored, but not given holes or fillets. "
                "It exports as STL only."]
    if not stats["watertight"]:
        warnings.append("The mesh has gaps (it is not closed), so no volume is given.")
    import_id = _keep(account, kind, filename, meshes.write_stl(tris), {}, warnings)
    return {"analysis": stats, "description": fa.mesh_description(stats), "stl_base64": None,
            "import_id": import_id, "warnings": warnings}


def _analyze_pdf(data: bytes, filename: str = "", account: str = "", trace_opts: dict | None = None) -> dict:
    text = fa.pdf_text(data)
    out: dict = {"text_excerpt": text[:1500], "numbers_found": fa.numbers_with_units(text), "vision": None}
    warnings: list[str] = []
    description = None
    if _vision_ready():
        try:
            pages = fa.pdf_page_images(data)
            v = _vision([(p, "image/jpeg") for p in pages], text)
            out["vision"] = v
            description = v["description"] or None
        except fa.FileRejected:
            raise
        except Exception as e:                       # noqa: BLE001
            logger.warning("vision call failed on a PDF: %s", type(e).__name__)
            warnings.append("The drawing reader did not answer, so only the text and numbers found in the PDF are shown.")
    else:
        warnings.append("Reading drawings as pictures is not switched on, so only the PDF's own text is shown.")
    if not text.strip() and out["vision"] is None and trace_opts is None:
        warnings.append("This PDF has no text, it is probably a scan. It needs the picture reader to be read.")
    result = {"analysis": out, "description": description, "stl_base64": None, "warnings": warnings}
    if trace_opts is not None:                       # asked for: trace the first page into a shape
        def keep(kind: str, payload: bytes, meta: dict, w: list[str]) -> str | None:
            return _keep(account, kind, filename, payload, meta, w)
        got = image_import.trace_pdf(data, options=trace_opts, keep=keep)
        out.update(got["analysis"])
        result.update(description=got["description"], import_id=got["import_id"])
        warnings.extend(got["warnings"])
    return result


def _analyze_image(data: bytes, filename: str, account: str, opts: dict) -> dict:
    """A picture or scanned drawing: traced into an editable flat shape, and read by the vision model if one is on."""
    def keep(kind: str, payload: bytes, meta: dict, warnings: list[str]) -> str | None:
        return _keep(account, kind, filename, payload, meta, warnings)
    try:
        return image_import.analyze_image(data, options=opts, vision=_vision if _vision_ready() else None, keep=keep)
    except image_import.ReaderUnavailable:
        raise HTTPException(status_code=503, detail="Reading images is not switched on for this server yet.") from None
    except image_import.ReaderFailed:
        logger.warning("vision call failed on an image")
        raise HTTPException(status_code=502, detail="The image reader did not answer. Try again in a moment.") from None


def build_router(*, limiter) -> APIRouter:
    router = APIRouter(prefix="/files", tags=["files"])
    max_bytes = settings.FILE_MAX_UPLOAD_MB * 1024 * 1024

    @router.get("/capabilities")
    @limiter.limit(settings.RATE_LIMIT_FILES)
    async def capabilities(request: Request, wallet: Wallet):
        return {"max_upload_mb": settings.FILE_MAX_UPLOAD_MB,
                "cad_files": _have("cadquery"), "pdf_text": _have("pypdf"),
                "pdf_pages_as_images": _have("pypdfium2") and _vision_ready(),
                "images": _vision_ready() and _have("PIL"),
                "image_tracing": raster_trace.available(),
                "pdf_tracing": raster_trace.available() and _have("pypdfium2"),
                "editable": ["step", "stp", "iges", "igs", "brep", "dxf", "svg", "kicad_pcb", "gbr", "zip", "stl", "obj"]
                            + (["png", "jpg", "jpeg", "webp"] if raster_trace.available() else []),
                "accepted": ["step", "stp", "iges", "igs", "brep", "dxf", "svg", "kicad_pcb", "gbr", "gtl", "gbl", "gko", "gm1", "drl", "zip", "stl", "obj", "pdf", "png", "jpg", "jpeg", "webp"]}

    @router.post("/analyze")
    @limiter.limit(settings.RATE_LIMIT_FILES)
    async def analyze(request: Request, wallet: Wallet, file: UploadFile = File(...), chat_id: str | None = Form(None),
                      scale_mm_per_px: float | None = Form(None, gt=0, le=1e6),
                      overall_width_mm: float | None = Form(None, gt=0, le=1e6),
                      overall_height_mm: float | None = Form(None, gt=0, le=1e6),
                      trace_mode: str = Form("auto", pattern="^(auto|silhouette|outline)$"),
                      largest_only: bool = Form(False), trace_pdf: bool = Form(False)):
        account = wallet["wallet_address"].lower()
        if chat_id is not None and chat_store.get_chat(account, chat_id) is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        data = bytearray()
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise HTTPException(status_code=413, detail=f"That file is over {settings.FILE_MAX_UPLOAD_MB} MB.")
        data = bytes(data)
        name = os.path.basename(file.filename or "")[:120]
        trace_opts = {"mm_per_px": scale_mm_per_px, "overall_width_mm": overall_width_mm,
                      "overall_height_mm": overall_height_mm, "mode": trace_mode, "largest_only": largest_only}
        try:
            kind = fa.sniff_kind(name, data)
            _count_use(account)
            if kind in fa.CAD_KINDS:
                if not _have("cadquery"):
                    raise HTTPException(status_code=503, detail="Reading CAD files is not available on this server.")
                result = await run_in_threadpool(_analyze_cad, data, kind, name, account)
            elif kind in fa.DRAWING_KINDS:
                result = await run_in_threadpool(_analyze_drawing, data, kind, name, account)
            elif kind in fa.MESH_KINDS:
                result = await run_in_threadpool(_analyze_mesh, data, kind, name, account)
            elif kind == "pdf":
                if not _have("pypdf"):
                    raise HTTPException(status_code=503, detail="Reading PDFs is not available on this server.")
                result = await run_in_threadpool(_analyze_pdf, data, name, account, trace_opts if trace_pdf else None)
            else:
                result = await run_in_threadpool(_analyze_image, data, name, account, trace_opts)
        except fa.FileRejected as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        card = fa.card_from_result(name, kind, len(data), result)
        out = {"kind": kind, "filename": name, "size_bytes": len(data), "card": card,
               "import_id": result.get("import_id"), **result}
        if chat_id is not None:
            # Saved in the chat so the card is still there after a reload. The assistant line holds
            # only neutral text: what the file says about itself is shown on the card, not fed to the AI.
            stored = {**card, "has_preview": False}
            plain = f"Read {name}." + (" It can be edited." if card["editable"] else "")
            user_msg = chat_store.add_message(account, chat_id, "user", f"Attached {name} ({_size(len(data))})",
                                              max_messages=settings.CHAT_MAX_MESSAGES_PER_CHAT)
            chat_store.set_title_if_default(account, chat_id, f"File: {name[:34]}")
            assistant_msg = chat_store.add_message(account, chat_id, "assistant", plain, meta={"file_card": stored},
                                                   max_messages=settings.CHAT_MAX_MESSAGES_PER_CHAT + 1)
            out["messages"] = {"user": user_msg, "assistant": assistant_msg}
        return out

    return router
