"""
Reading an uploaded picture or scanned drawing: trace it into a flat shape the person can edit,
and, when a vision model is switched on, read the dimensions and notes from it as well.

The two readers answer different questions. Tracing (raster_trace.py) gives geometry: the outline,
the holes and their sizes in pixels, scaled to millimetres only by a number the person supplies.
The vision model (file_analysis.py) gives meaning: the printed dimensions, the part's name, what is
missing. A printed number is only ever offered as a suggestion for the size, never applied.

Pure Python apart from the readers it calls, and the vision model and the store are passed in, so it
is tested without a server. file_api.py is the thin HTTP wrapper around it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import file_analysis as fa
import profiles
import raster_trace

Vision = Callable[[list[tuple[bytes, str]], str], dict]
Keep = Callable[[str, bytes, dict, list[str]], "str | None"]

SIZE_WORDS = re.compile(r"overall|length|width|diameter|height", re.I)


class ReaderUnavailable(Exception):
    """Neither tracing nor the vision model can run on this server."""


class ReaderFailed(Exception):
    """The vision model was needed and did not answer."""


def clean_options(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only the tracing options we know, drop empty ones."""
    out: dict[str, Any] = {}
    for key in ("mm_per_px", "overall_width_mm", "overall_height_mm"):
        if raw.get(key) is not None:
            out[key] = raw[key]
    if raw.get("mode"):
        out["mode"] = raw["mode"]
    if raw.get("largest_only"):
        out["largest_only"] = True
    return out


def traced_payload(prof: profiles.Profile, rep: dict[str, Any], *, kind: str, keep: Keep,
                   warnings: list[str]) -> dict[str, Any]:
    """What the card needs from a traced shape, and the stored copy that makes it editable."""
    stats = prof.stats()
    thickness = prof.suggested_thickness_mm
    warnings.extend(prof.warnings)
    import_id = keep(kind, json.dumps(prof.to_json()).encode(), {"suggested_thickness_mm": thickness, "traced": True}, warnings)
    return {
        "analysis": {**stats, "thickness_mm": thickness, "trace": rep, "preview_svg": raster_trace.preview_svg(prof)},
        "description": fa.drawing_description(stats, thickness),
        "import_id": import_id,
    }


def scale_hint(vision_result: dict[str, Any]) -> str | None:
    """A printed overall dimension, offered as a suggestion for the size. Never applied."""
    for d in vision_result.get("dimensions") or []:
        if d.get("value_mm") and d.get("confidence") == "high" and SIZE_WORDS.search(str(d.get("label", ""))):
            v = float(d["value_mm"])
            return (f"The drawing shows \"{d['label']}\" as {v:g} mm. If that is the size of the whole part, "
                    f"say \"make it {v:g} mm long\" to set it. Check it against the drawing first")
    return None


def analyze_image(data: bytes, *, options: dict[str, Any], vision: Vision | None, keep: Keep,
                  tracing: bool | None = None) -> dict[str, Any]:
    """Trace the picture, read it with the vision model if one is given, and combine the two.

    Raises fa.FileRejected for a picture that cannot be used (the message is safe to show),
    ReaderUnavailable when nothing can read pictures, ReaderFailed when only the vision model could
    have read it and it did not answer."""
    can_trace = raster_trace.available() if tracing is None else tracing
    if not can_trace and vision is None:
        raise ReaderUnavailable()
    jpeg, media = fa.prepare_image(data)              # refuses anything that is not a real picture, before any reader runs
    warnings: list[str] = []
    analysis: dict[str, Any] = {}
    description: str | None = None
    import_id: str | None = None
    prof = rep = None

    if can_trace:
        try:
            prof, rep = raster_trace.trace_image(data, **clean_options(options))
        except raster_trace.TraceUnavailable:
            if vision is None:
                raise ReaderUnavailable() from None
        except profiles.ProfileError as exc:
            if vision is None:
                raise fa.FileRejected(str(exc)) from exc
            warnings.append(f"The picture could not be traced into a shape: {exc}. Only the drawing reader's notes are shown.")
    if prof is not None and rep is not None:
        got = traced_payload(prof, rep, kind="image", keep=keep, warnings=warnings)
        analysis.update(got["analysis"])
        description, import_id = got["description"], got["import_id"]

    if vision is not None:
        try:
            v = vision([(jpeg, media)], "")
        except fa.FileRejected:
            raise
        except Exception as exc:                      # noqa: BLE001 - a provider failure must not lose the traced shape
            if prof is None:
                raise ReaderFailed() from exc
            warnings.append("The drawing reader did not answer, so only the traced shape is shown.")
        else:
            analysis["vision"] = v
            description = description or v.get("description") or None
            if rep is not None and rep.get("needs_scale"):
                hint = scale_hint(v)
                if hint:
                    warnings.append(hint)
    if prof is None and not analysis.get("vision"):
        raise fa.FileRejected("Nothing could be read from that picture")
    if prof is None:
        warnings.append("Check every number against your drawing before you build. Photos without a scale give no reliable sizes.")
    return {"analysis": analysis, "description": description, "stl_base64": None, "import_id": import_id, "warnings": warnings}


def trace_pdf(data: bytes, *, options: dict[str, Any], keep: Keep) -> dict[str, Any]:
    """Trace the first page of a PDF drawing. Raises fa.FileRejected with a safe message when it cannot."""
    if not raster_trace.available():
        raise fa.FileRejected("Tracing drawings is not available on this server")
    warnings: list[str] = []
    try:
        prof, rep = raster_trace.trace_pdf_page(data, **clean_options(options))
    except raster_trace.TraceUnavailable as exc:
        raise fa.FileRejected(str(exc)) from exc
    except profiles.ProfileError as exc:
        raise fa.FileRejected(str(exc)) from exc
    got = traced_payload(prof, rep, kind="image", keep=keep, warnings=warnings)
    got["warnings"] = warnings
    return got
